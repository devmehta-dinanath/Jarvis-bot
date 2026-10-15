"""Speaker, ownership, and deadline rules for Friday Sir and Lotus."""

from __future__ import annotations

import re
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
from app.services.whatsapp import repository as repo
from app.services.whatsapp import settings as wa_settings
from app.services.whatsapp.prompts import _history_block
from app.services.whatsapp.service import WhatsAppService, _draft_reply_with_budget

PSUR = "I will arrange the PSUR for Tramadol 50 mg."
FAST = "Please make it fast"
AGAIN = "Again"
LOTUS_FINAL = "Revised agent details attached. These are final."
LOTUS_ASK = "Please let me know the handover date after you contact the new agent."
INVENTED_NINE = "2026-10-15T09:00:00"


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _contact(db, wa_id: str, name: str) -> models.WhatsAppContact:
    contact = models.WhatsAppContact(wa_id=wa_id, profile_name=name)
    db.add(contact)
    db.flush()
    return contact


def _message(db, contact, *, direction: str, body: str, when: datetime) -> models.WhatsAppMessage:
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        direction=direction,
        body=body,
        timestamp=when,
        msg_type="text",
    )
    db.add(message)
    db.flush()
    return message


def _service() -> WhatsAppService:
    return WhatsAppService.__new__(WhatsAppService)


def test_history_records_the_speaker():
    assert g.speaker_for_direction("outbound") == "owner"
    assert g.speaker_for_direction("inbound") == "client"
    block = _history_block(
        [
            {"direction": "outbound", "speaker": "owner", "body": PSUR},
            {"direction": "inbound", "speaker": "client", "body": FAST},
        ]
    )
    assert "Me: I will arrange the PSUR" in block
    assert "Client: Please make it fast" in block
    assert "Me: Please make it fast" not in block


def test_similar_turns_are_not_owner_promises():
    assert g.client_states_own_promise("Please send the updated COA.") is False
    assert g.client_states_own_promise("Ok") is False
    assert g.client_states_own_promise(FAST) is False
    assert g.client_states_own_promise(AGAIN) is False
    assert g.client_states_own_promise("I'll confirm the estimate tomorrow.") is True
    assert g.states_future_promise(PSUR) is True
    assert g.supported_commitment_deadline(PSUR, INVENTED_NINE) is None
    assert g.resolve_owner_reminder_schedule(None) == (None, False)
    assert g.inbound_supports_reply_draft(FAST) is False
    assert g.inbound_supports_reply_draft(AGAIN) is False
    assert g.inbound_supports_reply_draft("Please resend the invoice.") is True


def test_friday_sir_promise_stays_on_the_owner_message():
    db = _session()
    contact = _contact(db, "919800000001", "Friday Sir")
    start = datetime.utcnow() - timedelta(hours=5)
    promise = _message(db, contact, direction="outbound", body=PSUR, when=start)
    ok = _message(db, contact, direction="inbound", body="Ok", when=start + timedelta(minutes=2))
    hurry = _message(db, contact, direction="inbound", body=FAST, when=start + timedelta(minutes=4))
    db.commit()

    service = _service()
    with patch.object(
        classifier,
        "_chat_json",
        return_value={
            "is_commitment": True,
            "commitment_type": "other",
            "label": "Arrange the PSUR for Tramadol 50 mg",
            "deadline_at": INVENTED_NINE,
        },
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service._analyze_commitment_unsafe(db, promise)
        service._analyze_client_commitment_unsafe(db, ok, ok.body)
        service._analyze_client_commitment_unsafe(db, hurry, hurry.body)
    db.commit()

    rows = db.query(models.WhatsAppCommitment).all()
    assert len(rows) == 1
    assert rows[0].direction == "owner"
    assert rows[0].message_id == promise.id
    assert rows[0].deadline_at is None
    assert "Tramadol" in rows[0].label or "PSUR" in rows[0].label
    cards = db.query(models.WhatsAppSuggestion).all()
    assert len(cards) == 1
    assert cards[0].kind == "commitment_reminder"
    assert cards[0].message_id == promise.id
    assert cards[0].message_id != hurry.id
    assert cards[0].draft_text is None
    assert "09:00" not in (cards[0].details or "")
    with patch("app.services.whatsapp.service.classifier.draft_reply") as draft:
        assert _draft_reply_with_budget(
            [{"direction": "outbound", "speaker": "owner", "body": PSUR}],
            FAST,
            "follow_up",
            result={"decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []}},
        ) is None
    draft.assert_not_called()


def test_lotus_again_is_not_a_commitment_and_the_ask_stays_a_followup():
    db = _session()
    contact = _contact(db, "919800000002", "Lotus")
    start = datetime.utcnow() - timedelta(hours=6)
    final = _message(db, contact, direction="outbound", body=LOTUS_FINAL, when=start)
    again = _message(
        db, contact, direction="inbound", body=AGAIN, when=start + timedelta(minutes=3)
    )
    ask = _message(
        db,
        contact,
        direction="outbound",
        body=LOTUS_ASK,
        when=start + timedelta(minutes=8),
    )
    db.commit()

    assert re.search(wa_settings.OWNER_FOLLOWUP_BODY_RE, LOTUS_ASK)
    assert g.states_future_promise(LOTUS_ASK) is False
    assert g.states_future_promise(LOTUS_FINAL) is False

    service = _service()
    with patch.object(
        classifier,
        "_chat_json",
        return_value={
            "is_commitment": True,
            "commitment_type": "other",
            "label": "Follow up",
            "deadline_at": INVENTED_NINE,
        },
    ), patch(
        "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
        return_value=[],
    ):
        service._analyze_commitment_unsafe(db, final)
        service._analyze_client_commitment_unsafe(db, again, again.body)
        service._analyze_commitment_unsafe(db, ask)
    db.commit()

    assert db.query(models.WhatsAppCommitment).count() == 0
    assert db.query(models.WhatsAppSuggestion).count() == 0
    with patch("app.services.whatsapp.service.classifier.draft_reply") as draft:
        assert _draft_reply_with_budget([], AGAIN, "other", result={
            "decision": {"reply_allowed": True, "schema_valid": True, "missing_facts": []}
        }) is None
    draft.assert_not_called()


def test_owner_commitment_sourced_from_the_client_is_dropped():
    db = _session()
    contact = _contact(db, "919800000003", "Friday Sir")
    hurry = _message(
        db,
        contact,
        direction="inbound",
        body=FAST,
        when=datetime.utcnow(),
    )
    commitment = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=hurry.id,
        commitment_type="other",
        label="Make it fast",
        direction="owner",
        deadline_at=datetime(2026, 10, 15, 3, 30),
    )
    repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=hurry.id,
        kind="commitment_reminder",
        category="pending_commitment",
        priority="high",
        lane="work",
        draft_text=None,
        details={"commitment_id": commitment.id, "chip_label": "You promised: make it fast, due 9:00 am."},
    )
    db.commit()
    assert repo.dismiss_unsupported_owner_commitments(db) == 1
    db.commit()
    card = db.query(models.WhatsAppSuggestion).one()
    assert card.status == "dismissed"


