"""Reprocess existing WhatsApp conversations after logic fixes."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models
from app.database import Base
from app.services.whatsapp import product_gates as g
from app.services.whatsapp import reprocess as wa_reprocess
from app.services.whatsapp import repository as repo

PSUR = "I will arrange the PSUR for Tramadol 50 mg."
FAST = "Please make it fast"
AGAIN = "Again"


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _contact(db, name: str) -> models.WhatsAppContact:
    c = models.WhatsAppContact(wa_id=f"9199{name[:4]}", profile_name=name)
    db.add(c)
    db.flush()
    return c


def _msg(db, contact, *, direction: str, body: str, when: datetime) -> models.WhatsAppMessage:
    m = models.WhatsAppMessage(
        contact_id=contact.id,
        direction=direction,
        body=body,
        timestamp=when,
        msg_type="text",
    )
    db.add(m)
    db.flush()
    return m


def test_dry_run_makes_no_database_writes():
    db = _session()
    contact = _contact(db, "Friday Sir")
    hurry = _msg(
        db,
        contact,
        direction="inbound",
        body=FAST,
        when=datetime.utcnow(),
    )
    bad = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=hurry.id,
        commitment_type="other",
        label="Make it fast",
        direction="owner",
        deadline_at=datetime(2026, 10, 15, 3, 30),
    )
    db.commit()
    before = db.query(models.WhatsAppCommitment).filter(
        models.WhatsAppCommitment.fulfilled_at.is_(None)
    ).count()
    summary = wa_reprocess.reprocess_contacts(
        db,
        contact_ids=[contact.id],
        dry_run=True,
        reanalyze_outbound=False,
    )
    after = db.query(models.WhatsAppCommitment).filter(
        models.WhatsAppCommitment.fulfilled_at.is_(None)
    ).count()
    assert summary.dry_run is True
    assert summary.commitments_invalidated == 1
    assert before == after == 1


def test_reconcile_removes_client_sourced_owner_commitment():
    db = _session()
    contact = _contact(db, "Friday Sir")
    start = datetime.utcnow() - timedelta(hours=2)
    promise = _msg(db, contact, direction="outbound", body=PSUR, when=start)
    hurry = _msg(db, contact, direction="inbound", body=FAST, when=start + timedelta(minutes=1))
    repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=hurry.id,
        commitment_type="other",
        label="Make it fast",
        direction="owner",
        deadline_at=datetime(2026, 10, 15, 3, 30),
    )
    db.commit()
    summary = wa_reprocess.reprocess_contacts(
        db,
        contact_ids=[contact.id],
        dry_run=False,
        reanalyze_outbound=False,
    )
    assert summary.commitments_invalidated == 1
    open_rows = repo.open_commitments_for_contact(db, contact.id, direction="owner")
    assert open_rows == []
    db.refresh(promise)


def test_unsupported_deadline_cleared_owner_promise_kept():
    db = _session()
    contact = _contact(db, "Friday Sir")
    start = datetime.utcnow() - timedelta(hours=2)
    promise = _msg(db, contact, direction="outbound", body=PSUR, when=start)
    row = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Arrange PSUR for Tramadol 50 mg",
        direction="owner",
        deadline_at=datetime(2026, 10, 15, 3, 30),
    )
    db.commit()
    summary = wa_reprocess.reprocess_contacts(
        db,
        contact_ids=[contact.id],
        dry_run=False,
        reanalyze_outbound=False,
    )
    assert summary.deadlines_cleared == 1
    assert summary.commitments_invalidated == 0
    db.refresh(row)
    assert row.fulfilled_at is None
    assert row.deadline_at is None
    assert row.message_id == promise.id


def test_lotus_again_client_commitment_invalidated_and_no_reply_draft():
    db = _session()
    contact = _contact(db, "Lotus")
    again = _msg(
        db,
        contact,
        direction="inbound",
        body=AGAIN,
        when=datetime.utcnow(),
    )
    repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=again.id,
        commitment_type="other",
        label="Again",
        direction="client",
        deadline_at=None,
    )
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=again.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="Sure, I'll follow up.",
        )
    )
    db.commit()
    summary = wa_reprocess.reprocess_contacts(
        db,
        contact_ids=[contact.id],
        dry_run=False,
        reanalyze_outbound=False,
    )
    assert summary.client_commitments_invalidated == 1
    assert summary.reply_drafts_cleared == 1
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .all()
    )
    assert pending == []


def test_friday_sir_outbound_reanalyze_creates_owner_commitment_once():
    db = _session()
    contact = _contact(db, "Friday Sir")
    start = datetime.utcnow() - timedelta(hours=2)
    promise = _msg(db, contact, direction="outbound", body=PSUR, when=start)
    _msg(db, contact, direction="inbound", body="Ok", when=start + timedelta(minutes=1))
    _msg(db, contact, direction="inbound", body=FAST, when=start + timedelta(minutes=2))
    db.commit()
    tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
    with patch(
        "app.services.whatsapp.classifier.detect_commitment",
        return_value={
            "is_commitment": True,
            "commitment_type": "other",
            "label": "Arrange the PSUR for Tramadol 50 mg",
            "deadline_at": tomorrow,
        },
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        wa_reprocess.reprocess_contacts(
            db,
            contact_ids=[contact.id],
            dry_run=False,
            reanalyze_outbound=True,
            reclassify_inbound=False,
        )
        wa_reprocess.reprocess_contacts(
            db,
            contact_ids=[contact.id],
            dry_run=False,
            reanalyze_outbound=True,
            reclassify_inbound=False,
        )
    rows = repo.open_commitments_for_contact(db, contact.id, direction="owner")
    assert len(rows) == 1
    assert rows[0].message_id == promise.id
    assert rows[0].deadline_at is None
    assert g.inbound_supports_reply_draft(FAST) is False


def test_explicit_friday_deadline_survives_reconcile():
    db = _session()
    contact = _contact(db, "Client")
    body = "I'll send the documents by Friday."
    when = datetime.utcnow()
    msg = _msg(db, contact, direction="outbound", body=body, when=when)
    friday = datetime(2026, 10, 16, 17, 0, 0)
    row = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=msg.id,
        commitment_type="document",
        label="Send documents",
        direction="owner",
        deadline_at=friday,
    )
    db.commit()
    summary = wa_reprocess.reprocess_contacts(
        db,
        contact_ids=[contact.id],
        dry_run=False,
        reanalyze_outbound=False,
    )
    assert summary.deadlines_cleared == 0
    assert summary.commitments_invalidated == 0
    db.refresh(row)
    assert row.deadline_at == friday
