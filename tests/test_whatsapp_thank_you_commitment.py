"""A bare thank-you must not become a payment promise or steal a reminder."""

from __future__ import annotations

import json
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
from app.services.whatsapp import classifier
from app.services.whatsapp.repository import (
    dismiss_unsupported_owner_commitments,
    quote_newer_inbound_on_pending_cards,
)
from app.services.whatsapp.service import WhatsAppService

_CONDITIONAL = (
    "Im working on more if weekend taak anything ill update you a date prior"
)
_BAD = {
    "is_commitment": True,
    "commitment_type": "other",
    "label": "update on payment status",
    "deadline_at": "2026-10-15T09:00:00+05:30",
}
_NO_COMMITMENT = {
    "is_commitment": False,
    "commitment_type": None,
    "label": None,
    "deadline_at": None,
}
_THREAD = [
    ("outbound", "Any updates on the payment"),
    ("outbound", "Hi Sir"),
    ("outbound", "Good Evening"),
    ("outbound", "Sir this week confirm will send across payment"),
    ("inbound", "Amount"),
    ("outbound", "Sir incoming payment, so need 1 day to confirm"),
    ("outbound", "Hello Sir"),
    ("outbound", "Done a transfer today"),
    ("outbound", _CONDITIONAL),
    ("inbound", "Pls"),
    ("inbound", "Thank you"),
]


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _detect(message: str, payload: dict | None = None, **kwargs):
    with patch.object(classifier, "_chat_json", return_value=payload or _BAD):
        return classifier.detect_commitment(message, **kwargs)


