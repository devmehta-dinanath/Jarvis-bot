"""Client acceptance: drafts, reminders, and follow-ups stay separate decisions."""

from __future__ import annotations

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
from app.services.whatsapp import classifier, product_gates as g
from app.services.whatsapp.repository import (
    create_commitment,
    create_suggestion,
    dismiss_commitment_suggestions,
    dismiss_pending_older_than,
    dismiss_suggestions_already_answered,
    dismiss_superseded_pending_for_contact,
    list_suggestions,
)
from app.services.whatsapp.service import WhatsAppService, _draft_reply_with_budget

YEMEN = "Kindly update me about Yemen inspection."
CALL = "Ok but please just make a phone call to Rahul Desai sir."


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_invoice_resend_can_draft_when_the_invoice_is_already_in_the_thread():
    history = [{"direction": "outbound", "body": "Invoice attached for the last shipment."}]
    body = "Please resend the invoice."
    assert g.should_clarify_missing_owner_fact(body, history) is False
    assert g.should_clarify_missing_material_readiness(body, history) is False
    with patch(
        "app.services.whatsapp.service.classifier.draft_reply",
        return_value="Resending the invoice from our last message.",
    ), patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", True):
        text = _draft_reply_with_budget(
            history,
            body,
            "document",
            result={"decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []}},
        )
    assert text == "Resending the invoice from our last message."


def test_unknown_stock_date_asks_the_owner_instead_of_drafting():
    body = "When will the stock arrive?"
    assert g.should_clarify_missing_owner_fact(body, []) is True
    with patch("app.services.whatsapp.service.classifier.draft_reply") as draft:
        text = _draft_reply_with_budget(
            [],
            body,
            "shipment",
            result={
                "decision": {
                    "reply_allowed": False,
                    "schema_valid": True,
                    "missing_facts": ["stock arrival date"],
                }
            },
        )
    assert text is None
    draft.assert_not_called()


def test_festival_greeting_does_not_get_a_business_draft():
    assert g.is_greeting_or_festival("Happy Diwali!")
    with patch("app.services.whatsapp.service.classifier.draft_reply") as draft:
        text = _draft_reply_with_budget(
            [],
            "Happy Diwali!",
            "greeting",
            result={"decision": {"reply_allowed": False, "schema_valid": True}},
        )
    assert text is None
    draft.assert_not_called()


def test_new_unanswered_question_still_drafts_after_an_unrelated_owner_message():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919811111111", profile_name="Buyer")
    db.add(contact)
    db.flush()
    earlier = datetime.utcnow() - timedelta(hours=2)
    owner = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Good morning",
        timestamp=earlier,
    )
    question = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Please resend the invoice.",
        timestamp=earlier + timedelta(minutes=30),
    )
    db.add_all([owner, question])
    db.flush()
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=question.id,
            kind="reply",
            category="document",
            status="pending",
            draft_text="Resending the invoice.",
        )
    )
    db.commit()
    assert dismiss_suggestions_already_answered(db) == 0


def test_ok_sir_after_the_rahul_request_is_a_call_reminder_not_a_reply():
    history = [
        {"direction": "outbound", "body": YEMEN},
        {"direction": "inbound", "body": CALL},
    ]
    assert g.last_inbound_is_action_request(CALL)
    assert g.owner_ack_creates_commitment("Ok Sir.", CALL) is True
    label = g.accepted_action_label(CALL, history)
    assert label == "call Rahul Desai about the Yemen inspection"
    assert "Shiv" not in label

    with patch.object(
        classifier,
        "_chat_json",
        return_value={
            "is_commitment": False,
            "commitment_type": None,
            "label": None,
            "deadline_at": "2026-10-15T09:00:00",
        },
    ):
        detected = classifier.detect_commitment(
            "Ok Sir.",
            history=history,
            last_inbound=CALL,
        )
    assert detected["is_commitment"] is True
    assert detected["label"] == label
    assert detected["deadline_at"] is None

    db = _session()
    contact = models.WhatsAppContact(wa_id="919822222222", profile_name="Shiv Kumar Tiwari")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(minutes=10)
    owner_ask = models.WhatsAppMessage(
        contact_id=contact.id, direction="outbound", body=YEMEN, timestamp=start
    )
    client_ask = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body=CALL,
        timestamp=start + timedelta(minutes=1),
    )
    owner_ok = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Ok Sir.",
        timestamp=start + timedelta(minutes=2),
    )
    db.add_all([owner_ask, client_ask, owner_ok])
    db.flush()
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=client_ask.id,
            kind="reply",
            category="other",
            status="pending",
            draft_text="I will call Rahul and update you.",
        )
    )
    db.commit()

    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(
        classifier,
        "_chat_json",
        return_value={"is_commitment": False, "deadline_at": "2026-10-15T09:00:00"},
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service._analyze_commitment_unsafe(db, owner_ok)
    db.commit()
    assert dismiss_suggestions_already_answered(db) == 1

    commitment = (
        db.query(models.WhatsAppCommitment)
        .filter(models.WhatsAppCommitment.contact_id == contact.id)
        .one()
    )
    assert commitment.label == "call Rahul Desai about the Yemen inspection"
    assert "Shiv" not in commitment.label
    assert commitment.message_id == owner_ok.id
    assert commitment.deadline_at is None
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .all()
    )
    assert len(pending) == 1
    assert pending[0].kind == "commitment_reminder"
    assert pending[0].message_id == owner_ok.id
    assert pending[0].draft_text is None
    assert "09:00" not in (pending[0].details or "")
    assert "9:00" not in (pending[0].details or "")


