"""Deploy-time WhatsApp refresh: scope, idempotence, and no message sends."""

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
from app.services.whatsapp import deploy_refresh as deploy
from app.services.whatsapp import repository as repo

PSUR = "I will arrange the PSUR for Tramadol 50 mg."
FAST = "Please make it fast"
AGAIN = "Again"
INVOICE = "Please resend the invoice."


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _contact(db, name: str) -> models.WhatsAppContact:
    row = models.WhatsAppContact(wa_id=f"9199{abs(hash(name)) % 100000:05d}", profile_name=name)
    db.add(row)
    db.flush()
    return row


def _msg(db, contact, *, direction: str, body: str) -> models.WhatsAppMessage:
    row = models.WhatsAppMessage(
        contact_id=contact.id,
        direction=direction,
        body=body,
        timestamp=datetime.utcnow(),
        msg_type="text",
        classified_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _run(db, data_dir: Path, **kwargs):
    params = dict(
        enabled=True,
        dry_run=False,
        release="2026-10-15-drafts",
        contact_ids=[],
        wa_ids=[],
        reclassify_inbound=False,
        max_inbound=3,
        data_dir=data_dir,
    )
    params.update(kwargs)
    return deploy.run_deploy_refresh(db, **params)


def test_disabled_and_unscoped_do_not_write(tmp_path):
    db = _session()
    contact = _contact(db, "Friday Sir")
    message = _msg(db, contact, direction="inbound", body=FAST)
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=message.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="I'll hurry.",
        )
    )
    db.commit()
    assert _run(db, tmp_path, enabled=False).status == "disabled"
    db.expire_all()
    assert db.query(models.WhatsAppMessage).count() == 1
    assert db.query(models.WhatsAppSuggestion).filter_by(status="pending").count() == 1
    assert list(tmp_path.glob("whatsapp_reprocess_releases/*")) == []


def test_missing_contact_ids_refresh_every_stored_chat(tmp_path):
    db = _session()
    contact = _contact(db, "Friday Sir")
    message = _msg(db, contact, direction="inbound", body=FAST)
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=message.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="I'll hurry.",
        )
    )
    db.commit()
    with patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        result = _run(db, tmp_path, contact_ids=[], wa_ids=[])
    assert result.status == "applied"
    assert result.reply_drafts_cleared == 1
    db.expire_all()
    assert db.query(models.WhatsAppMessage).count() == 1
    assert db.query(models.WhatsAppSuggestion).filter_by(status="pending").count() == 0


def test_invalid_draft_dismissed_message_and_valid_commitment_kept(tmp_path):
    db = _session()
    contact = _contact(db, "Friday Sir")
    promise = _msg(db, contact, direction="outbound", body=PSUR)
    hurry = _msg(db, contact, direction="inbound", body=FAST)
    kept = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Arrange PSUR for Tramadol 50 mg",
        direction="owner",
        deadline_at=None,
    )
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=hurry.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="Making it fast.",
        )
    )
    db.commit()
    with patch("app.services.whatsapp.actions.send_reply") as sent, patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ), patch(
        "app.services.whatsapp.classifier.detect_commitment",
        return_value={
            "is_commitment": True,
            "commitment_type": "other",
            "label": "Arrange the PSUR for Tramadol 50 mg",
            "deadline_at": None,
        },
    ):
        result = _run(db, tmp_path, contact_ids=[contact.id], reclassify_inbound=False)
    sent.assert_not_called()
    assert result.status == "applied"
    assert result.reply_drafts_cleared == 1
    db.expire_all()
    assert db.get(models.WhatsAppMessage, promise.id).body == PSUR
    assert db.get(models.WhatsAppMessage, hurry.id).body == FAST
    db.refresh(kept)
    assert kept.fulfilled_at is None
    pending = db.query(models.WhatsAppSuggestion).filter_by(status="pending").all()
    assert all(row.kind != "reply" or row.message_id != hurry.id for row in pending)
    again = _run(db, tmp_path, contact_ids=[contact.id])
    assert again.status == "already_applied"
    assert repo.open_commitments_for_contact(db, contact.id, direction="owner")


