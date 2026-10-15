"""Waiting-on owner nudges stay generic check-ins."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp.classifier import WhatsAppAIError
from app.services.whatsapp.service import (
    WhatsAppService,
    _OWNER_FOLLOWUP_CHECKIN,
    _owner_followup_draft,
)

_CHECKIN = "Hi, have you had a chance to review my last message?"


def _contact_and_outbound(body: str):
    sent = datetime.utcnow() - timedelta(hours=50)
    contact = SimpleNamespace(
        id=7,
        profile_name="Rahul",
        wa_id="919800000007",
        last_replied_at=sent,
    )
    outbound = SimpleNamespace(id=11, body=body, timestamp=sent)
    return contact, outbound


def _raise_nudge(body: str, draft):
    contact, outbound = _contact_and_outbound(body)
    db = MagicMock()
    created = {}

    def _capture(*_args, **kwargs):
        created.update(kwargs)
        return SimpleNamespace(**kwargs)

    service = WhatsAppService.__new__(WhatsAppService)
    with patch(
        "app.services.whatsapp.service.SessionLocal",
        return_value=db,
    ), patch(
        "app.services.whatsapp.service.repo.work_contacts_awaiting_their_reply",
        return_value=[(contact, outbound)],
    ), patch(
        "app.services.whatsapp.service.repo.list_instructions",
        return_value=[],
    ), patch(
        "app.services.whatsapp.service.repo.recent_outbound_examples",
        return_value=[],
    ), patch(
        "app.services.whatsapp.service.repo.live_chat_context",
        return_value=[],
    ), patch(
        "app.services.whatsapp.service.repo.create_suggestion",
        side_effect=_capture,
    ) as created_row, patch(
        "app.services.whatsapp.service.classifier.draft_reply",
        side_effect=draft if isinstance(draft, Exception) else None,
        return_value=None if isinstance(draft, Exception) else draft,
    ) as drafted:
        service._check_owner_outbound_followups()
    return created, created_row, drafted


class TestOwnerFollowupNudge:
    def test_normal_draft_stays_a_generic_checkin(self):
        created, created_row, drafted = _raise_nudge(
            "Here is the quotation for the project.",
            _CHECKIN,
        )
        drafted.assert_called_once()
        created_row.assert_called_once()
        assert created["draft_text"] == _CHECKIN
        assert created["kind"] == "owner_followup_nudge"
        assert created["priority"] == "high"
        assert created["details"]["chip_label"].startswith("Waiting on Rahul:")
        assert created["details"]["followup_actions"] is True
        assert 49 < created["details"]["hours_waiting"] < 51

    def test_model_failure_uses_the_generic_checkin(self):
        created, created_row, _drafted = _raise_nudge(
            "Here is the quotation for the project.",
            WhatsAppAIError("draft down"),
        )
        created_row.assert_called_once()
        assert created["draft_text"] == _OWNER_FOLLOWUP_CHECKIN
        assert "14.50" not in created["draft_text"]

    def test_unknown_price_or_delivery_date_is_not_restated(self):
        created, _row, _drafted = _raise_nudge(
            "Can you confirm the price? Delivery might be around late December.",
            "The price is Rs 14.50 and delivery is on 20 December.",
        )
        assert created["draft_text"] == _OWNER_FOLLOWUP_CHECKIN
        assert "14.50" not in created["draft_text"]
        assert "December" not in created["draft_text"]
        assert "Rs" not in created["draft_text"]

    def test_document_message_does_not_promise_the_file(self):
        created, _row, _drafted = _raise_nudge(
            "I've attached the invoice for your review.",
            "I'll send the invoice shortly.",
        )
        assert created["draft_text"] == _OWNER_FOLLOWUP_CHECKIN
        assert "send the invoice" not in created["draft_text"].lower()

    def test_draft_does_not_claim_payment_or_delivery_happened(self):
        created, _row, _drafted = _raise_nudge(
            "Please confirm the payment.",
            "Payment has been received and the order was delivered.",
        )
        assert created["draft_text"] == _OWNER_FOLLOWUP_CHECKIN
        lowered = created["draft_text"].lower()
        assert "payment has been" not in lowered
        assert "delivered" not in lowered

    def test_generic_checkin_and_harmless_may_are_kept(self):
        assert _OWNER_FOLLOWUP_CHECKIN == (
            "Hi, just checking in on my last message — any update when you get a chance?"
        )
        assert _owner_followup_draft(_OWNER_FOLLOWUP_CHECKIN) == _OWNER_FOLLOWUP_CHECKIN
        harmless = "You may have missed my last message — any update when you get a chance?"
        assert _owner_followup_draft(harmless) == harmless
        created, _row, _drafted = _raise_nudge(
            "Here is the quotation for the project.",
            harmless,
        )
        assert created["draft_text"] == harmless

    def test_explicit_date_price_file_and_completed_action_stay_blocked(self):
        quotation = "Here is the quotation for the project."
        for claimed in (
            "Checking in. Delivery on December 31.",
            "Checking in. Delivery on May 31.",
            "The price is Rs 14.50.",
            "I'll send the invoice shortly.",
            "Payment has been received and the order was delivered.",
        ):
            created, _row, _drafted = _raise_nudge(quotation, claimed)
            assert created["draft_text"] == _OWNER_FOLLOWUP_CHECKIN
            assert "December" not in created["draft_text"]
            assert "14.50" not in created["draft_text"]
            assert "send the invoice" not in created["draft_text"].lower()
            assert "delivered" not in created["draft_text"].lower()

    def test_immediate_request_still_creates_no_card(self):
        created, created_row, drafted = _raise_nudge(
            "Please send this now. Quotation attached.",
            _CHECKIN,
        )
        drafted.assert_not_called()
        created_row.assert_not_called()
        assert created == {}
