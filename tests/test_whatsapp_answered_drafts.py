"""A client message the owner already answered must not keep a reply draft."""

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
from app.services.whatsapp import reprocess as wa_reprocess
from app.services.whatsapp import repository as repo
from app.services.whatsapp.service import WhatsAppService

CLASSIFIED = {
    "is_important": True,
    "category": "document",
    "lane": "work",
    "safety_concern": False,
    "payment_status": None,
    "document_type": "invoice",
    "anger_level": None,
    "shipment_status": None,
    "language": "English",
    "translation": None,
    "summary": "Client asks for the invoice again",
    "personal_tone": False,
    "needs_clarification": False,
    "clarifying_question": None,
    "clarifying_options": None,
    "confidence": 95,
    "priority": "normal",
    "decision": {
        "reply_allowed": True,
        "schema_valid": True,
        "missing_facts": [],
    },
}


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _contact(db, wa_id: str, name: str) -> models.WhatsAppContact:
    contact = models.WhatsAppContact(wa_id=wa_id, profile_name=name, is_group=False)
    db.add(contact)
    db.flush()
    return contact


def _message(db, contact, *, direction: str, body: str, when: datetime) -> models.WhatsAppMessage:
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        direction=direction,
        body=body,
        timestamp=when,
        msg_type="text",
    )
    db.add(message)
    db.flush()
    return message


def _reply(db, contact, message, text: str) -> models.WhatsAppSuggestion:
    return repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=message.id,
        kind="reply",
        category="other",
        priority="normal",
        lane="work",
        draft_text=text,
    )


def test_owner_ok_removes_the_reply_and_keeps_a_separate_reminder():
    db = _session()
    contact = _contact(db, "919811111111", "Karan")
    start = datetime(2026, 10, 13, 7, 28)
    client = _message(
        db,
        contact,
        direction="inbound",
        body="Mill k baat karna padega",
        when=start,
    )
    owner = _message(db, contact, direction="outbound", body="Ok", when=start + timedelta(minutes=1))
    reply = _reply(db, contact, client, "Karan, koi update hai? Office mein kisi ko bhejne ka plan hua kya?")
    waiting = repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=client.id,
        kind="client_commitment_reminder",
        category="client_commitment",
        priority="high",
        lane="work",
        draft_text="Karan, koi update hai?",
        details={"chip_label": "Waiting on Karan: send someone to the office, 7 days."},
    )
    promise = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=owner.id,
        commitment_type="other",
        label="check and revert",
        direction="owner",
        deadline_at=None,
    )
    reminder = repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=owner.id,
        kind="commitment_reminder",
        category="pending_commitment",
        priority="high",
        lane="work",
        draft_text=None,
        details={"commitment_id": promise.id, "chip_label": "You promised: check and revert to Karan."},
    )
    db.commit()

    assert repo.dismiss_suggestions_already_answered(db) == 2
    db.commit()
    db.refresh(reply)
    db.refresh(waiting)
    db.refresh(reminder)
    assert reply.status == "dismissed"
    assert reply.draft_text is None
    assert waiting.status == "pending"
    assert waiting.draft_text is None
    assert reminder.status == "pending"
    assert db.get(models.WhatsAppCommitment, promise.id).fulfilled_at is None
    assert db.query(models.WhatsAppMessage).count() == 2


def test_a_real_owner_reply_clears_the_same_draft_for_another_contact():
    db = _session()
    contact = _contact(db, "919822222222", "Mehul")
    start = datetime(2026, 10, 12, 4, 0)
    client = _message(db, contact, direction="inbound", body="Need the packing list today", when=start)
    _message(
        db,
        contact,
        direction="outbound",
        body="I have sent the packing list.",
        when=start + timedelta(minutes=3),
    )
    reply = _reply(db, contact, client, "Sending the packing list now.")
    db.commit()
    assert repo.dismiss_suggestions_already_answered(db) == 1
    db.refresh(reply)
    assert reply.status == "dismissed"
    assert reply.draft_text is None


def test_a_new_request_after_the_reply_can_still_draft():
    db = _session()
    contact = _contact(db, "919833333333", "Asha")
    start = datetime(2026, 10, 14, 6, 0)
    old = _message(db, contact, direction="inbound", body="Please call the mill", when=start)
    _message(db, contact, direction="outbound", body="Ok", when=start + timedelta(minutes=1))
    _message(
        db,
        contact,
        direction="outbound",
        body="Please find invoice INV-1042 attached.",
        when=start + timedelta(minutes=2),
    )
    fresh = _message(
        db,
        contact,
        direction="inbound",
        body="Can you send the invoice again?",
        when=start + timedelta(minutes=10),
    )
    old_reply = _reply(db, contact, old, "I will call them.")
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch(
        "app.services.whatsapp.service.classifier.classify_message",
        return_value=CLASSIFIED,
    ), patch(
        "app.services.whatsapp.service.classifier.detect_client_commitment",
        return_value={"is_commitment": False, "commitment_type": None, "label": None, "deadline_at": None},
    ), patch(
        "app.services.whatsapp.service.classifier.draft_reply",
        return_value="I am resending invoice INV-1042.",
    ), patch(
        "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
        True,
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service.classify_message_now(db, fresh)
    db.expire_all()
    repo.dismiss_suggestions_already_answered(db)
    db.expire_all()
    stale = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.message_id == old.id,
            models.WhatsAppSuggestion.status == "pending",
            models.WhatsAppSuggestion.kind == "reply",
        )
        .all()
    )
    fresh_rows = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.message_id == fresh.id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .all()
    )
    assert stale == []
    assert len(fresh_rows) == 1
    assert fresh_rows[0].draft_text == "I am resending invoice INV-1042."


def test_reprocess_does_not_recreate_the_answered_draft():
    db = _session()
    contact = _contact(db, "919844444444", "Karan")
    start = datetime(2026, 10, 13, 7, 28)
    client = _message(db, contact, direction="inbound", body="Mill k baat karna padega", when=start)
    _message(db, contact, direction="outbound", body="Ok", when=start + timedelta(minutes=1))
    _reply(db, contact, client, "Karan, koi update hai?")
    db.commit()
    from app.services.manager import service_manager

    service = WhatsAppService.__new__(WhatsAppService)
    previous = service_manager._whatsapp
    service_manager._whatsapp = service
    try:
        with patch(
            "app.services.whatsapp.service.classifier.classify_message",
            return_value=CLASSIFIED,
        ), patch(
            "app.services.whatsapp.service.classifier.detect_client_commitment",
            return_value={"is_commitment": False, "commitment_type": None, "label": None, "deadline_at": None},
        ), patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value="Karan, koi update hai?",
        ), patch(
            "app.services.whatsapp.service.classifier.detect_commitment",
            return_value={"is_commitment": False, "commitment_type": None, "label": None, "deadline_at": None},
        ), patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ), patch(
            "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
            return_value=[],
        ):
            wa_reprocess.reprocess_contacts(
                db,
                contact_ids=[contact.id],
                dry_run=False,
                reclassify_inbound=True,
                max_inbound_reclassify=3,
            )
    finally:
        service_manager._whatsapp = previous
    db.expire_all()
    pending_replies = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact.id,
            models.WhatsAppSuggestion.status == "pending",
            models.WhatsAppSuggestion.kind == "reply",
        )
        .all()
    )
    assert pending_replies == []
    assert db.query(models.WhatsAppMessage).count() == 2
