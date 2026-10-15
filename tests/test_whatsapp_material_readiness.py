"""Draft and proposal requests need owner readiness before a reply is drafted."""

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
from app.services.whatsapp import classifier
from app.services.whatsapp import product_gates
from app.services.whatsapp.prompts import _REPLY_SYSTEM, draft_category_hint
from app.services.whatsapp.service import WhatsAppService, _draft_reply_with_budget

_HOTEL = "This is the hotel location. I will finalize the hotel there."
_DRAFT_ASK = "Sir can you make the new draft and send to that group please"
_PROPOSALS = "Yes including the new proposals"
_READY = "The new proposals are ready. I'll prepare the draft and send it."
_NO_COMMITMENT = {
    "is_commitment": False,
    "commitment_type": None,
    "label": None,
    "deadline_at": None,
}


def _model(**overrides):
    base = {
        "is_important": True,
        "category": "document",
        "safety_concern": False,
        "payment_status": None,
        "document_type": "draft",
        "anger_level": None,
        "shipment_status": None,
        "language": "English",
        "translation": None,
        "summary": "Client asks for a new draft",
        "personal_tone": False,
        "needs_clarification": False,
        "clarifying_question": None,
        "clarifying_options": None,
        "confidence": 94,
        "reply_allowed": True,
    }
    base.update(overrides)
    return base


def _classify(history, message, payload):
    with patch.object(classifier, "_chat_json", return_value=payload):
        return classifier.classify_message(history, message)


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestMaterialReadiness:
    def test_prompt_does_not_treat_a_request_as_agreement(self):
        assert "does not prove the account owner has agreed" in _REPLY_SYSTEM
        assert "never promise preparation, inclusion, revision, or sending" in _REPLY_SYSTEM
        hint = draft_category_hint("document", None, personal=False)
        assert "do not promise to send it" in hint.lower()
        assert "owner has agreed" in hint.lower()
        guide = classifier._CLASSIFY_SYSTEM.lower()
        assert "owner has agreed to do it" in guide

    def test_unready_draft_request_requires_clarification_and_stores_no_reply(self):
        history = [{"direction": "outbound", "body": _HOTEL}]
        result = _classify(history, _DRAFT_ASK, _model())
        assert result["needs_clarification"] is True
        assert result["decision"]["reply_allowed"] is False
        assert result["clarifying_question"] == "Can you prepare and send this?"
        assert result["clarifying_options"] == [
            "Yes, it's ready",
            "Not ready yet",
            "Need more detail",
        ]
        with patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value="Sure, I'll include the new proposals and send the draft shortly.",
        ) as drafted:
            text = _draft_reply_with_budget(
                history,
                _DRAFT_ASK,
                "document",
                result=result,
            )
        drafted.assert_not_called()
        assert text is None

        db = _session()
        contact = models.WhatsAppContact(wa_id="919800000321", profile_name="Client")
        db.add(contact)
        db.flush()
        db.add(
            models.WhatsAppMessage(
                contact_id=contact.id,
                direction="outbound",
                body=_HOTEL,
                timestamp=datetime.utcnow() - timedelta(days=2),
            )
        )
        message = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body=_DRAFT_ASK,
            timestamp=datetime.utcnow(),
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        service = WhatsAppService.__new__(WhatsAppService)
        with patch(
            "app.services.whatsapp.service.classifier._chat_json",
            return_value=_model(),
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
        stored = (
            db.query(models.WhatsAppSuggestion)
            .filter(models.WhatsAppSuggestion.message_id == message.id)
            .one()
        )
        assert stored.draft_text is None
        assert stored.kind == "clarify"
        details = json.loads(stored.details)
        assert details["needs_clarification"] is True
        assert "send the draft shortly" not in json.dumps(details).lower()

    def test_repeated_request_still_does_not_invent_a_promise(self):
        history = [
            {"direction": "outbound", "body": _HOTEL},
            {"direction": "inbound", "body": _DRAFT_ASK},
        ]
        result = _classify(history, _PROPOSALS, _model(summary="Client adds the new proposals"))
        assert result["needs_clarification"] is True
        assert result["decision"]["reply_allowed"] is False
        assert product_gates.should_clarify_missing_material_readiness(_PROPOSALS, history)
        with patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value="Sure, I'll include the new proposals and send the draft shortly.",
        ) as drafted:
            assert _draft_reply_with_budget(history, _PROPOSALS, "document", result=result) is None
        drafted.assert_not_called()

    def test_ready_and_agreed_draft_can_still_be_written(self):
        history = [
            {"direction": "outbound", "body": _HOTEL},
            {"direction": "outbound", "body": _READY},
            {"direction": "inbound", "body": _DRAFT_ASK},
        ]
        result = _classify(history, _PROPOSALS, _model())
        assert result["needs_clarification"] is False
        assert result["decision"]["reply_allowed"] is True
        safe = "I'll send the draft with the proposals that are ready."
        with patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ), patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value=safe,
        ) as drafted:
            text = _draft_reply_with_budget(history, _PROPOSALS, "document", result=result)
        drafted.assert_called_once()
        assert text == safe

    def test_available_invoice_copy_still_drafts(self):
        history = [
            {"direction": "outbound", "body": "Please find invoice INV-1042 attached."},
            {"direction": "inbound", "body": "Got it"},
        ]
        message = "Can you send the invoice again?"
        result = _classify(
            history,
            message,
            _model(document_type="invoice", summary="Client wants the invoice resent"),
        )
        assert result["needs_clarification"] is False
        assert result["decision"]["reply_allowed"] is True
        assert not product_gates.should_clarify_missing_material_readiness(message, history)
        safe = "Sending the invoice copy from our earlier message."
        with patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ), patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value=safe,
        ):
            assert _draft_reply_with_budget(history, message, "document", result=result) == safe

    def test_missing_stock_date_and_price_remain_blocked(self):
        assert product_gates.should_clarify_missing_owner_fact("When will the stock arrive?", [])
        assert product_gates.should_clarify_missing_owner_fact("What's the price?", [])
        result = _classify(
            [],
            "When will the stock arrive?",
            _model(category="shipment", document_type=None, summary="Stock ETA"),
        )
        assert result["needs_clarification"] is True
        assert result["decision"]["reply_allowed"] is False
        assert result["clarifying_question"] == "What should I tell them?"
