"""Product integration smoke tests — Draft × Reminder × Follow-up isolation (I1–I10).

Verification-first: exercises classifier + WhatsAppService commitment/follow-up paths
with mocked LLM/DB. Does not call live WAHA, OpenAI, or Inbox UI.
"""

from __future__ import annotations

import inspect
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
from app.services.whatsapp.service import WhatsAppService


def _classify_payload(**overrides):
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
        "summary": "test",
        "personal_tone": False,
        "needs_clarification": False,
        "clarifying_question": None,
        "clarifying_options": None,
        "confidence": 92,
    }
    base.update(overrides)
    return base


def _svc() -> WhatsAppService:
    return WhatsAppService.__new__(WhatsAppService)


# ---------------------------------------------------------------------------
# Config path documentation (runtime sources)
# ---------------------------------------------------------------------------


class TestConfigIsolation:
    def test_followup_creation_uses_wa_settings_48_72(self):
        assert wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS == 48.0
        assert wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS == 48.0
        assert wa_settings.WHATSAPP_FOLLOWUP_URGENT_HOURS == 72.0
        assert wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS == 72.0
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(
            encoding="utf-8"
        )
        assert "flag_hours=wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS" in svc_src
        assert "flag_hours=wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS" in svc_src
        assert "wa_settings.WHATSAPP_FOLLOWUP_URGENT_HOURS" in svc_src
        assert "wa_settings.WHATSAPP_OWNER_FOLLOWUP_URGENT_HOURS" in svc_src

    def test_config_py_24_only_affects_followup_priority_helper(self):
        """app.config default 24 must not drive Waiting-on creation."""
        from app.config import WHATSAPP_FOLLOWUP_FLAG_HOURS as config_flag

        priority_src = inspect.getsource(WhatsAppService._followup_priority)
        assert "WHATSAPP_FOLLOWUP_FLAG_HOURS" in priority_src
        owner_src = inspect.getsource(WhatsAppService._check_owner_outbound_followups)
        client_src = inspect.getsource(WhatsAppService._check_pending_client_commitments)
        assert "wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS" in owner_src
        assert "wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS" in client_src
        assert isinstance(config_flag, float)

    def test_reminder_morning_brief_uses_wa_settings(self):
        assert wa_settings.WHATSAPP_MORNING_BRIEF_HOUR == 9
        commit_src = inspect.getsource(WhatsAppService._analyze_commitment_unsafe)
        assert "wa_settings.WHATSAPP_MORNING_BRIEF_HOUR" in commit_src


# ---------------------------------------------------------------------------
# I1 — Invoice / document request → Draft only
# ---------------------------------------------------------------------------


class TestI1InvoiceDraftOnly:
    def test_i1_classify_allows_draft_no_clarify(self):
        history = [
            {"direction": "outbound", "body": "Please find invoice INV-1042 attached."},
            {"direction": "inbound", "body": "Got it"},
        ]
        body = "Can you send me the invoice?"
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_classify_payload(
                category="document",
                document_type="invoice",
                summary="Client asks for the invoice",
                confidence=94,
            ),
        ):
            result = classifier.classify_message(history, body)

        assert result["is_important"] is True
        assert result["needs_clarification"] is False
        assert result["confidence"] >= 90
        assert not g.should_clarify_missing_owner_fact(body, history)
        assert not g.is_immediate_request(body)

    def test_i1_no_client_commitment_from_request(self):
        body = "Can you send me the invoice?"
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": False,
                "commitment_type": None,
                "label": None,
                "deadline_at": None,
            },
        ):
            client = classifier.detect_client_commitment(body)
        assert client["is_commitment"] is False
        assert not g.is_bare_ack(body)


# ---------------------------------------------------------------------------
# I2 — Missing stock → clarify, no draft invent
# ---------------------------------------------------------------------------


class TestI2MissingStockClarify:
    def test_i2_stock_missing_forces_clarification(self):
        history = [{"direction": "inbound", "body": "Hi, checking on our order"}]
        body = "Do you have this item in stock?"
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_classify_payload(
                category="shipment",
                document_type=None,
                shipment_status=None,
                summary="Stock availability question",
                needs_clarification=False,
                confidence=88,
            ),
        ):
            result = classifier.classify_message(history, body)

        assert result["needs_clarification"] is True
        assert result["clarifying_question"]
        assert result["confidence"] is not None and result["confidence"] <= 60
        svc_src = inspect.getsource(WhatsAppService._classify_one_unsafe)
        assert 'result.get("needs_clarification")' in svc_src
        assert "_create_clarification_suggestion" in svc_src