def test_explicit_tomorrow_monday_and_friday_deadlines_are_kept():
    friday = "2026-10-16T17:00:00"
    assert (
        g.supported_commitment_deadline("I'll send the documents by Friday.", friday)
        == friday
    )
    tomorrow = "2026-10-16T18:00:00"
    assert g.supported_commitment_deadline("I'll send the documents tomorrow.", tomorrow) == tomorrow
    monday = "2026-10-19T11:00:00"
    assert g.supported_commitment_deadline("I'll call you on Monday.", monday) == monday
    assert g.supported_commitment_deadline("Ok Sir.", "2026-10-15T09:00:00") is None


def test_unrelated_owner_message_does_not_clear_the_reminder():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919833333333", profile_name="Shiv Kumar Tiwari")
    db.add(contact)
    db.flush()
    promised = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Ok Sir.",
        timestamp=datetime.utcnow() - timedelta(minutes=5),
    )
    later = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Good evening",
        timestamp=datetime.utcnow(),
    )
    db.add_all([promised, later])
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promised.id,
        commitment_type="other",
        label="call Rahul Desai about the Yemen inspection",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=promised.id,
            kind="commitment_reminder",
            category="pending_commitment",
            status="pending",
            draft_text=None,
        )
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(classifier, "check_commitment_fulfilled", return_value=True) as check, patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service._analyze_commitment_unsafe(db, later)
    db.commit()
    check.assert_not_called()
    db.refresh(commitment)
    assert commitment.fulfilled_at is None
    assert dismiss_suggestions_already_answered(db) == 0


def test_a_relevant_completion_clears_the_reminder():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919844444444", profile_name="Shiv Kumar Tiwari")
    db.add(contact)
    db.flush()
    promised = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Ok Sir.",
        timestamp=datetime.utcnow() - timedelta(minutes=5),
    )
    done = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Called Rahul Desai about the inspection.",
        timestamp=datetime.utcnow(),
    )
    db.add_all([promised, done])
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promised.id,
        commitment_type="other",
        label="call Rahul Desai about the Yemen inspection",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    with patch.object(classifier, "check_commitment_fulfilled", return_value=True), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service._analyze_commitment_unsafe(db, done)
    db.commit()
    db.refresh(commitment)
    assert commitment.fulfilled_at is not None


def test_unknown_payment_status_does_not_draft_an_overdue_apology():
    assert g.keyword_payment_status("Any update on the payment") is None
    assert g.keyword_payment_status("Incoming payment expected") is None
    assert g.keyword_payment_status("Payment is pending") is None
    assert g.keyword_payment_status("Payment sent today") == "received"
    assert g.keyword_payment_status("Payment received") == "received"
    assert g.keyword_payment_status("The invoice is overdue") == "overdue"
    with patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", False):
        text = _draft_reply_with_budget(
            [],
            "Any update on the payment",
            "payment",
            payment_status=None,
            result={"decision": {"reply_allowed": True, "schema_valid": True}},
        )
    assert text is None