def test_again_does_not_gain_a_draft_and_invoice_can(tmp_path):
    db = _session()
    lotus = _contact(db, "Lotus")
    friday = _contact(db, "Friday Sir")
    _msg(db, lotus, direction="inbound", body=AGAIN)
    invoice = _msg(db, friday, direction="inbound", body=INVOICE)
    db.commit()
    before = db.query(models.WhatsAppMessage).count()

    def _classify(self, db, message):
        if message.id != invoice.id:
            return
        db.add(
            models.WhatsAppSuggestion(
                contact_id=message.contact_id,
                message_id=message.id,
                kind="reply",
                category="document",
                status="pending",
                draft_text="Resending the invoice.",
            )
        )
        db.flush()

    with patch(
        "app.services.whatsapp.service.WhatsAppService.classify_message_now",
        _classify,
    ), patch("app.services.whatsapp.actions.send_reply") as sent, patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ), patch(
        "app.services.whatsapp.classifier.detect_commitment",
        return_value={"is_commitment": False, "commitment_type": None, "label": None, "deadline_at": None},
    ):
        lotus_result = _run(
            db,
            tmp_path,
            release="lotus-again",
            contact_ids=[lotus.id],
            reclassify_inbound=True,
        )
        friday_result = _run(
            db,
            tmp_path,
            release="friday-invoice",
            contact_ids=[friday.id],
            reclassify_inbound=True,
        )
    sent.assert_not_called()
    assert lotus_result.inbound_reclassified == 0
    assert friday_result.inbound_reclassified == 1
    assert db.query(models.WhatsAppMessage).count() == before
    drafts = (
        db.query(models.WhatsAppSuggestion)
        .filter_by(contact_id=friday.id, status="pending", kind="reply")
        .all()
    )
    assert len(drafts) == 1
    assert drafts[0].message_id == invoice.id
    second = _run(
        db,
        tmp_path,
        release="friday-invoice",
        contact_ids=[friday.id],
        reclassify_inbound=True,
    )
    assert second.status == "already_applied"
    assert (
        db.query(models.WhatsAppSuggestion)
        .filter_by(contact_id=friday.id, kind="reply", status="pending")
        .count()
        == 1
    )


def test_failure_rolls_back_and_does_not_mark_the_release(tmp_path):
    db = _session()
    contact = _contact(db, "Friday Sir")
    message = _msg(db, contact, direction="outbound", body=PSUR)
    kept = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=message.id,
        commitment_type="other",
        label="Arrange PSUR",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    db.commit()
    kept_id = kept.id

    def _boom(db, **kwargs):
        db.add(
            models.WhatsAppSuggestion(
                contact_id=contact.id,
                message_id=message.id,
                kind="reply",
                category="other",
                status="pending",
                draft_text="should not stick",
            )
        )
        db.flush()
        raise RuntimeError("database unavailable")

    with patch("app.services.whatsapp.reprocess.reprocess_contacts", _boom):
        try:
            _run(db, tmp_path, contact_ids=[contact.id])
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected failure")
    db.rollback()
    assert deploy.release_already_applied("2026-10-15-drafts", data_dir=tmp_path) is False
    assert db.query(models.WhatsAppSuggestion).count() == 0
    assert db.get(models.WhatsAppCommitment, kept_id).fulfilled_at is None
    assert db.query(models.WhatsAppMessage).count() == 1


def test_dry_run_leaves_rows_and_skips_a_second_worker_after_apply(tmp_path):
    db = _session()
    contact = _contact(db, "Lotus")
    again = _msg(db, contact, direction="inbound", body=AGAIN)
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=again.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="Again?",
        )
    )
    db.commit()
    with patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        dry = _run(db, tmp_path, contact_ids=[contact.id], dry_run=True)
    assert dry.status == "dry_run"
    assert dry.reply_drafts_cleared == 1
    db.expire_all()
    assert db.query(models.WhatsAppSuggestion).filter_by(status="pending").count() == 1
    assert deploy.release_already_applied("2026-10-15-drafts", data_dir=tmp_path) is False


def test_docker_refresh_is_opt_in_and_not_part_of_the_image_build():
    root = ROOT
    compose = (root / "docker-compose.server.yml").read_text(encoding="utf-8")
    entry = (root / "scripts/docker-server-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile.server").read_text(encoding="utf-8")
    assert "WHATSAPP_REPROCESS_ON_DEPLOY: \"${WHATSAPP_REPROCESS_ON_DEPLOY:-false}\"" in compose
    assert "WHATSAPP_REPROCESS_DRY_RUN: \"${WHATSAPP_REPROCESS_DRY_RUN:-false}\"" in compose
    assert "WHATSAPP_REPROCESS_ON_DEPLOY" in entry
    assert "python -m app.services.whatsapp.deploy_refresh" in entry
    assert "exec \"$@\"" in entry
    assert "RUN python -m app.services.whatsapp.deploy_refresh" not in dockerfile
    settings = (root / "app/services/whatsapp/settings.py").read_text(encoding="utf-8")
    assert 'WHATSAPP_REPROCESS_ON_DEPLOY = _flag("WHATSAPP_REPROCESS_ON_DEPLOY", "false")' in settings
