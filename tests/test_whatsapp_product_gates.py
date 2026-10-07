"""Focused unit tests for Draft / Reminder / Follow-up product gates.

These do not call OpenAI. Run with:
  python3 -m pytest tests/test_whatsapp_product_gates.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import product_gates as g


class TestDraftFactGates:
    def test_a2_stock_missing_needs_clarify(self):
        assert g.looks_like_owner_fact_question("When will the stock arrive?")
        assert g.should_clarify_missing_owner_fact(
            "When will the stock arrive?",
            [{"direction": "inbound", "body": "Hi, checking on our order"}],
        )
        # Availability questions must also clarify (I2) — not only ETA phrasing.
        assert g.looks_like_owner_fact_question("Do you have this item in stock?")
        assert g.should_clarify_missing_owner_fact(
            "Do you have this item in stock?",
            [{"direction": "inbound", "body": "Hi, checking on our order"}],
        )

    def test_a3_stock_available_no_clarify(self):
        history = [
            {"direction": "outbound", "body": "Stock will arrive on Friday."},
            {"direction": "inbound", "body": "Thanks"},
        ]
        assert not g.should_clarify_missing_owner_fact(
            "When will the stock arrive?", history
        )

    def test_a4_festival_detected(self):
        assert g.is_greeting_or_festival("Happy Diwali!")
        assert g.is_greeting_or_festival("Hi")

    def test_invoice_question_not_owner_fact_gate(self):
        # "send invoice again" is a document request — not an ETA/price invent risk.
        assert not g.looks_like_owner_fact_question("Can you send the invoice again?")


class TestReminderHelpers:
    def test_reminder_copy(self):
        label = g.format_owner_reminder_chip(
            label="Send documents",
            contact_name="Rahul",
            deadline_at=None,
        )
        assert label.startswith("You promised: send documents to Rahul")

    def test_morning_brief_in_future(self):
        now = datetime(2026, 10, 6, 5, 0, 0)  # UTC
        nxt = g.next_morning_brief_utc(hour=9, tz_name="Asia/Kolkata", now_utc=now)
        assert nxt > now

    def test_bare_ack(self):
        assert g.is_bare_ack("Ok")
        assert g.is_bare_ack("Sure")
        assert not g.is_bare_ack("Ok I'll send the COA")


class TestUsableContactDisplayName:
    def test_phone_and_jid_not_usable_for_greeting(self):
        assert g.usable_contact_display_name("8615372598293") is None
        assert g.usable_contact_display_name("+91 98765 43210") is None
        assert g.usable_contact_display_name("919876543210@c.us") is None
        assert g.usable_contact_display_name("[Client's Name]") is None

    def test_real_name_usable(self):
        assert g.usable_contact_display_name("Rahul") == "Rahul"
        assert g.usable_contact_display_name("Dr. Bichara Somi") == "Dr. Bichara Somi"


class TestFollowupHelpers:
    def test_waiting_on_silence_copy(self):
        text = g.format_waiting_on_silence(
            contact_name="Rahul",
            subject="quotation",
            hours_waiting=50,
        )
        assert text == "Waiting on Rahul: reply to your quotation, 2 days."

    def test_waiting_on_promise_copy(self):
        text = g.format_waiting_on_promise(
            contact_name="Rahul",
            label="Payment confirmation",
            hours_waiting=72,
        )
        assert text == "Waiting on Rahul: payment confirmation, 3 days."

    def test_c3_c4_immediate(self):
        assert g.is_immediate_request("Please send this now.")
        assert g.is_immediate_request("Abhi bhej do.")
        assert not g.is_immediate_request("I'll confirm tomorrow.")

    def test_near_deadline(self):
        soon = datetime.utcnow() + timedelta(hours=2)
        later = datetime.utcnow() + timedelta(hours=48)
        assert g.deadline_is_near(soon, within_hours=4)
        assert not g.deadline_is_near(later, within_hours=4)

    def test_owner_followup_subject(self):
        assert g.owner_followup_subject("Here is the quotation for the project.") == "quotation"
        assert g.owner_followup_subject("Any update on pricing?") == "pricing"
        assert g.owner_followup_subject("Can you confirm the specs?") == "question"


class TestSettingsDefaults:
    def test_followup_defaults_48(self):
        from app.services.whatsapp import settings as s

        assert s.WHATSAPP_FOLLOWUP_FLAG_HOURS == 48.0
        assert s.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS == 48.0
        assert s.WHATSAPP_FOLLOWUP_URGENT_HOURS == 72.0
        assert s.WHATSAPP_MORNING_BRIEF_HOUR == 9
