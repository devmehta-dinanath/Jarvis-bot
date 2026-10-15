"""Inbox cards from a different WhatsApp login stay hidden."""

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
from app.services.whatsapp import account_scope
from app.services.whatsapp.repository import list_suggestions


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _chat(db, *, name: str, wa_id: str, owner: str, body: str):
    contact = models.WhatsAppContact(wa_id=wa_id, profile_name=name)
    db.add(contact)
    db.flush()
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body=body,
            timestamp=datetime.utcnow(),
            raw_payload=json.dumps(
                {
                    "from": f"{wa_id}@c.us",
                    "to": f"{owner}@c.us",
                    "fromMe": False,
                    "body": body,
                }
            ),
        )
    )
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            kind="reply",
            category="follow_up",
            status="pending",
            draft_text="draft",
        )
    )
    db.commit()
    return contact


def _pending(db):
    items, _total = list_suggestions(
        db,
        status="pending",
        kind=None,
        lane=None,
        contact_id=None,
        limit=50,
        offset=0,
    )
    return [item.contact_id for item in items]


def test_other_login_hides_saved_chats_and_the_same_login_shows_them():
    db = _session()
    ahmed = _chat(
        db,
        name="Ahmed From Iraq Friend",
        wa_id="964700000001",
        owner="919800000000",
        body="Bro can you do the registration for these ? And whose company ?",
    )
    with patch(
        "app.services.whatsapp.waha_client.connected_account_key",
        return_value="918888888888",
    ):
        assert _pending(db) == []
    with patch(
        "app.services.whatsapp.waha_client.connected_account_key",
        return_value="919800000000",
    ):
        assert _pending(db) == [ahmed.id]


def test_same_account_keeps_history_when_payloads_have_no_owner():
    db = _session()
    contact = models.WhatsAppContact(wa_id="919811111111", profile_name="Rakesh")
    db.add(contact)
    db.flush()
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="Let me confirm tomorrow",
            timestamp=datetime.utcnow(),
            raw_payload=json.dumps({"body": "Let me confirm tomorrow"}),
        )
    )
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            kind="reply",
            category="client_commitment",
            status="pending",
        )
    )
    db.commit()
    with patch(
        "app.services.whatsapp.waha_client.connected_account_key",
        return_value="918888888888",
    ):
        assert _pending(db) == [contact.id]


def test_reply_card_updates_in_place_and_meeting_stays():
    from app.services.whatsapp.repository import create_suggestion

    db = _session()
    contact = models.WhatsAppContact(wa_id="919811111111", profile_name="Dev Mehta")
    db.add(contact)
    db.flush()
    first = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=None,
        kind="meeting",
        category="meeting",
        draft_text=None,
        details={"chip_label": "Meeting"},
    )
    second = create_suggestion(
        db,
        contact_id=contact.id,
        message_id=None,
        kind="reply",
        category="timeline",
        draft_text="I'll confirm the time.",
        details={"chip_label": "Timeline question"},
    )
    db.commit()
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id == contact.id,
            models.WhatsAppSuggestion.status == "pending",
        )
        .all()
    )
    assert second.id != first.id
    assert {row.category for row in pending} == {"meeting", "timeline"}


def test_reply_clears_chat_and_leaves_the_meeting():
    from datetime import timedelta

    from app.services.whatsapp.repository import create_suggestion, dismiss_pending_older_than

    db = _session()
    contact = models.WhatsAppContact(wa_id="919822222222", profile_name="Dev Mehta")
    db.add(contact)
    db.flush()
    earlier = datetime.utcnow() - timedelta(hours=1)
    db.add(
        models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="heloo call me",
            timestamp=earlier,
        )
    )
    db.flush()
    message = db.query(models.WhatsAppMessage).one()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=message.id,
        kind="meeting",
        category="meeting",
        details={"start": "2099-01-01T17:00:00+00:00"},
    )
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=message.id,
        kind="reply",
        category="timeline",
        draft_text="On it.",
    )
    db.commit()
    dismiss_pending_older_than(db, contact.id, datetime.utcnow())
    db.commit()
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .all()
    )
    assert [row.category for row in pending] == ["meeting"]


