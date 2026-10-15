"""History window and commitment identity for the live inbox cases."""

from __future__ import annotations

import inspect
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
from app.services.whatsapp import classifier, product_gates as g
from app.services.whatsapp.repository import (
    create_commitment,
    create_suggestion,
    live_chat_context,
    open_commitments_for_contact,
    quote_newer_inbound_on_pending_cards,
    recent_history,
)
from app.services.whatsapp.service import WhatsAppService
from app.services.whatsapp.settings import WHATSAPP_CHAT_CONTEXT_MESSAGES


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_each_model_path_requests_forty_messages_not_twenty():
    assert WHATSAPP_CHAT_CONTEXT_MESSAGES == 40
    assert inspect.signature(live_chat_context).parameters["limit"].default == 40
    service = Path(ROOT, "app/services/whatsapp/service.py").read_text(encoding="utf-8")
    actions = Path(ROOT, "app/services/whatsapp/actions.py").read_text(encoding="utf-8")
    assert service.count("WHATSAPP_CHAT_CONTEXT_MESSAGES") >= 3
    assert "limit=12" not in service
    assert "limit=20" not in service
    assert actions.count("limit=WHATSAPP_CHAT_CONTEXT_MESSAGES") == 2


def test_forty_message_window_keeps_an_older_promise_that_twenty_drops():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919810000040", profile_name="Punit")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(hours=50)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Kal revert karta hun confirm",
        timestamp=start,
    )
    db.add(promise)
    db.flush()
    for index in range(39):
        db.add(
            models.WhatsAppMessage(
                contact_id=contact.id,
                direction="inbound" if index % 2 else "outbound",
                body=f"Unrelated note {index}",
                timestamp=start + timedelta(minutes=index + 1),
            )
        )
    db.commit()
    wide = recent_history(db, contact.id, limit=40)
    narrow = recent_history(db, contact.id, limit=20)
    assert any(item["body"] == "Kal revert karta hun confirm" for item in wide)
    assert all(item["body"] != "Kal revert karta hun confirm" for item in narrow)
    assert len(wide) == 40


def test_thank_you_and_unrelated_chat_do_not_move_or_renew_the_promise():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919810000041", profile_name="Punit")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(hours=5)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Kal revert karta hun confirm",
        timestamp=start,
        msg_type="text",
    )
    pet = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Sharing the pet clinic contact.",
        timestamp=start + timedelta(hours=1),
        msg_type="text",
    )
    thanks = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Thank you",
        timestamp=start + timedelta(hours=2),
        msg_type="text",
    )
    db.add_all([promise, pet, thanks])
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Revert to confirm",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text=None,
        details={"commitment_id": commitment.id},
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(classifier, "check_commitment_fulfilled", return_value=True), patch.object(
        classifier, "detect_commitment"
    ) as detect:
        service._analyze_commitment_unsafe(db, pet)
    detect.assert_not_called()
    db.refresh(commitment)
    assert commitment.fulfilled_at is None
    assert commitment.message_id == promise.id
    assert quote_newer_inbound_on_pending_cards(db, thanks) == 0
    card = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.kind == "commitment_reminder")
        .one()
    )
    assert card.message_id == promise.id


def test_invitation_letter_completes_only_that_commitment():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919810000042", profile_name="Abdullah")
    db.add(contact)
    db.flush()
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="I will send the invitation letter.",
        timestamp=datetime.utcnow() - timedelta(hours=3),
        msg_type="text",
    )
    other = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="I will get the estimate timeline.",
        timestamp=datetime.utcnow() - timedelta(hours=2),
        msg_type="text",
    )
    document = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Invitation_letter.pdf",
        timestamp=datetime.utcnow(),
        msg_type="document",
    )
    db.add_all([promise, other, document])
    db.flush()
    invitation = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="document",
        label="Send the invitation letter",
        direction="owner",
        deadline_at=None,
    )
    timeline = create_commitment(
        db,
        contact_id=contact.id,
        message_id=other.id,
        commitment_type="other",
        label="Get the estimate timeline",
        direction="owner",
        deadline_at=None,
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(classifier, "check_commitment_fulfilled", return_value=True), patch.object(
        classifier, "detect_commitment"
    ) as detect:
        service._analyze_commitment_unsafe(db, document)
    detect.assert_not_called()
    db.refresh(invitation)
    db.refresh(timeline)
    assert invitation.fulfilled_at is not None
    assert invitation.message_id == promise.id
    assert timeline.fulfilled_at is None
    assert timeline.message_id == other.id


def test_unrelated_pdf_does_not_finish_mebeneel_and_a_second_promise_is_kept():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919810000043", profile_name="Shubham")
    db.add(contact)
    db.flush()
    design = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="I will share Mebeneel Design.",
        timestamp=datetime.utcnow() - timedelta(hours=4),
        msg_type="text",
    )
    pdf = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="random-notes.pdf",
        timestamp=datetime.utcnow() - timedelta(hours=1),
        msg_type="document",
    )
    later = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="I'll update you in a couple of hours.",
        timestamp=datetime.utcnow(),
        msg_type="text",
    )
    db.add_all([design, pdf, later])
    db.flush()
    original = create_commitment(
        db,
        contact_id=contact.id,
        message_id=design.id,
        commitment_type="document",
        label="Share Mebeneel Design",
        direction="owner",
        deadline_at=None,
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(classifier, "check_commitment_fulfilled", return_value=True):
        service._analyze_commitment_unsafe(db, pdf)
    db.refresh(original)
    assert original.fulfilled_at is None
    assert original.message_id == design.id

    hour_deadline = (datetime.utcnow() + timedelta(hours=2)).replace(microsecond=0)
    with patch.object(
        classifier,
        "_chat_json",
        return_value={
            "is_commitment": True,
            "commitment_type": "other",
            "label": "Update you",
            "deadline_at": hour_deadline.isoformat(),
        },
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ), patch(
        "app.services.whatsapp.service.actions.create_commitment_reminder",
        return_value=None,
    ):
        service._analyze_commitment_unsafe(db, later)
    db.refresh(original)
    rows = open_commitments_for_contact(db, contact.id, direction="owner")
    assert original.fulfilled_at is None
    assert [row.message_id for row in rows] == [design.id, later.id]
    assert rows[1].deadline_at == hour_deadline


def test_this_week_and_early_next_week_do_not_become_wednesday():
    wednesday = "2026-10-14"
    assert g.supported_commitment_deadline("I will send this week.", f"{wednesday}T15:00:00") is None
    assert (
        classifier.stated_deadline_date("I'll update you early next week.", wednesday) is None
    )
    friday = "2026-10-16T17:00:00"
    assert g.supported_commitment_deadline("I'll send the documents by Friday.", friday) == friday
