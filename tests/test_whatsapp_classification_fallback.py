"""Keyword classification must not draft without a validated decision."""

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
from app.services.whatsapp import product_gates
from app.services.whatsapp.classifier import WhatsAppAIError
from app.services.whatsapp.repository import has_reply_precedent
from app.services.whatsapp.service import WhatsAppService, _draft_reply_with_budget

_NO_COMMITMENT = {
    "is_commitment": False,
    "commitment_type": None,
    "label": None,
    "deadline_at": None,
}


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _precedent(db, category: str) -> None:
    contact = models.WhatsAppContact(
        wa_id=f"prior-{category}",
        profile_name="Earlier client",
    )
    db.add(contact)
    db.flush()
    earlier = datetime.utcnow() - timedelta(days=2)
    later = datetime.utcnow() - timedelta(days=1)
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="Earlier message",
            timestamp=earlier,
            category=category,
            classified_at=earlier,
        )
    )
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="outbound",
            body="Earlier reply.",
            timestamp=later,
        )
    )
    db.commit()


def _inbound(db, body: str) -> models.WhatsAppMessage:
    contact = models.WhatsAppContact(wa_id="919800000010", profile_name="Client")
    db.add(contact)
    db.flush()
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body=body,
        timestamp=datetime.utcnow(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _classify(db, message: models.WhatsAppMessage):
    service = WhatsAppService.__new__(WhatsAppService)
    with patch(
        "app.services.whatsapp.service.classifier.classify_message",
        side_effect=WhatsAppAIError("classification down"),
    ), patch(
        "app.services.whatsapp.service.classifier.detect_client_commitment",
        return_value=_NO_COMMITMENT,
    ), patch(
        "app.services.whatsapp.service.classifier.draft_reply",
        return_value="I'll confirm the price and the meeting time.",
    ) as drafted, patch(
        "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
        True,
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service.classify_message_now(db, message)
    drafted.assert_not_called()
    db.expire_all()
    return (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.message_id == message.id)
        .all()
    )


class TestClassificationFallbackDoesNotDraft:
    def test_stock_question_stores_no_factual_draft(self):
        db = _session()
        body = "When will the stock arrive?"
        assert product_gates.should_clarify_missing_owner_fact(body, [])
        message = _inbound(db, body)
        rows = _classify(db, message)
        assert len(rows) == 1
        assert rows[0].draft_text is None
        assert rows[0].category == "error"
        db.refresh(message)
        assert message.category == "error"

    def test_price_message_containing_call_does_not_draft_a_meeting(self):
        db = _session()
        _precedent(db, "meeting")
        assert has_reply_precedent(db, "meeting")
        body = "What is the price? Please call."
        message = _inbound(db, body)
        rows = _classify(db, message)
        assert len(rows) == 1
        assert rows[0].draft_text is None
        assert rows[0].category == "meeting"
        db.refresh(message)
        assert message.category == "meeting"

    def test_payment_message_keeps_a_card_without_a_payment_draft(self):
        db = _session()
        _precedent(db, "payment")
        assert has_reply_precedent(db, "payment")
        body = "I made the payment yesterday."
        message = _inbound(db, body)
        rows = _classify(db, message)
        assert len(rows) == 1
        assert rows[0].draft_text is None
        assert rows[0].category == "payment"
        db.refresh(message)
        assert message.category == "payment"


def test_a_missing_decision_on_the_shared_gate_still_drafts():
    """Callers that are not the keyword-classification path still draft."""
    with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
        text = _draft_reply_with_budget([], "Thanks", "other", result={})
    assert text == "Thanks for your message. I'll get back to you shortly."
