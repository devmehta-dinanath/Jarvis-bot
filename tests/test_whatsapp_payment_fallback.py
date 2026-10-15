"""Model-failure payment keywords must not default to overdue."""

from __future__ import annotations

import json
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
from app.services.whatsapp.classifier import WhatsAppAIError
from app.services.whatsapp.service import WhatsAppService, _payment_chip_label

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


def _inbound(db, body: str) -> models.WhatsAppMessage:
    contact = models.WhatsAppContact(wa_id="919800000880", profile_name="Client")
    db.add(contact)
    db.flush()
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="outbound",
            body="Earlier note",
            timestamp=datetime.utcnow() - timedelta(days=2),
        )
    )
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


def _classify_without_model(db, message: models.WhatsAppMessage):
    service = WhatsAppService.__new__(WhatsAppService)
    with patch(
        "app.services.whatsapp.service.classifier.classify_message",
        side_effect=WhatsAppAIError("classification down"),
    ), patch(
        "app.services.whatsapp.service.classifier.detect_client_commitment",
        return_value=_NO_COMMITMENT,
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ), patch(
        "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
        True,
    ):
        service.classify_message_now(db, message)
    db.expire_all()
    return (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.message_id == message.id)
        .one()
    )


class TestKeywordPaymentFallback:
    def test_generic_incoming_and_pending_are_not_overdue(self):
        unknown = (
            "Any update on the payment",
            "Sir incoming payment, so need 1 day to confirm",
            "The payment is still pending",
        )
        for body in unknown:
            assert g.keyword_payment_status(body) is None
            db = _session()
            stored = _classify_without_model(db, _inbound(db, body))
            details = json.loads(stored.details or "{}")
            assert details.get("payment_status") != "overdue"
            assert not str(details.get("chip_label") or "").startswith("Payment overdue")

    def test_sent_and_received_stay_received(self):
        for body in ("Payment sent today", "I received the payment"):
            assert g.keyword_payment_status(body) == "received"
            assert _payment_chip_label("received").startswith("Payment received")

    def test_explicit_overdue_is_preserved(self):
        body = "This invoice is overdue"
        assert g.keyword_payment_status(body) == "overdue"
        assert _payment_chip_label("overdue").startswith("Payment overdue")
        db = _session()
        message = _inbound(db, body)
        service = WhatsAppService.__new__(WhatsAppService)
        service._create_payment_suggestion(
            db,
            message,
            [],
            body,
            {
                "payment_status": "overdue",
                "decision": {"reply_allowed": True, "schema_valid": True},
            },
            "critical",
        )
        db.commit()
        stored = (
            db.query(models.WhatsAppSuggestion)
            .filter(models.WhatsAppSuggestion.message_id == message.id)
            .one()
        )
        details = json.loads(stored.details)
        assert details["payment_status"] == "overdue"
        assert details["chip_label"].startswith("Payment overdue")


def test_fallback_classify_message_does_not_default_payment_to_overdue():
    from app.services.whatsapp import fallback

    unknown = {
        "Any update on the payment": None,
        "Incoming payment expected": None,
        "The payment is still pending": None,
        "I sent a note about the payment": None,
    }
    for body, status in unknown.items():
        result = fallback.classify_message(body)
        assert result["category"] == "payment"
        assert result["payment_status"] is status

    received = fallback.classify_message("Payment received today")
    assert received["category"] == "payment"
    assert received["payment_status"] == "received"

    sent = fallback.classify_message("Payment sent today")
    assert sent["payment_status"] == "received"

    overdue = fallback.classify_message("This invoice is overdue")
    assert overdue["category"] == "payment"
    assert overdue["payment_status"] == "overdue"