def test_no_deadline_morning_brief_is_a_reminder_not_a_client_reply():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919855555555", profile_name="Shiv Kumar Tiwari")
    db.add(contact)
    db.flush()
    source = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=datetime.utcnow() - timedelta(days=1),
    )
    db.add(source)
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=source.id,
        commitment_type="other",
        label="Check and revert",
        direction="owner",
        deadline_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    db.close = lambda: None
    with patch(
        "app.services.whatsapp.service.SessionLocal",
        return_value=db,
    ), patch(
        "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
        True,
    ), patch(
        "app.services.whatsapp.service.classifier.draft_owner_commitment_message",
        return_value="I'll update you shortly.",
    ) as drafted, patch(
        "app.services.whatsapp.service.actions.send_reply",
    ) as sent:
        service._check_pending_commitments()
    drafted.assert_not_called()
    sent.assert_not_called()
    db.expire_all()
    card = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.contact_id == contact.id)
        .one()
    )
    assert card.kind == "commitment_reminder"
    assert card.status == "pending"
    assert card.draft_text is None
    assert "Check and revert" in (card.details or "")
    db.refresh(commitment)
    assert commitment.fulfilled_at is None


def test_unanswered_inbound_still_drafts_while_a_commitment_exists():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919866666666", profile_name="Buyer")
    db.add(contact)
    db.flush()
    source = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=datetime.utcnow() - timedelta(days=1),
    )
    db.add(source)
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=source.id,
        commitment_type="other",
        label="Check and revert",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(hours=8),
    )
    db.commit()
    history = [{"direction": "outbound", "body": "Invoice attached for the last shipment."}]
    with patch(
        "app.services.whatsapp.service.classifier.draft_reply",
        return_value="Resending the invoice from our last message.",
    ), patch("app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED", True):
        text = _draft_reply_with_budget(
            history,
            "Please resend the invoice.",
            "document",
            result={"decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []}},
        )
    assert text == "Resending the invoice from our last message."
    db.refresh(commitment)
    assert commitment.fulfilled_at is None
    assert commitment.label == "Check and revert"


def _pending_kinds(db, contact_id: int) -> dict[str, models.WhatsAppSuggestion]:
    rows = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact_id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .all()
    )
    return {row.kind: row for row in rows}


def test_reply_draft_and_owner_reminder_can_both_stay_pending():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919877777777", profile_name="Buyer")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(hours=2)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=start,
    )
    question = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Please resend the invoice.",
        timestamp=start + timedelta(minutes=30),
    )
    db.add_all([promise, question])
    db.flush()
    reply = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=question.id,
        kind="reply",
        category="document",
        draft_text="Resending the invoice from our last message.",
    )
    reminder = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text=None,
        details={"commitment_id": 41, "commitment_label": "Check and revert"},
    )
    db.commit()
    assert reminder.id != reply.id
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply", "commitment_reminder"}
    assert pending["reply"].draft_text == "Resending the invoice from our last message."
    assert pending["commitment_reminder"].draft_text is None
    listed, total = list_suggestions(
        db,
        status="pending",
        kind=None,
        lane=None,
        contact_id=contact.id,
        limit=20,
        offset=0,
    )
    assert total == 2
    assert {row.kind for row in listed} == {"reply", "commitment_reminder"}


def test_no_deadline_reminder_does_not_replace_a_pending_reply():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919888888888", profile_name="Buyer")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(days=1)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=start,
    )
    question = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Please resend the invoice.",
        timestamp=start + timedelta(hours=1),
    )
    db.add_all([promise, question])
    db.flush()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=question.id,
        kind="reply",
        category="document",
        draft_text="Resending the invoice from our last message.",
    )
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Check and revert",
        direction="owner",
        deadline_at=datetime.utcnow() - timedelta(hours=1),
    )
    db.commit()
    service = WhatsAppService.__new__(WhatsAppService)
    db.close = lambda: None
    with patch(
        "app.services.whatsapp.service.SessionLocal",
        return_value=db,
    ), patch(
        "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
        True,
    ), patch(
        "app.services.whatsapp.service.classifier.draft_owner_commitment_message",
        return_value="I'll update you shortly.",
    ) as drafted:
        service._check_pending_commitments()
    drafted.assert_not_called()
    db.expire_all()
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply", "commitment_reminder"}
    assert pending["reply"].draft_text == "Resending the invoice from our last message."
    assert pending["commitment_reminder"].draft_text is None
    assert pending["commitment_reminder"].message_id == promise.id
    db.refresh(commitment)
    assert commitment.fulfilled_at is None
    assert dismiss_superseded_pending_for_contact(db, contact.id, keep_id=pending["reply"].id) == 0
    assert set(_pending_kinds(db, contact.id)) == {"reply", "commitment_reminder"}