# ---------------------------------------------------------------------------
# I3 — Owner promise → Reminder only
# ---------------------------------------------------------------------------


class TestI3OwnerPromiseReminder:
    def test_i3_kal_bhejta_creates_owner_reminder_not_followup(self):
        tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        svc = _svc()
        db = MagicMock()
        message = SimpleNamespace(
            id=11,
            contact_id=3,
            body="Kal bhejta hoon.",
            classified_at=None,
        )
        contact = SimpleNamespace(profile_name="Rahul", wa_id="9198")
        created = []

        def _create_commitment(db, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(
                id=100,
                contact_id=kwargs["contact_id"],
                message_id=kwargs["message_id"],
                commitment_type=kwargs["commitment_type"],
                label=kwargs["label"],
                direction=kwargs["direction"],
                deadline_at=kwargs["deadline_at"],
                contact=contact,
            )

        with (
            patch.object(repo, "dismiss_awaiting_owner_reply_for_contact", return_value=0),
            patch.object(repo, "pending_commitment_for_contact", return_value=None),
            patch.object(
                repo,
                "live_chat_context",
                return_value=[
                    {"direction": "inbound", "body": "Please send me the COA."}
                ],
            ),
            patch.object(repo, "create_commitment", side_effect=_create_commitment),
            patch.object(
                classifier,
                "detect_commitment",
                return_value={
                    "is_commitment": True,
                    "commitment_type": "document",
                    "label": "Send COA",
                    "deadline_at": tomorrow.isoformat(),
                },
            ),
            patch(
                "app.services.whatsapp.service.actions.create_commitment_reminder",
                return_value={"id": "evt1", "htmlLink": None, "reminder_at": None},
            ),
            patch.object(svc, "_create_commitment_tracked_suggestion") as tracked,
        ):
            svc._analyze_commitment_unsafe(db, message)

        assert len(created) == 1
        assert created[0]["direction"] == "owner"
        assert created[0]["deadline_at"] is not None
        tracked.assert_called_once()
        assert all(c["direction"] == "owner" for c in created)


# ---------------------------------------------------------------------------
# I4 — Client promise → Follow-up commitment, no owner Reminder
# ---------------------------------------------------------------------------


class TestI4ClientPromiseFollowup:
    def test_i4_client_confirm_creates_client_direction_only(self):
        tomorrow = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        svc = _svc()
        db = MagicMock()
        message = SimpleNamespace(id=22, contact_id=4, body="I'll confirm tomorrow.")
        created = []

        def _create_commitment(db, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(
                id=200,
                contact_id=kwargs["contact_id"],
                message_id=kwargs["message_id"],
                commitment_type=kwargs["commitment_type"],
                label=kwargs["label"],
                direction=kwargs["direction"],
                deadline_at=kwargs["deadline_at"],
                contact=SimpleNamespace(profile_name="Rahul", wa_id="1"),
            )

        with (
            patch.object(repo, "pending_commitment_for_contact", return_value=None),
            patch.object(repo, "create_commitment", side_effect=_create_commitment),
            patch.object(
                classifier,
                "detect_client_commitment",
                return_value={
                    "is_commitment": True,
                    "commitment_type": "confirmation",
                    "label": "confirmation",
                    "deadline_at": tomorrow.isoformat(),
                },
            ),
            patch(
                "app.services.whatsapp.service.actions.create_commitment_reminder",
                return_value=None,
            ),
            patch.object(svc, "_create_commitment_tracked_suggestion") as tracked,
        ):
            svc._analyze_client_commitment_unsafe(db, message, message.body)

        assert len(created) == 1
        assert created[0]["direction"] == "client"
        assert "confirm" in created[0]["label"].lower()
        if tracked.called:
            args = tracked.call_args
            commitment_arg = args[0][1] if len(args[0]) > 1 else args.kwargs.get(
                "commitment"
            )
            assert commitment_arg.direction == "client"

    def test_i4_client_path_does_not_call_owner_detect(self):
        svc_src = inspect.getsource(WhatsAppService._analyze_client_commitment_unsafe)
        assert "detect_client_commitment" in svc_src
        # Strip client detect name so a leftover detect_commitment( would show up.
        stripped = svc_src.replace("detect_client_commitment", "")
        assert "detect_commitment(" not in stripped


# ---------------------------------------------------------------------------
# I5 — Client request + Owner Ok → Reminder
# ---------------------------------------------------------------------------


class TestI5OwnerOkAfterRequest:
    def test_i5_ok_after_coa_request_is_reminder(self):
        friday = datetime(2026, 10, 10, 17, 0, 0)
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send COA",
                "deadline_at": friday.isoformat(),
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
        deadline, immediate = g.resolve_owner_reminder_schedule(friday)
        assert immediate is True
        assert deadline == friday
        assert (
            g.owner_ack_creates_commitment("Ok.", "Please send the COA by Friday.")
            is True
        )

    def test_i5_isolated_ok_not_reminder(self):
        with patch.object(classifier, "_chat_json") as mocked:
            result = classifier.detect_commitment("Ok.", last_inbound="How are you?")
        mocked.assert_not_called()
        assert result["is_commitment"] is False


# ---------------------------------------------------------------------------
# I6 — Festival / small talk → nothing
# ---------------------------------------------------------------------------


class TestI6SmallTalkNothing:
    def test_i6_festival_classify_no_draft_no_commitment(self):
        with patch.object(
            classifier,
            "_chat_json",
            return_value=_classify_payload(
                is_important=False,
                category="greeting",
                document_type=None,
                summary="Festival greeting",
                confidence=90,
                personal_tone=True,
            ),
        ):
            result = classifier.classify_message([], "Happy Diwali! 😊")

        assert result["category"] == "greeting"
        assert result["is_important"] is False
        assert result["needs_clarification"] is False
        assert g.is_greeting_or_festival("Happy Diwali!")
        svc_src = inspect.getsource(WhatsAppService._classify_one_unsafe)
        assert 'category == "greeting"' in svc_src
        assert "skipping draft and chip" in svc_src

    def test_i6_owner_greeting_not_quotation_followup(self):
        assert g.is_greeting_or_festival("Happy Diwali!")
        assert g.owner_followup_subject("Happy Diwali!") != "quotation"


# ---------------------------------------------------------------------------
# I7 — Quotation silence → Follow-up after 48h only
# ---------------------------------------------------------------------------


class TestI7QuotationSilenceFollowup:
    def test_i7_copy_and_threshold(self):
        body = "Here is the quotation for the project."
        subject = g.owner_followup_subject(body)
        assert subject == "quotation"
        chip = g.format_waiting_on_silence(
            contact_name="Rahul", subject=subject, hours_waiting=50
        )
        assert chip == "Waiting on Rahul: reply to your quotation, 2 days."
        assert "Follow up with" not in chip
        flag = wa_settings.WHATSAPP_OWNER_FOLLOWUP_FLAG_HOURS
        assert 47.0 < flag
        assert 48.0 >= flag

    def test_i7_owner_check_gates(self):
        owner_src = inspect.getsource(WhatsAppService._check_owner_outbound_followups)
        assert "format_waiting_on_silence" in owner_src
        assert "is_small_talk_message" in owner_src
        assert "is_immediate_request" in owner_src
        assert 'kind="owner_followup_nudge"' in owner_src


# ---------------------------------------------------------------------------
# I8 — Client reply clears Waiting-on
# ---------------------------------------------------------------------------


class TestI8ClientReplyClearsFollowup:
    def test_i8_waiting_on_cleared_on_inbound(self):
        db = MagicMock()
        chip = SimpleNamespace(
            id=1,
            status="pending",
            kind="owner_followup_nudge",
            details=json.dumps(
                {
                    "chip_label": "Waiting on Rahul: reply to your quotation, 2 days.",
                }
            ),
        )
        q1 = MagicMock()
        q1.filter.return_value = q1
        q1.all.return_value = [chip]
        q2 = MagicMock()
        q2.filter.return_value = q2
        q2.all.return_value = []
        db.query.side_effect = [q1, q2]

        cleared = repo.dismiss_waiting_on_followups_for_contact(db, contact_id=9)
        assert cleared == 1
        assert chip.status == "dismissed"

    def test_i8_wired_in_classify_path(self):
        svc_src = inspect.getsource(WhatsAppService._classify_one_unsafe)
        assert "dismiss_waiting_on_followups_for_contact" in svc_src
        assert "client_commitment_reminder" in repo._SUPERSEDEABLE_KINDS
        assert "owner_followup_nudge" in repo._SUPERSEDEABLE_KINDS


# ---------------------------------------------------------------------------
# I9 — Owner fulfills Reminder → clears, no accidental Follow-up from fulfill
# ---------------------------------------------------------------------------


class TestI9OwnerFulfillsReminder:
    def test_i9_fulfillment_clears_reminder_only(self):
        svc = _svc()
        db = MagicMock()
        pending = SimpleNamespace(
            id=55,
            label="Send COA",
            fulfilled_at=None,
            contact_id=8,
        )
        message = SimpleNamespace(
            id=33,
            contact_id=8,
            body="Please find the COA attached.",
            classified_at=None,
        )
        with (
            patch.object(repo, "dismiss_awaiting_owner_reply_for_contact", return_value=0),
            patch.object(repo, "pending_commitment_for_contact", return_value=pending),
            patch.object(
                classifier, "check_commitment_fulfilled", return_value=True
            ) as fulfilled,
            patch.object(repo, "fulfill_commitment") as fulfill,
            patch.object(repo, "dismiss_commitment_suggestions") as dismiss,
            patch.object(repo, "create_commitment") as create,
            patch.object(classifier, "detect_commitment") as detect,
        ):
            svc._analyze_commitment_unsafe(db, message)

        fulfilled.assert_called_once()
        fulfill.assert_called_once_with(db, 55)
        dismiss.assert_called_once_with(db, 55)
        create.assert_not_called()
        detect.assert_not_called()


# ---------------------------------------------------------------------------
# I10 — Immediate request → no delayed Follow-up
# ---------------------------------------------------------------------------


class TestI10ImmediateNoDelayedFollowup:
    def test_i10_now_and_abhi_blocked(self):
        assert g.is_immediate_request("Please send this now.")
        assert g.is_immediate_request("Abhi bhej do.")

        svc = _svc()
        db = MagicMock()
        message = SimpleNamespace(id=44, contact_id=5, body="Please send this now.")
        with (
            patch.object(repo, "pending_commitment_for_contact") as pending,
            patch.object(repo, "create_commitment") as create,
            patch.object(classifier, "detect_client_commitment") as detect,
        ):
            svc._analyze_client_commitment_unsafe(db, message, message.body)

        pending.assert_not_called()
        create.assert_not_called()
        detect.assert_not_called()

    def test_i10_immediate_gate_before_detect(self):
        client_src = inspect.getsource(
            WhatsAppService._analyze_client_commitment_unsafe
        )
        assert client_src.index("is_immediate_request") < client_src.index(
            "detect_client_commitment"
        )


# ---------------------------------------------------------------------------
# Cross-system isolation summary
# ---------------------------------------------------------------------------


class TestSystemsRemainIsolated:
    def test_inbound_classify_does_not_create_owner_reminder(self):
        classify_src = inspect.getsource(WhatsAppService._classify_one_unsafe)
        assert "_analyze_client_commitment" in classify_src
        assert "_analyze_commitment_unsafe" not in classify_src
        assert "_check_owner_outbound_followups" not in classify_src

    def test_owner_outbound_does_not_create_client_followup(self):
        owner_src = inspect.getsource(WhatsAppService._analyze_commitment_unsafe)
        assert 'direction="owner"' in owner_src or "direction='owner'" in owner_src
        assert "detect_client_commitment" not in owner_src

    def test_periodic_checkers_are_separate(self):
        svc_src = Path(ROOT / "app/services/whatsapp/service.py").read_text(
            encoding="utf-8"
        )
        assert "_check_pending_commitments()" in svc_src
        assert "_check_pending_client_commitments()" in svc_src
        assert "_check_owner_outbound_followups()" in svc_src