def test_past_meeting_is_removed():
    from app.services.whatsapp.repository import create_suggestion, dismiss_past_meetings

    db = _session()
    contact = models.WhatsAppContact(wa_id="919833333333", profile_name="Dev Mehta")
    db.add(contact)
    db.flush()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=None,
        kind="meeting",
        category="meeting",
        details={"start": "2000-01-01T17:00:00+00:00"},
    )
    db.commit()
    assert dismiss_past_meetings(db) == 1
    db.commit()
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .count()
    )
    assert pending == 0


def test_reply_after_a_promise_clears_the_draft_and_keeps_the_meeting():
    from datetime import timedelta

    from app.services.whatsapp.repository import dismiss_suggestions_already_answered

    db = _session()
    contact = models.WhatsAppContact(wa_id="919844444444", profile_name="Punit Surolia")
    db.add(contact)
    db.flush()
    thanked_at = datetime.utcnow() - timedelta(minutes=5)
    replied_at = thanked_at + timedelta(minutes=1)
    later_at = replied_at + timedelta(minutes=1)
    thank_you = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Thank you",
        timestamp=thanked_at,
    )
    reply = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="Trusted",
        timestamp=replied_at,
    )
    newer = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Can you confirm the other point?",
        timestamp=later_at,
    )
    db.add_all([thank_you, reply, newer])
    db.flush()
    db.add_all(
        [
            models.WhatsAppSuggestion(
                contact_id=contact.id,
                message_id=thank_you.id,
                kind="commitment_reminder",
                category="pending_commitment",
                status="pending",
                draft_text="I'll revert to confirm soon.",
            ),
            models.WhatsAppSuggestion(
                contact_id=contact.id,
                message_id=thank_you.id,
                kind="meeting",
                category="meeting",
                status="pending",
            ),
            models.WhatsAppSuggestion(
                contact_id=contact.id,
                message_id=reply.id,
                kind="owner_followup_nudge",
                category="follow_up",
                status="pending",
            ),
            models.WhatsAppSuggestion(
                contact_id=contact.id,
                message_id=newer.id,
                kind="reply",
                category="timeline",
                status="pending",
                draft_text="Yes, confirming that now.",
            ),
        ]
    )
    db.commit()

    assert dismiss_suggestions_already_answered(db) == 1
    db.commit()
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .all()
    )
    assert {row.kind for row in pending} == {"meeting", "owner_followup_nudge", "reply"}


def test_promise_card_stays_when_that_message_is_still_the_latest_reply():
    from app.services.whatsapp.repository import (
        create_suggestion,
        dismiss_suggestions_already_answered,
    )

    db = _session()
    contact = models.WhatsAppContact(wa_id="919855555555", profile_name="Punit Surolia")
    db.add(contact)
    db.flush()
    promise = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="outbound",
        body="I will revert to confirm by Friday",
        timestamp=datetime.utcnow(),
    )
    db.add(promise)
    db.flush()
    create_suggestion(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        kind="commitment_reminder",
        category="pending_commitment",
        draft_text="I'll revert to confirm soon.",
    )
    db.commit()

    assert dismiss_suggestions_already_answered(db) == 0
    pending = (
        db.query(models.WhatsAppSuggestion)
        .filter(models.WhatsAppSuggestion.status == "pending")
        .count()
    )
    assert pending == 1


def test_old_meeting_card_is_cleared_and_its_message_is_read_again():
    from app.services.whatsapp.repository import requeue_pending_meetings

    db = _session()
    contact = models.WhatsAppContact(wa_id="919866666666", profile_name="Santy")
    db.add(contact)
    db.flush()
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body="Rs. 14.50 Please close this as revised price confirm.",
        timestamp=datetime.utcnow(),
        classified_at=datetime.utcnow(),
        category="meeting",
        is_important=True,
    )
    db.add(message)
    db.flush()
    db.add(
        models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=message.id,
            kind="meeting",
            category="meeting",
            status="pending",
        )
    )
    db.commit()

    assert requeue_pending_meetings(db) == 1
    db.commit()
    db.refresh(message)
    suggestion = db.query(models.WhatsAppSuggestion).one()
    assert suggestion.status == "dismissed"
    assert message.classified_at is None
    assert message.category is None
    assert message.body.startswith("Rs. 14.50")


def test_owner_key_reads_recipient_on_incoming_message():
    assert (
        account_scope.owner_key_from_payload(
            {"from": "964700000001@c.us", "to": "919800000000@c.us", "fromMe": False}
        )
        == "919800000000"
    )