def test_dismissing_one_card_leaves_the_other():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919899999999", profile_name="Buyer")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(hours=3)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=start,
    )
    question = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Please resend the invoice.",
        timestamp=start + timedelta(hours=1),
    )
    db.add_all([promise, question])
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Check and revert",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    db.flush()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=question.id,
        kind="reply",
        category="document",
        draft_text="Resending the invoice from our last message.",
    )
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text=None,
        details={"commitment_id": commitment.id, "commitment_label": "Check and revert"},
    )
    db.commit()

    assert dismiss_commitment_suggestions(db, commitment.id) == 1
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply"}
    assert pending["reply"].draft_text == "Resending the invoice from our last message."

    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text=None,
        details={"commitment_label": "Check and revert"},
    )
    db.commit()
    reply = _pending_kinds(db, contact.id)["reply"]
    reply.status = "dismissed"
    db.commit()
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"commitment_reminder"}

    assert dismiss_pending_older_than(db, contact.id, datetime.utcnow()) == 0
    assert set(_pending_kinds(db, contact.id)) == {"commitment_reminder"}


def test_reminder_and_followup_both_stay_pending_for_one_chat():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919811100000", profile_name="Buyer")
    db.add(contact)
    db.flush()
    start = datetime.utcnow() - timedelta(days=2)
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Let me check and revert.",
        timestamp=start,
    )
    question = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Please resend the invoice.",
        timestamp=start + timedelta(hours=1),
    )
    db.add_all([promise, question])
    db.flush()
    commitment = create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="Check and revert",
        direction="owner",
        deadline_at=datetime.utcnow() + timedelta(days=1),
    )
    db.flush()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=question.id,
        kind="reply",
        category="document",
        draft_text="Resending the invoice from our last message.",
    )
    reminder = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text=None,
        details={"commitment_id": commitment.id, "commitment_label": "Check and revert"},
    )
    followup = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="owner_followup_nudge",
        category="follow_up",
        draft_text="Hi, just checking in on my last message.",
        details={"chip_label": "Waiting on Buyer: quotation.", "followup_actions": True},
    )
    db.commit()
    assert len({reminder.id, followup.id}) == 2
    listed, total = list_suggestions(
        db,
        status="pending",
        kind=None,
        lane=None,
        contact_id=contact.id,
        limit=20,
        offset=0,
    )
    assert total == 3
    assert {row.kind for row in listed} == {
        "reply",
        "commitment_reminder",
        "owner_followup_nudge",
    }

    newer = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="owner_followup_nudge",
        category="follow_up",
        draft_text="Any update when you get a chance?",
        details={"chip_label": "Waiting on Buyer: quotation.", "followup_actions": True},
    )
    db.commit()
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply", "commitment_reminder", "owner_followup_nudge"}
    assert pending["commitment_reminder"].id == reminder.id
    assert pending["owner_followup_nudge"].id == newer.id
    assert pending["reply"].draft_text == "Resending the invoice from our last message."

    from app.services.whatsapp.routes import dismiss_suggestion, snooze_suggestion

    dismiss_suggestion(pending["owner_followup_nudge"].id, db)
    db.expire_all()
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply", "commitment_reminder"}
    assert pending["commitment_reminder"].id == reminder.id
    db.refresh(commitment)
    assert commitment.fulfilled_at is None

    restored = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="owner_followup_nudge",
        category="follow_up",
        draft_text="Any update when you get a chance?",
        details={"chip_label": "Waiting on Buyer: quotation.", "followup_actions": True},
    )
    db.commit()
    snooze_suggestion(pending["commitment_reminder"].id, 24, db)
    db.expire_all()
    pending = _pending_kinds(db, contact.id)
    assert set(pending) == {"reply", "owner_followup_nudge"}
    assert pending["owner_followup_nudge"].id == restored.id
    assert pending["reply"].draft_text == "Resending the invoice from our last message."


def test_follow_up_waiting_period_is_unchanged():
    from app.services.whatsapp import settings as wa_settings

    assert wa_settings.WHATSAPP_FOLLOWUP_FLAG_HOURS == 48
    assert wa_settings.WHATSAPP_FOLLOWUP_URGENT_HOURS == 72
    assert g.is_immediate_request("Please send it abhi")
    assert g.is_small_talk_message("How are you?")
