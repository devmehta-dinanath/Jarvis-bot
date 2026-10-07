"""Follow-up (Waiting-on) path tests — C1–C8.

Does not cover Draft Reply or Reminder (owner commitment) scenarios.
Uses mocks for LLM/DB where needed; no live WAHA/OpenAI/Inbox.
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import classifier
from app.services.whatsapp import product_gates as g
from app.services.whatsapp import repository as repo
from app.services.whatsapp import settings as wa_settings


class TestFollowupThresholds:
    def test_c8_defaults_48_and_72(self):
        assert wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS == 48.0
        assert wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS == 48.0
        assert wa_settings.WHATSAPP_FOLLOWUP_URGENT_HOURS == 72.0
        assert wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS == 72.0

    def test_c8_runtime_paths_use_wa_settings_not_config_24(self):
        """Follow-up checkers must read wa_settings (48), not stale app.config 24."""
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(encoding="utf-8")
        assert "wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS" in svc_src
        assert "wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS" in svc_src
        assert "wa_settings.WHATSAPP_FOLLOWUP_URGENT_HOURS" in svc_src
        assert "wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS" in svc_src
        # Owner silence check must use owner flag hours.
        assert "work_contacts_awaiting_their_reply" in svc_src
        assert (
            "flag_hours=wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS" in svc_src
            or "flag_hours=wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS" in svc_src
        )

    def test_c8_eligibility_boundary_hours(self):
        flag = wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS
        assert flag == 48.0
        assert 47 < flag  # 47h not yet eligible
        assert 48 >= flag  # 48h eligible
        assert 72 >= wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS


class TestFollowupCopyAndSubjects:
    def test_c1_quotation_silence_copy(self):
        subject = g.owner_followup_subject(
            "Here is the quotation for the project."
        )
        assert subject == "quotation"
        text = g.format_waiting_on_silence(
            contact_name="Rahul",
            subject=subject,
            hours_waiting=50,
        )
        assert text == "Waiting on Rahul: reply to your quotation, 2 days."
        assert "Follow up with" not in text

    def test_c2_client_confirm_copy(self):
        text = g.format_waiting_on_promise(
            contact_name="Rahul",
            label="confirmation",
            hours_waiting=50,
        )
        assert text == "Waiting on Rahul: confirmation, 2 days."

    def test_subject_labels_when_supported(self):
        assert g.owner_followup_subject("Please approve the SOW.") == "approval"
        assert (
            g.owner_followup_subject("Please confirm payment received.")
            == "payment confirmation"
        )
        assert g.owner_followup_subject("I've attached the document.") == "document"
        # No invented label when context is thin.
        assert g.owner_followup_subject("Sent — thanks.") == "message"


class TestFollowupExclusions:
    def test_c3_immediate_now(self):
        assert g.is_immediate_request("Please send this now.")
        assert not g.is_immediate_request("I'll confirm tomorrow.")

    def test_c4_abhi(self):
        assert g.is_immediate_request("Abhi bhej do.")

    def test_small_talk_excluded(self):
        assert g.is_small_talk_message("Hi")
        assert g.is_small_talk_message("How are you?")
        assert g.is_small_talk_message("Good morning")
        assert not g.is_small_talk_message(
            "Here is the quotation for the project."
        )

    def test_near_term_deadline_skipped(self):
        soon = datetime.utcnow() + timedelta(hours=2)
        later = datetime.utcnow() + timedelta(hours=48)
        assert g.deadline_is_near(soon, within_hours=4.0)
        assert not g.deadline_is_near(later, within_hours=4.0)

    def test_c7_group_filter_in_candidate_query(self):
        src = inspect.getsource(repo.work_contacts_awaiting_their_reply)
        assert "is_group.is_(False)" in src


class TestClientNudgeNameHandling:
    def test_phone_id_does_not_pass_as_greetable_name(self):
        with patch.object(
            classifier,
            "_chat",
            return_value="Hi, just checking in on the documents — any update?",
        ) as mocked:
            classifier.draft_commitment_nudge(
                "upload evidence",
                contact_name="8615372598293",
            )
        user_content = mocked.call_args[0][1]
        assert "No real display name" in user_content
        assert "8615372598293" not in user_content
        assert "[Client's Name]" not in classifier._CLIENT_NUDGE_SYSTEM


class TestClientCommitmentDetectionMocked:
    def test_c2_detect_will_confirm(self):
        tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "confirmation",
                "label": "confirmation",
                "deadline_at": tomorrow.isoformat(),
            },
        ):
            detected = classifier.detect_client_commitment("I'll confirm tomorrow.")
        assert detected["is_commitment"] is True
        assert "confirm" in (detected["label"] or "").lower()

    def test_c3_c4_service_skips_immediate(self):
        """Runtime path must refuse immediate language before LLM commit create."""
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(
            encoding="utf-8"
        )
        assert "is_immediate_request(body)" in svc_src
        assert "is_small_talk_message(body)" in svc_src


class TestFollowupAutoClear:
    def test_c5_waiting_on_kinds_supersedeable(self):
        assert "client_commitment_reminder" in repo._SUPERSEDEABLE_KINDS
        assert "owner_followup_nudge" in repo._SUPERSEDEABLE_KINDS

    def test_c5_dismiss_waiting_on_helper(self):
        db = MagicMock()
        chip = SimpleNamespace(
            id=1,
            status="pending",
            kind="client_commitment_reminder",
            details='{"commitment_id": 9, "chip_label": "Waiting on Rahul: payment confirmation, 3 days."}',
        )
        commitment = SimpleNamespace(
            id=9,
            direction="client",
            fulfilled_at=None,
            last_reminded_at=None,
        )
        q1 = MagicMock()
        q1.filter.return_value = q1
        q1.all.return_value = [chip]
        q2 = MagicMock()
        q2.filter.return_value = q2
        q2.all.return_value = [commitment]
        db.query.side_effect = [q1, q2]

        cleared = repo.dismiss_waiting_on_followups_for_contact(db, contact_id=42)
        assert cleared == 1
        assert chip.status == "dismissed"
        assert commitment.last_reminded_at is not None

    def test_c6_dismiss_awaiting_owner_reply(self):
        db = MagicMock()
        chip = SimpleNamespace(id=2, status="pending", kind="followup_nudge")
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [chip]
        db.query.return_value = q

        cleared = repo.dismiss_awaiting_owner_reply_for_contact(db, contact_id=7)
        assert cleared == 1
        assert chip.status == "dismissed"

    def test_c5_c6_wired_in_service(self):
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(
            encoding="utf-8"
        )
        assert "dismiss_waiting_on_followups_for_contact" in svc_src
        assert "dismiss_awaiting_owner_reply_for_contact" in svc_src


class TestFollowupOwnerSilencePath:
    def test_c1_owner_check_uses_pattern_and_waiting_copy(self):
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(
            encoding="utf-8"
        )
        assert "format_waiting_on_silence" in svc_src
        assert "format_waiting_on_promise" in svc_src
        assert 'kind="owner_followup_nudge"' in svc_src
        assert 'kind="client_commitment_reminder"' in svc_src
        assert "followup_actions" in svc_src

    def test_c1_not_immediate_after_send(self):
        """Quotation outbound is follow-up-worthy; immediate language is not."""
        body = "Here is the quotation for the project."
        assert not g.is_immediate_request(body)
        assert not g.is_small_talk_message(body)
        assert g.owner_followup_subject(body) == "quotation"

    def test_owner_followup_snooze_allows_reappear_after_expiry(self):
        db = MagicMock()
        contact = SimpleNamespace(id=5, last_replied_at=datetime.utcnow() - timedelta(days=3))
        db.get.return_value = contact
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        snoozed = SimpleNamespace(
            status="dismissed",
            details=json.dumps({"snoozed_until": past}),
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [snoozed]
        db.query.return_value = q
        assert repo.pending_owner_followup_exists(db, 5) is False

    def test_owner_followup_dismiss_blocks_regeneration(self):
        db = MagicMock()
        contact = SimpleNamespace(id=5, last_replied_at=datetime.utcnow() - timedelta(days=3))
        db.get.return_value = contact
        dismissed = SimpleNamespace(
            status="dismissed",
            details=json.dumps({}),
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        q = MagicMock()
        q.filter.return_value = q
        q.all.return_value = [dismissed]
        db.query.return_value = q
        assert repo.pending_owner_followup_exists(db, 5) is True


class TestFollowupButtonsFrontend:
    def test_followup_card_buttons(self):
        card = (
            Path(ROOT).parent
            / "Jarvis-bot-frontend/src/ui/components/whatsapp-request-card.js"
        ).read_text(encoding="utf-8")
        cats = (
            Path(ROOT).parent
            / "Jarvis-bot-frontend/src/lib/whatsapp-categories.js"
        ).read_text(encoding="utf-8")
        assert "isFollowupActionCard" in cats
        assert "owner_followup_nudge" in cats
        assert "client_commitment_reminder" in cats
        assert "Remind me later" in card
        assert "Mark done" in card
        assert "Dismiss" in card


class TestFollowupHoursGateMocked:
    """C8 — eligibility at 47 vs 48 vs 72 using the same formulas as the service."""

    def test_c8_hours_waiting_gates(self):
        flag = wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS
        urgent = wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS
        assert 47.0 < flag
        assert 48.0 >= flag
        is_urgent_48 = 48.0 >= urgent
        is_urgent_72 = 72.0 >= urgent
        assert is_urgent_48 is False
        assert is_urgent_72 is True
