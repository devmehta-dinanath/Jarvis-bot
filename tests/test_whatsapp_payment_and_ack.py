"""Unknown payment status stays unknown, and an ack is not a forced promise."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import classifier
from app.services.whatsapp import product_gates as g
from app.services.whatsapp.prompts import payment_reply_hint
from app.services.whatsapp.service import _draft_reply_with_budget, _payment_chip_label

_CONDITIONAL = (
    "Im working on more if weekend taak anything ill update you a date prior"
)


def _classify(message: str, payment_status):
    payload = {
        "is_important": True,
        "category": "payment",
        "safety_concern": False,
        "payment_status": payment_status,
        "confidence": 92,
        "language": "English",
        "summary": "Payment message",
        "needs_clarification": False,
    }
    with patch.object(classifier, "_chat_json", return_value=payload):
        return classifier.classify_message([], message)


def _detect(message: str, *, last_inbound: str | None = None, payload: dict | None = None):
    with patch.object(
        classifier,
        "_chat_json",
        return_value=payload
        or {
            "is_commitment": True,
            "commitment_type": "other",
            "label": "update on payment status",
            "deadline_at": "2026-10-15T09:00:00+05:30",
        },
    ):
        return classifier.detect_commitment(message, last_inbound=last_inbound)


class TestPaymentStatusStaysUnknown:
    def test_incoming_pending_sent_and_blank_are_not_overdue(self):
        cases = {
            "Sir incoming payment, so need 1 day to confirm": "incoming",
            "The payment is still pending": "pending",
            "Done a transfer today": "sent",
            "Can we talk about the payment?": None,
            "Can we talk about the payment? ": "",
            "Payment note": "unclassified",
        }
        for message, raw in cases.items():
            result = _classify(message, raw)
            assert result["payment_status"] is None, message
            assert not _payment_chip_label(result["payment_status"]).startswith("Payment overdue")
            hint = payment_reply_hint(result["payment_status"]).lower()
            assert "chasing a payment" not in hint
            assert "status is not established" in hint

    def test_received_and_overdue_are_preserved(self):
        received = _classify("I have made the payment", "received")
        overdue = _classify("This invoice is overdue", "overdue")
        assert received["payment_status"] == "received"
        assert "received" in _payment_chip_label("received").lower()
        assert overdue["payment_status"] == "overdue"
        assert "overdue" in _payment_chip_label("overdue").lower()

    def test_unknown_payment_fallback_does_not_apologise_for_a_delay(self):
        with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
            text = _draft_reply_with_budget(
                [],
                "Sir incoming payment, so need 1 day to confirm",
                "payment",
                payment_status=None,
                result={"decision": {"reply_allowed": True, "schema_valid": True}},
            )
        assert text is None


class TestAckDoesNotInventAnOwnerCommitment:
    def test_thanks_please_and_a_client_request_are_not_owner_promises(self):
        assert _detect("Thank you")["is_commitment"] is False
        assert _detect("Pls")["is_commitment"] is False
        assert _detect("Please send the invoice")["is_commitment"] is False
        assert _detect("Hi Sir")["is_commitment"] is False
        assert _detect(_CONDITIONAL)["is_commitment"] is False
        assert _detect("Done a transfer today")["is_commitment"] is False
        assert g.owner_ack_creates_commitment("Ok", "Thank you") is False
        assert g.owner_ack_creates_commitment("Ok", "Pls") is False
        assert _detect("Ok", last_inbound="Thank you")["is_commitment"] is False
        assert _detect("Ok", last_inbound="Pls")["is_commitment"] is False

    def test_owner_ok_after_a_real_request_is_still_a_commitment(self):
        with patch.object(classifier, "_chat_json", return_value={"is_commitment": False}):
            result = classifier.detect_commitment(
                "Ok",
                last_inbound="Please send the invoice by Friday.",
            )
        assert result["is_commitment"] is True
        assert g.owner_ack_creates_commitment("Ok", "Please send the COA by Friday.") is True

    def test_explicit_owner_promise_keeps_its_deadline(self):
        result = _detect(
            "I'll send the documents by Friday.",
            payload={
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send documents",
                "deadline_at": "2026-10-16T17:00:00+05:30",
            },
        )
        assert result["is_commitment"] is True
        assert result["deadline_at"] == "2026-10-16T17:00:00+05:30"
