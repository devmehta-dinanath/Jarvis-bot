"""Meeting confirmation requires a later agreement to a clock time."""

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
from app.services.whatsapp.classifier import WhatsAppAIError
from app.services.whatsapp.meeting_scope import (
    infer_mutual_meeting_confirmation,
    should_skip_meeting_inbox,
)
from app.services.whatsapp.service import WhatsAppService

_PROPOSAL = "Can we meet tomorrow at 3pm?"
_AGREEMENT = "Yes, 3pm tomorrow works."


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _message(db, body: str):
    contact = models.WhatsAppContact(wa_id="919800000077", profile_name="Boukhari")
    db.add(contact)
    db.flush()
    message = models.WhatsAppMessage(
        contact_id=contact.id,
        direction="inbound",
        body=body,
        timestamp=datetime.utcnow(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _store_meeting(db, body: str, history: list, extracted: dict | None):
    message = _message(db, body)
    service = WhatsAppService.__new__(WhatsAppService)
    result = {
        "category": "meeting",
        "decision": {"reply_allowed": False, "schema_valid": True, "missing_facts": []},
    }
    extract = (
        patch(
            "app.services.whatsapp.service.classifier.extract_meeting",
            side_effect=WhatsAppAIError("meeting extract down"),
        )
        if extracted is None
        else patch(
            "app.services.whatsapp.service.classifier.extract_meeting",
            return_value=extracted,
        )
    )
    with extract, patch(
        "app.services.whatsapp.service.wa_calendar.enrich_meeting_details",
        side_effect=lambda meeting: meeting,
    ), patch(
        "app.services.whatsapp.service.actions.maybe_auto_set_reminder",
        return_value=None,
    ):
        suggestion = service._create_suggestion(
            db, message, history, body, result, "normal"
        )
    assert suggestion is not None
    return json.loads(suggestion.details)


class TestMutualConfirmation:
    def test_genuine_agreement_is_confirmed(self):
        history = [
            {"direction": "outbound", "body": _PROPOSAL},
            {"direction": "inbound", "body": _AGREEMENT},
        ]
        assert infer_mutual_meeting_confirmation(history, "", {"confirmed": False}) is True
        db = _session()
        details = _store_meeting(
            db,
            _AGREEMENT,
            [{"direction": "outbound", "body": _PROPOSAL}],
            {
                "title": "Meeting with client",
                "start": None,
                "end": None,
                "confirmed": False,
                "is_meeting": True,
            },
        )
        assert details["confirmed"] is True

    def test_request_followed_by_ok_is_not_confirmation(self):
        assert (
            infer_mutual_meeting_confirmation(
                [
                    {"direction": "inbound", "body": _PROPOSAL},
                    {"direction": "outbound", "body": "Ok"},
                ],
                "",
                {"confirmed": False},
            )
            is False
        )
        assert (
            infer_mutual_meeting_confirmation(
                [
                    {"direction": "inbound", "body": "Are you free tomorrow?"},
                    {"direction": "outbound", "body": "Ok"},
                ],
                "",
                {"confirmed": False},
            )
            is False
        )
        assert (
            infer_mutual_meeting_confirmation(
                [
                    {"direction": "inbound", "body": "Please call me"},
                    {"direction": "outbound", "body": "Ok"},
                ],
                "",
                {"confirmed": False},
            )
            is False
        )

    def test_model_false_survives_a_call_phrase(self):
        history = [{"direction": "outbound", "body": "Ok"}]
        body = "Please call me about the price"
        meeting = {"confirmed": False}
        assert infer_mutual_meeting_confirmation(history, body, meeting) is False
        if infer_mutual_meeting_confirmation(history, body, meeting):
            meeting["confirmed"] = True
        assert meeting["confirmed"] is False
        db = _session()
        details = _store_meeting(
            db,
            body,
            history,
            {
                "title": "Meeting with client",
                "start": None,
                "end": None,
                "confirmed": False,
                "is_meeting": True,
            },
        )
        assert details["confirmed"] is False

    def test_connect_now_and_delay_only_stay_excluded(self):
        assert should_skip_meeting_inbox("Yes we can connect now") is True
        assert should_skip_meeting_inbox("allow me 1 hour") is True
        assert infer_mutual_meeting_confirmation([], "Yes we can connect now", None) is False
        assert infer_mutual_meeting_confirmation([], "allow me 1 hour", None) is False

    def test_tomorrow_fallback_is_not_mutual_agreement(self):
        db = _session()
        details = _store_meeting(db, "Can we talk tomorrow?", [], None)
        assert details["confirmed"] is False
        assert "add to Google Calendar" not in details["chip_label"]

    def test_price_message_with_call_is_not_a_confirmed_meeting(self):
        body = "What is the price? Please call me."
        assert infer_mutual_meeting_confirmation([], body, {"confirmed": False}) is False
        db = _session()
        details = _store_meeting(db, body, [], None)
        assert details["confirmed"] is False