class TestThankYouDoesNotBecomeAPaymentPromise:
    def test_exact_thread_does_not_store_the_invented_promise(self):
        assert _detect(_CONDITIONAL)["is_commitment"] is False
        assert _detect("Any updates on the payment")["is_commitment"] is False
        assert _detect("Hi Sir")["is_commitment"] is False
        assert _detect("Done a transfer today")["is_commitment"] is False
        assert _detect("Sir incoming payment, so need 1 day to confirm")["is_commitment"] is False
        will_send = _detect("Sir this week confirm will send across payment")
        assert will_send["is_commitment"] is False
        assert will_send["deadline_at"] is None

        db = _session()
        contact = models.WhatsAppContact(
            wa_id="919800000777",
            profile_name="Chetan Patni Kaytross",
        )
        db.add(contact)
        db.flush()
        start = datetime.utcnow() - timedelta(days=2)
        rows = []
        for offset, (direction, body) in enumerate(_THREAD):
            rows.append(
                models.WhatsAppMessage(
                    contact_id=contact.id,
                    direction=direction,
                    body=body,
                    timestamp=start + timedelta(minutes=offset),
                )
            )
        db.add_all(rows)
        db.commit()
        service = WhatsAppService.__new__(WhatsAppService)
        with patch.object(classifier, "_chat_json", return_value=_BAD):
            for row in rows:
                if row.direction == "outbound":
                    service._analyze_commitment(db, row)
        assert db.query(models.WhatsAppCommitment).count() == 0
        pls = next(row for row in rows if row.body == "Pls")
        with patch(
            "app.services.whatsapp.service.classifier._chat_json",
            return_value={
                "is_important": True,
                "category": "payment",
                "safety_concern": False,
                "confidence": 95,
                "summary": "Client said please",
                "language": "English",
                "needs_clarification": False,
                "is_commitment": True,
                "label": "update on payment status",
            },
        ), patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value=(
                "Hello Chetan Sir, just wanted to let you know "
                "I'll update you on the payment status soon."
            ),
        ), patch(
            "app.services.whatsapp.service.classifier.detect_client_commitment",
            return_value=_BAD,
        ), patch(
            "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
            return_value=[],
        ), patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ):
            service.classify_message_now(db, pls)
        assert db.query(models.WhatsAppCommitment).count() == 0

        thanks = rows[-1]
        promise = next(row for row in rows if row.body == _CONDITIONAL)
        card = models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=promise.id,
            kind="commitment_reminder",
            category="pending_commitment",
            status="pending",
            draft_text="Following up on the transfer.",
        )
        db.add(card)
        db.commit()
        with patch(
            "app.services.whatsapp.service.classifier._chat_json",
            return_value={
                "is_important": True,
                "category": "payment",
                "safety_concern": False,
                "confidence": 95,
                "summary": "Client thanked the owner",
                "language": "English",
                "needs_clarification": False,
            },
        ), patch(
            "app.services.whatsapp.service.classifier.draft_reply",
            return_value=(
                "Hello Chetan Sir, just wanted to let you know "
                "I'll update you on the payment status soon."
            ),
        ) as drafted, patch(
            "app.services.whatsapp.service.classifier.detect_client_commitment",
            return_value=_NO_COMMITMENT,
        ), patch(
            "app.services.whatsapp.repository.waha_client.fetch_recent_messages",
            return_value=[],
        ), patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ):
            service.classify_message_now(db, thanks)
        drafted.assert_not_called()
        db.expire_all()
        stored = db.get(models.WhatsAppSuggestion, card.id)
        assert stored.message_id == promise.id
        assert stored.draft_text == "Following up on the transfer."
        assert "payment status soon" not in (stored.draft_text or "").lower()
        assert (
            db.query(models.WhatsAppSuggestion)
            .filter(
                models.WhatsAppSuggestion.message_id == thanks.id,
                models.WhatsAppSuggestion.kind == "commitment_reminder",
            )
            .count()
            == 0
        )

    def test_thank_you_after_a_completed_update_has_no_draft(self):
        history = [
            {"direction": "outbound", "body": "Done a transfer today"},
            {"direction": "inbound", "body": "Pls"},
        ]
        assert _detect("Done a transfer today")["is_commitment"] is False
        with patch.object(
            classifier,
            "_chat_json",
            return_value={
                "is_important": True,
                "category": "payment",
                "safety_concern": False,
                "confidence": 96,
                "language": "English",
                "summary": "Thanks",
                "needs_clarification": False,
            },
        ):
            result = classifier.classify_message(history, "Thank you")
        assert result["is_important"] is False
        assert result["category"] == "greeting"
        assert result["decision"]["reply_allowed"] is False

    def test_genuine_promise_and_explicit_deadline_still_stand(self):
        plain = _detect(
            "Let me check and revert.",
            {
                "is_commitment": True,
                "commitment_type": "other",
                "label": "Check and revert",
                "deadline_at": "2026-10-15T09:00:00+05:30",
            },
        )
        assert plain["is_commitment"] is True
        assert plain["label"] == "Check and revert"
        assert plain["deadline_at"] is None

        friday = _detect(
            "I'll send the documents by Friday.",
            {
                "is_commitment": True,
                "commitment_type": "document",
                "label": "Send documents",
                "deadline_at": "2026-10-16T17:00:00+05:30",
            },
        )
        assert friday["is_commitment"] is True
        assert friday["deadline_at"] == "2026-10-16T17:00:00+05:30"

        grounded = _detect(
            "Sir this week confirm will send across payment",
            {
                "is_commitment": True,
                "commitment_type": "other",
                "label": "Send payment",
                "deadline_at": "2026-10-15T09:00:00+05:30",
            },
        )
        assert grounded["is_commitment"] is True
        assert grounded["deadline_at"] is None

    def test_newer_thanks_does_not_retarget_a_commitment_card(self):
        db = _session()
        contact = models.WhatsAppContact(wa_id="919800000778", profile_name="Chetan")
        db.add(contact)
        db.flush()
        promise = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="outbound",
            body="I'll send the documents by Friday.",
            timestamp=datetime.utcnow() - timedelta(hours=2),
        )
        thanks = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="Thank you",
            timestamp=datetime.utcnow(),
        )
        reply_message = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="Please send the invoice.",
            timestamp=datetime.utcnow() - timedelta(hours=3),
        )
        db.add_all([reply_message, promise, thanks])
        db.flush()
        commitment = models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=promise.id,
            kind="commitment_reminder",
            category="pending_commitment",
            status="pending",
        )
        reply = models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=reply_message.id,
            kind="reply",
            category="document",
            status="pending",
        )
        db.add_all([commitment, reply])
        db.commit()
        assert quote_newer_inbound_on_pending_cards(db, thanks) == 1
        db.refresh(commitment)
        db.refresh(reply)
        assert commitment.message_id == promise.id
        assert reply.message_id == thanks.id

    def test_stored_conditional_commitment_is_dropped_instead_of_drafted(self):
        db = _session()
        contact = models.WhatsAppContact(
            wa_id="919800000779",
            profile_name="Chetan Patni Kaytross",
        )
        db.add(contact)
        db.flush()
        source = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="outbound",
            body=_CONDITIONAL,
            timestamp=datetime.utcnow() - timedelta(days=1),
        )
        thanks = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="inbound",
            body="Thank you",
            timestamp=datetime.utcnow(),
        )
        db.add_all([source, thanks])
        db.flush()
        commitment = models.WhatsAppCommitment(
            contact_id=contact.id,
            message_id=source.id,
            direction="owner",
            commitment_type="other",
            label="update on payment status",
            deadline_at=datetime.utcnow() - timedelta(hours=2),
        )
        db.add(commitment)
        db.flush()
        card = models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=thanks.id,
            kind="commitment_reminder",
            category="pending_commitment",
            status="pending",
            draft_text=(
                "Hello Chetan Sir, just wanted to let you know "
                "I'll update you on the payment status soon."
            ),
            details=json.dumps({"commitment_id": commitment.id}),
        )
        db.add(card)
        db.commit()
        assert dismiss_unsupported_owner_commitments(db) == 1
        db.commit()
        db.refresh(card)
        db.refresh(commitment)
        assert card.status == "dismissed"
        assert commitment.fulfilled_at is not None

        service = WhatsAppService.__new__(WhatsAppService)
        with patch(
            "app.services.whatsapp.service.SessionLocal",
            return_value=db,
        ), patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ), patch(
            "app.services.whatsapp.service.classifier.draft_owner_commitment_message",
            return_value="I'll update you on the payment status soon.",
        ) as drafted:
            service._check_pending_commitments()
        drafted.assert_not_called()

    def test_explicit_owner_commitment_still_reminds(self):
        db = _session()
        contact = models.WhatsAppContact(wa_id="919800000780", profile_name="Rahul")
        db.add(contact)
        db.flush()
        source = models.WhatsAppMessage(
            contact_id=contact.id,
            direction="outbound",
            body="I'll send the documents by Friday.",
            timestamp=datetime.utcnow() - timedelta(days=1),
        )
        db.add(source)
        db.flush()
        commitment = models.WhatsAppCommitment(
            contact_id=contact.id,
            message_id=source.id,
            direction="owner",
            commitment_type="document",
            label="Send documents",
            deadline_at=datetime.utcnow() - timedelta(hours=2),
        )
        db.add(commitment)
        db.flush()
        card = models.WhatsAppSuggestion(
            contact_id=contact.id,
            message_id=source.id,
            kind="commitment_reminder",
            category="pending_commitment",
            status="pending",
            details=json.dumps({"commitment_id": commitment.id}),
        )
        db.add(card)
        db.commit()
        assert dismiss_unsupported_owner_commitments(db) == 0
        service = WhatsAppService.__new__(WhatsAppService)
        safe = "I'll send the documents on Friday."
        db.close = lambda: None
        with patch(
            "app.services.whatsapp.service.SessionLocal",
            return_value=db,
        ), patch(
            "app.services.whatsapp.service.WHATSAPP_AI_DRAFTS_ENABLED",
            True,
        ), patch(
            "app.services.whatsapp.service.classifier.draft_owner_commitment_message",
            return_value=safe,
        ) as drafted, patch(
            "app.services.whatsapp.service.repo.list_instructions",
            return_value=[],
        ), patch(
            "app.services.whatsapp.service.repo.recent_outbound_examples",
            return_value=[],
        ):
            service._check_pending_commitments()
        drafted.assert_called_once()
        db.expire_all()
        stored = db.get(models.WhatsAppSuggestion, card.id)
        assert stored.status == "pending"
        assert stored.draft_text == safe
        assert db.get(models.WhatsAppCommitment, commitment.id).fulfilled_at is None
