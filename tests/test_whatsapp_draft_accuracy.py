"""Factual draft paths: hints, fallbacks, and stored timeline cards."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models
from app.database import Base
from app.services.whatsapp.prompts import draft_category_hint
from app.services.whatsapp.service import WhatsAppService, _draft_reply_with_budget

_ALLOWED = {"decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []}}


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _message(db, body: str):
    contact = models.WhatsAppContact(wa_id="919800000099", profile_name="Ejike")
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


class TestCategoryHints:
    def test_lead_document_and_timeline_hints_do_not_require_unsupported_facts(self):
        lead = draft_category_hint("lead", None, personal=False)
        document = draft_category_hint("document", None, personal=False)
        timeline = draft_category_hint("timeline", None, personal=False)
        meeting = draft_category_hint("meeting", None, personal=False)
        budget = draft_category_hint("budget", None, personal=False)

        assert "signal you can share a catalogue" not in lead.lower()
        assert "do not offer a catalogue" in lead.lower()
        assert "confirm you will send it shortly" not in document.lower()
        assert "do not promise to send it" in document.lower()
        assert "confirm or propose dates" not in timeline.lower()
        assert "unless that date is already in the conversation" in timeline.lower()
        assert "propose options" not in meeting.lower()
        assert budget == "The client is asking about budget/pricing. Acknowledge and indicate next step."


class TestFallbackDrafts:
    def test_unknown_fact_categories_do_not_persist_a_template(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
            for category in ("document", "lead", "timeline", "meeting"):
                text = _draft_reply_with_budget([], "hello", category, result=_ALLOWED)
                assert text is None

    def test_delayed_shipment_fallback_does_not_promise_a_revised_date(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
            text = _draft_reply_with_budget(
                [],
                "The shipment is delayed",
                "shipment",
                result=_ALLOWED,
                shipment_status="delayed",
            )
        assert text == "Thanks for the update. I can't confirm a new arrival date from this message."
        assert "will confirm the revised delivery" not in text.lower()

    def test_payment_fallback_still_verifies_without_confirming_receipt(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
            text = _draft_reply_with_budget(
                [],
                "Payment done please check",
                "payment",
                result=_ALLOWED,
                payment_status="received",
            )
        assert "verify" in text.lower()
        assert "do not confirm receipt" not in text.lower()

    def test_blocked_decisions_do_not_reach_a_template(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False), patch(
            "app.services.whatsapp.service.wa_fallback.draft_reply"
        ) as fallback:
            for decision in (
                {"reply_allowed": False, "schema_valid": True},
                {"reply_allowed": True, "schema_valid": False},
                {"reply_allowed": True, "schema_valid": True, "missing_facts": ["price"]},
            ):
                text = _draft_reply_with_budget(
                    [],
                    "How much is it?",
                    "budget",
                    result={"decision": decision},
                )
                assert text is None
        fallback.assert_not_called()


class TestTimelinePersistence:
    def test_late_december_is_stored_without_a_date_or_draft(self):
        db = _session()
        body = "it will be around late December"
        message = _message(db, body)
        service = WhatsAppService.__new__(WhatsAppService)
        with patch(
            "app.services.whatsapp.service.classifier.extract_deadline",
            return_value={
                "deadline_label": "Delivery deadline",
                "date": "2026-12-31",
                "date_rejected": False,
            },
        ), patch(
            "app.services.whatsapp.service._draft_reply_with_budget",
            return_value="Late December works for us.",
        ) as drafted:
            suggestion = service._create_suggestion(
                db,
                message,
                [],
                body,
                {
                    "category": "timeline",
                    "decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []},
                },
                "normal",
            )
        drafted.assert_not_called()
        assert suggestion.draft_text is None
        details = json.loads(suggestion.details)
        assert details["deadline_date"] is None
        assert details["date"] is None
        assert "2026-12-31" not in json.dumps(details)

    def test_explicit_day_is_stored_with_its_draft(self):
        db = _session()
        body = "Please deliver by the 20th"
        message = _message(db, body)
        service = WhatsAppService.__new__(WhatsAppService)
        with patch(
            "app.services.whatsapp.service.classifier.extract_deadline",
            return_value={
                "deadline_label": "Delivery deadline",
                "date": "2026-12-20",
                "date_rejected": False,
            },
        ), patch(
            "app.services.whatsapp.service._draft_reply_with_budget",
            return_value="We'll deliver by the 20th.",
        ):
            suggestion = service._create_suggestion(
                db,
                message,
                [],
                body,
                {
                    "category": "timeline",
                    "decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []},
                },
                "normal",
            )
        assert suggestion.draft_text == "We'll deliver by the 20th."
        details = json.loads(suggestion.details)
        assert details["deadline_date"] == "2026-12-20"
        assert details["date"] == "2026-12-20"
