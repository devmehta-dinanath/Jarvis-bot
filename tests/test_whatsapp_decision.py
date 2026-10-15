"""Phase 2 decision contract. Mocked model output only."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import classifier
from app.services.whatsapp import decision
from app.services.whatsapp import product_gates
from app.services.whatsapp.service import _draft_reply_with_budget


def _payload(**overrides):
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


class TestDecisionSchema:
    def test_valid_decision_allows_a_draft(self):
        with patch.object(classifier, "_chat_json", return_value=_payload(
            evidence=["Invoice INV-1042 was sent earlier"],
            missing_facts=[],
            reply_allowed=True,
            commitment=None,
        )):
            result = classifier.classify_message(
                [{"direction": "outbound", "body": "Please find invoice INV-1042 attached."}],
                "Can you send the invoice again?",
            )
        decision_row = result["decision"]
        assert decision_row["schema_valid"] is True
        assert decision_row["intent"] == "document"
        assert decision_row["reply_allowed"] is True
        assert decision_row["confidence"] == 92
        assert decision_row["evidence"] == ["Invoice INV-1042 was sent earlier"]
        assert decision_row["missing_facts"] == []
        assert decision_row["commitment"] is None
        assert result["needs_clarification"] is False

    def test_missing_optional_fields_keep_the_existing_draft_path(self):
        with patch.object(classifier, "_chat_json", return_value=_payload()):
            result = classifier.classify_message([], "Can you send the invoice again?")
        assert result["decision"]["schema_valid"] is True
        assert result["decision"]["reply_allowed"] is True
        assert result["decision"]["evidence"] == []

    def test_malformed_fields_block_the_draft(self):
        parsed = decision.build_decision(
            {
                "reply_allowed": "yes",
                "evidence": "the price is 14.50",
                "missing_facts": [12],
                "commitment": "send it",
            },
            intent="budget",
            is_important=True,
            safety_concern=False,
            needs_clarification=False,
            confidence=90,
        )
        assert parsed["schema_valid"] is False
        assert parsed["reply_allowed"] is False
        assert parsed["evidence"] == []
        assert parsed["commitment"] is None

    def test_missing_facts_block_even_if_the_model_allows_a_draft(self):
        with patch.object(classifier, "_chat_json", return_value=_payload(
            category="timeline",
            document_type=None,
            summary="Client wants a landing window",
            reply_allowed=True,
            missing_facts=["landing date"],
            evidence=[],
        )):
            result = classifier.classify_message(
                [],
                "Can you confirm the landing window we discussed?",
            )
        assert result["needs_clarification"] is False
        assert result["decision"]["reply_allowed"] is False
        assert result["decision"]["missing_facts"] == ["landing date"]

    def test_safety_blocks_a_model_request_to_draft(self):
        with patch.object(classifier, "_chat_json", return_value=_payload(
            category="other",
            document_type=None,
            safety_concern=True,
            reply_allowed=True,
            summary="Possible distress",
        )):
            result = classifier.classify_message([], "I am in danger")
        assert result["safety_concern"] is True
        assert result["decision"]["reply_allowed"] is False

    def test_non_dict_model_output_is_closed(self):
        parsed = decision.build_decision(
            None,
            intent="other",
            is_important=True,
            safety_concern=False,
            needs_clarification=False,
            confidence=None,
        )
        assert parsed["schema_valid"] is False
        assert parsed["reply_allowed"] is False

    def test_commitment_object_is_kept_only_when_it_has_a_label(self):
        valid = decision.build_decision(
            {"commitment": {"label": "Send the invoice", "deadline_at": "2026-10-20", "direction": "owner"}},
            intent="document",
            is_important=True,
            safety_concern=False,
            needs_clarification=False,
            confidence=88,
        )
        assert valid["schema_valid"] is True
        assert valid["commitment"]["label"] == "Send the invoice"
        assert valid["commitment"]["direction"] == "owner"
        invalid = decision.build_decision(
            {"commitment": {"label": "  ", "direction": "owner"}},
            intent="document",
            is_important=True,
            safety_concern=False,
            needs_clarification=False,
            confidence=88,
        )
        assert invalid["schema_valid"] is False
        assert invalid["commitment"] is None
        assert invalid["reply_allowed"] is False


class TestApproximateDates:
    def test_late_december_is_not_the_last_day(self):
        body = (
            "60days is involved before we get it here in sa it will be around late December"
        )
        assert classifier.stated_deadline_date(body, "2026-12-31") is None
        assert classifier.stated_deadline_date(body, "2026-12-15") is None

    def test_explicit_day_is_kept_and_a_different_day_is_not(self):
        assert classifier.stated_deadline_date("Please deliver by the 20th", "2026-12-20") == "2026-12-20"
        assert classifier.stated_deadline_date("Please deliver by the 20th", "2026-12-31") is None

    def test_relative_phrase_can_still_resolve(self):
        assert classifier.stated_deadline_date("Please send it by Friday", "2026-10-16") == "2026-10-16"
        assert classifier.stated_deadline_date("When will it be ready?", "2026-12-31") is None

    def test_extract_deadline_drops_a_rejected_date(self):
        body = "it will be around late December"
        with patch.object(
            classifier,
            "_chat_json",
            return_value={"deadline_label": "Delivery deadline", "date": "2026-12-31"},
        ):
            extracted = classifier.extract_deadline(body)
        assert extracted["date"] is None
        assert extracted["date_rejected"] is True


class TestExistingGatesStay:
    def test_stock_question_still_clarifies(self):
        with patch.object(classifier, "_chat_json", return_value=_payload(
            category="timeline",
            document_type=None,
            summary="Stock arrival",
            reply_allowed=True,
            confidence=95,
        )):
            result = classifier.classify_message([], "When will the stock arrive?")
        assert result["needs_clarification"] is True
        assert result["decision"]["reply_allowed"] is False
        assert product_gates.should_clarify_missing_owner_fact("When will the stock arrive?", [])

    def test_reply_allowed_false_does_not_use_the_fallback_template(self):
        with patch("app.services.whatsapp.service.wa_fallback.draft_reply") as fallback:
            text = _draft_reply_with_budget(
                [],
                "around late December",
                "timeline",
                result={"decision": {"reply_allowed": False}},
            )
        assert text is None
        fallback.assert_not_called()

    def test_allowed_draft_still_reaches_the_fallback_when_ai_drafts_are_off(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False), patch(
            "app.services.whatsapp.service.wa_fallback.draft_reply",
            return_value="Thanks for your message. I'll get back to you shortly.",
        ) as fallback:
            text = _draft_reply_with_budget(
                [],
                "Can you send the invoice again?",
                "other",
                result={"decision": {"reply_allowed": True}},
            )
        assert text == "Thanks for your message. I'll get back to you shortly."
        fallback.assert_called_once()

    def test_ok_after_a_request_is_still_forced_to_a_commitment(self):
        with patch.object(classifier, "_chat_json", return_value={"is_commitment": False}):
            forced = classifier.detect_commitment(
                "Ok",
                last_inbound="Please send the invoice",
            )
        assert forced["is_commitment"] is True
        with patch.object(classifier, "_chat_json") as chat:
            blocked = classifier.detect_commitment("Ok", last_inbound="How are you?")
        assert blocked["is_commitment"] is False
        chat.assert_not_called()