def test_answered_client_message_is_not_shown_on_any_chat():
    """The wording does not matter. A client turn the owner already answered is not the card."""
    db = _session()
    contact = _contact(db, "919800000005", "Kris")
    promise = _message(
        db,
        contact,
        direction="outbound",
        body="I will send the specification.",
        when=datetime(2026, 10, 14, 11, 0),
    )
    client_reply = _message(
        db,
        contact,
        direction="inbound",
        body="Can you send the updated specification when you can?",
        when=datetime(2026, 10, 14, 12, 0),
    )
    _message(
        db,
        contact,
        direction="outbound",
        body="Yes, I will.",
        when=datetime(2026, 10, 14, 12, 5),
    )
    commitment = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="send the specification",
        direction="owner",
        deadline_at=None,
    )
    repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=client_reply.id,
        kind="commitment_reminder",
        category="pending_commitment",
        priority="high",
        lane="work",
        draft_text="I will send it shortly.",
        details={"commitment_id": commitment.id, "chip_label": "You promised: send the specification to Kris."},
    )
    reply = repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=client_reply.id,
        kind="reply",
        category="other",
        priority="normal",
        lane="work",
        draft_text="Sure, sending it.",
    )
    db.commit()
    assert repo.dismiss_suggestions_already_answered(db) == 2
    db.commit()
    reminder = db.query(models.WhatsAppSuggestion).filter_by(kind="commitment_reminder").one()
    assert reminder.status == "pending"
    assert reminder.message_id == promise.id
    assert reminder.draft_text is None
    db.refresh(reply)
    assert reply.status == "dismissed"


def test_friday_sir_card_drops_the_hurry_quote_and_the_invented_time():
    assert g.owner_reminder_still_supported(FAST, "arrange PSUR") is False
    db = _session()
    contact = _contact(db, "919800000004", "Friday Sir")
    promise = _message(
        db,
        contact,
        direction="outbound",
        body="Sir i will arrange the PSUR to",
        when=datetime(2026, 10, 14, 11, 17),
    )
    hurry = _message(
        db,
        contact,
        direction="inbound",
        body=FAST,
        when=datetime(2026, 10, 14, 12, 11),
    )
    commitment = repo.create_commitment(
        db,
        contact_id=contact.id,
        message_id=promise.id,
        commitment_type="other",
        label="arrange PSUR",
        direction="owner",
        deadline_at=datetime(2026, 10, 15, 3, 30),
    )
    repo.create_suggestion(
        db,
        contact_id=contact.id,
        message_id=hurry.id,
        kind="commitment_reminder",
        category="pending_commitment",
        priority="high",
        lane="work",
        draft_text="I will arrange it by 9:00 am.",
        details={
            "commitment_id": commitment.id,
            "chip_label": "You promised: arrange PSUR to Friday Sir, due today 9:00 am.",
        },
    )
    db.commit()
    assert repo.dismiss_unsupported_owner_commitments(db) == 1
    db.commit()
    card = db.query(models.WhatsAppSuggestion).one()
    assert card.status == "pending"
    assert card.message_id == promise.id
    assert card.draft_text is None
    assert "9:00" not in (card.details or "")
    assert "due" not in (card.details or "")
    db.refresh(commitment)
    assert commitment.deadline_at is None
    assert commitment.fulfilled_at is None

