"""Draft Reply path tests — mocked LLM, real classify_message + service gates.

Does not call OpenAI. Does not touch Reminder/Follow-up.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import classifier
from app.services.whatsapp import product_gates as g


def _mock_classify_payload(**overrides):
    base = {
        "is_important": True,
        "category": "document",
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
        "confidence": 92,
    }
    base.update(overrides)
    return base


class TestClassifyMessageDraftPath:
    """A1–A5 through classify_message with mocked _chat_json."""

    def test_a1_invoice_again_no_clarify_high_conf(self):
        history = [
            {"direction": "outbound", "body": "Please find invoice INV-1042 attached."},
            {"direction": "inbound", "body": "Got it"},
        ]
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_mock_classify_payload(
                category="document",
                document_type="invoice",
                summary="Client wants the invoice resent",
                confidence=95,
            ),
        ):
            result = classifier.classify_message(
                history, "Can you send the invoice again?"
            )
        assert result["is_important"] is True
        assert result["category"] == "document"
        assert result["needs_clarification"] is False
        assert result["clarifying_question"] is None
        assert result["confidence"] >= 90
        # Deterministic invent-risk gate must not misfire on invoice re-send.
        assert not g.should_clarify_missing_owner_fact(
            "Can you send the invoice again?", history
        )

    def test_a2_stock_missing_forces_clarification(self):
        history = [
            {"direction": "inbound", "body": "Hi, checking on our order"},
            {"direction": "outbound", "body": "We are looking into it"},
        ]
        # LLM might NOT set needs_clarification — deterministic gate must still force it.
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_mock_classify_payload(
                category="shipment",
                document_type=None,
                shipment_status="delayed",
                summary="Client asks when stock arrives",
                needs_clarification=False,
                confidence=88,
            ),
        ):
            result = classifier.classify_message(
                history, "When will the stock arrive?"
            )
        assert result["needs_clarification"] is True
        assert result["clarifying_question"]
        assert result["clarifying_options"] and len(result["clarifying_options"]) >= 2
        # Confidence pulled down so service confidence gate also suppresses inventing.
        assert result["confidence"] is not None and result["confidence"] <= 60

    def test_a3_stock_friday_in_history_no_clarify(self):
        history = [
            {"direction": "outbound", "body": "Stock will arrive on Friday."},
            {"direction": "inbound", "body": "Thanks"},
        ]
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_mock_classify_payload(
                category="shipment",
                document_type=None,
                shipment_status="good",
                summary="Client asks stock ETA",
                confidence=90,
            ),
        ):
            result = classifier.classify_message(
                history, "When will the stock arrive?"
            )
        assert result["needs_clarification"] is False
        assert result["is_important"] is True
        assert result["confidence"] == 90

    def test_a4_festival_greeting_not_important(self):
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_mock_classify_payload(
                is_important=False,
                category="greeting",
                document_type=None,
                summary="Festival greeting",
                confidence=90,
                personal_tone=True,
            ),
        ):
            result = classifier.classify_message([], "Happy Diwali!")
        assert result["category"] == "greeting"
        assert result["is_important"] is False
        assert result["needs_clarification"] is False
        # Force-clarify must not run on unimportant greetings.
        assert not (
            result["is_important"]
            and g.should_clarify_missing_owner_fact("Happy Diwali!", [])
        )

    def test_a5_hi_greeting_not_important(self):
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_mock_classify_payload(
                is_important=False,
                category="greeting",
                document_type=None,
                summary="Greeting",
                confidence=85,
                personal_tone=True,
            ),
        ):
            result = classifier.classify_message([], "Hi")
        assert result["category"] == "greeting"
        assert result["is_important"] is False
        assert result["needs_clarification"] is False


class TestReplyPromptAndServiceGates:
    def test_reply_system_forbids_invented_facts(self):
        assert "Never invent dates" in classifier._REPLY_SYSTEM
        assert "invoice numbers" in classifier._REPLY_SYSTEM
        assert "ACCURACY (HARD)" in classifier._REPLY_SYSTEM

    def test_draft_anyway_pattern_absent_from_service_source(self):
        """Regression: STEP 2 used to draft on low confidence when AI drafts were on."""
        src = Path(ROOT / "app/services/whatsapp/service.py").read_text(encoding="utf-8")
        assert "suppress_draft_for_confidence = below_threshold and not WHATSAPP_AI_DRAFTS_ENABLED" not in src
        assert "suppress_draft_for_confidence = below_threshold" in src
        assert 'elif result["is_important"] or (below_threshold and WHATSAPP_AI_DRAFTS_ENABLED):' not in src
        assert "Low confidence — please double-check before sending" not in src
        # Greeting branch must skip chip/draft, not call _create_greeting_suggestion inline.
        assert "skipping draft and chip" in src

    def test_frontend_hides_empty_greeting_draft_box(self):
        fe = Path(ROOT).parent / "Jarvis-bot-frontend/src/lib/whatsapp-categories.js"
        text = fe.read_text(encoding="utf-8")
        assert 'resolveCategory(suggestion) === "greeting"' in text
        assert "!suggestion?.draft_text" in text
