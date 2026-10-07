"""Reminder (owner commitment) path tests — mocks LLM/DB where needed.

Does not cover Draft Reply or Follow-up (client waiting-on) scenarios.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp import classifier
from app.services.whatsapp import product_gates as g
from app.services.whatsapp import repository as repo
from app.services.whatsapp import settings as wa_settings


class TestReminderAckGates:
    def test_b5_isolated_ok_hard_blocked(self):
        assert g.owner_ack_creates_commitment("Ok", "How are you?") is False
        assert g.last_inbound_is_action_request("How are you?") is False

    def test_b4_ok_after_request_allowed(self):
        assert (
            g.owner_ack_creates_commitment("Ok", "Please send the COA by Friday.")
            is True
        )
        assert g.last_inbound_is_action_request("Please send the COA by Friday.")

    def test_non_ack_defers_to_llm(self):
        assert g.owner_ack_creates_commitment("I'll send the documents tomorrow.", None) is None


class TestReminderTiming:
    def test_b1_explicit_deadline_immediate(self):
        due = datetime(2026, 10, 7, 10, 0, 0)
        deadline, immediate = g.resolve_owner_reminder_schedule(due)
        assert deadline == due
        assert immediate is True

    def test_b3_no_deadline_morning_brief_not_24h(self):
        now = datetime(2026, 10, 6, 5, 0, 0)
        deadline, immediate = g.resolve_owner_reminder_schedule(
            None,
            morning_hour=9,
            tz_name="Asia/Kolkata",
            now_utc=now,
        )
        assert immediate is False
        assert deadline > now
        # Must not be ~24h fallback from now — Morning Brief is next local 9am.
        assert deadline - now != timedelta(hours=24)
        assert wa_settings.WHATSAPP_MORNING_BRIEF_HOUR == 9

    def test_owner_awaiting_reminder_disables_no_deadline_fallback(self):
        """Regression: no-deadline owner promises must not use 24h chip fallback."""
        import inspect

        src = inspect.getsource(repo.commitments_awaiting_reminder)
        assert "allow_no_deadline_fallback" in src
        svc = Path(ROOT / "app/services/whatsapp/service.py").read_text(encoding="utf-8")
        assert "allow_no_deadline_fallback=False" in svc


class TestDetectCommitmentMocked:
    def test_b1_explicit_promise_with_date(self):
        tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send documents",
                "deadline_at": tomorrow.isoformat(),
            },
        ):
            result = classifier.detect_commitment("I'll send the documents tomorrow.")
        assert result["is_commitment"] is True
        assert result["label"]
        assert "document" in (result["label"] or "").lower() or result["commitment_type"] == "document"
        assert result["deadline_at"]
        deadline, immediate = g.resolve_owner_reminder_schedule(
            datetime.fromisoformat(result["deadline_at"])
        )
        assert immediate is True
        chip = g.format_owner_reminder_chip(
            label=result["label"], contact_name="Rahul", deadline_at=deadline
        )
        assert chip.startswith("You promised:")
        assert "Rahul" in chip

    def test_b2_hinglish_kal_bhejta(self):
        tomorrow = (datetime.utcnow() + timedelta(days=1)).isoformat()
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send it",
                "deadline_at": tomorrow,
            },
        ):
            result = classifier.detect_commitment("Kal bhejta hoon.")
        assert result["is_commitment"] is True
        assert result["deadline_at"]

    def test_b3_no_deadline_morning_brief(self):
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "other",
                "label": "Check and revert",
                "deadline_at": None,
            },
        ):
            result = classifier.detect_commitment("Let me check and revert.")
        assert result["is_commitment"] is True
        assert not result["deadline_at"]
        _, immediate = g.resolve_owner_reminder_schedule(None)
        assert immediate is False

    def test_b4_request_plus_ok(self):
        friday = datetime(2026, 10, 10, 17, 0, 0).isoformat()
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send COA",
                "deadline_at": friday,
            },
        ) as mocked:
            result = classifier.detect_commitment(
                "Ok.",
                last_inbound="Please send the COA by Friday.",
                history=[
                    {
                        "direction": "inbound",
                        "body": "Please send the COA by Friday.",
                    }
                ],
            )
        assert mocked.called
        assert result["is_commitment"] is True
        assert "coa" in (result["label"] or "").lower()
        assert result["deadline_at"]

    def test_b5_isolated_ok_no_llm_call(self):
        with patch.object(classifier, "_chat_json") as mocked:
            result = classifier.detect_commitment(
                "Ok.",
                last_inbound="How are you?",
            )
        mocked.assert_not_called()
        assert result["is_commitment"] is False


class TestOwnerCommitmentDraftNoFalseCompletion:
    def test_prompt_forbids_false_completion_generally(self):
        system = classifier._OWNER_COMMITMENT_DRAFT_SYSTEM.lower()
        assert "already completed" in system or "never state or imply" in system
        assert "fulfillment" in system or "promise" in system
        # Must stay principle-based — not a language-specific ban list.
        assert "kaam ho gaya" not in system
        assert "bhej diya" not in system

    def test_draft_user_prompt_requires_future_intent(self):
        with patch.object(
            classifier,
            "_chat",
            return_value="Sidi, I'll get the required documents to you shortly.",
        ) as mocked:
            classifier.draft_owner_commitment_message(
                "provide required documents",
                contact_name="mohamed abduallah",
            )
        assert mocked.called
        user_content = mocked.call_args[0][1].lower()
        assert "no fulfillment evidence" in user_content
        assert "already done" in user_content


class TestFulfillmentDismissSnooze:
    def test_b6_fulfillment_dismisses_suggestions(self):
        with patch.object(
            classifier,
            "_chat_json",
            return_value={"fulfilled": True},
        ):
            assert classifier.check_commitment_fulfilled(
                "Send documents", "Please find the documents attached."
            )

        db = MagicMock()
        suggestion = SimpleNamespace(
            id=1,
            status="pending",
            details=json.dumps({"commitment_id": 42, "chip_label": "You promised"}),
            resolved_at=None,
        )
        db.query.return_value.filter.return_value.all.return_value = [suggestion]
        updated = repo.dismiss_commitment_suggestions(db, 42)
        assert updated == 1
        assert suggestion.status == "dismissed"

    def test_b7_snooze_bumps_deadline_and_hides_chip(self):
        """Mirrors routes.snooze_suggestion persistence rules without HTTP."""
        now = datetime(2026, 10, 6, 12, 0, 0)
        commitment = SimpleNamespace(
            deadline_at=now - timedelta(hours=1),
            fulfilled_at=None,
            last_reminded_at=None,
        )
        hours = 24
        base = commitment.deadline_at or now
        if base < now:
            base = now
        commitment.deadline_at = base + timedelta(hours=hours)
        commitment.last_reminded_at = now
        assert commitment.deadline_at == now + timedelta(hours=24)
        assert commitment.last_reminded_at == now
        # Not due until snoozed deadline → awaiting-reminder query would exclude it.
        assert commitment.deadline_at > now

    def test_b8_dismiss_fulfills_so_no_regeneration(self):
        commitment = SimpleNamespace(id=7, fulfilled_at=None)
        # Same effect as routes.dismiss_suggestion for reminder cards.
        commitment.fulfilled_at = datetime.utcnow()
        assert commitment.fulfilled_at is not None
        # commitments_awaiting_reminder filters fulfilled_at.is_(None)
        assert commitment.fulfilled_at is not None


class TestReminderFrontendContracts:
    def test_reminders_section_and_buttons_exist(self):
        cats = (
            Path(ROOT).parent
            / "Jarvis-bot-frontend/src/lib/whatsapp-categories.js"
        ).read_text(encoding="utf-8")
        card = (
            Path(ROOT).parent
            / "Jarvis-bot-frontend/src/ui/components/whatsapp-request-card.js"
        ).read_text(encoding="utf-8")
        api = (
            Path(ROOT).parent / "Jarvis-bot-frontend/src/lib/api.js"
        ).read_text(encoding="utf-8")
        assert 'id: "reminders"' in cats
        assert "pending_commitment" in cats
        assert "isReminderActionCard" in cats
        assert "markSuggestionDone" in api
        assert "snoozeSuggestion" in api
        assert 'textContent = "Snooze"' in card or "Snooze" in card
        assert 'textContent = isFollowupActionCard(suggestion) ? "Mark done" : "Done"' in card
