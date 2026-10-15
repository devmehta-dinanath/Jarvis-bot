"""Own WhatsApp messages must not be stored as inbound client messages."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.whatsapp.waha_client import payload_is_from_me
from app.services.whatsapp.webhook import _waha_peer_chat_id


def test_received_message_is_not_from_me():
    body = {
        "fromMe": False,
        "from": "919800000000@c.us",
        "body": "Please send the invoice",
    }
    with patch(
        "app.services.whatsapp.waha_client.session_self_ids",
        return_value={"919811111111"},
    ):
        assert payload_is_from_me(body) is False


def test_nested_from_me_is_outbound():
    body = {
        "from": "919800000000@c.us",
        "body": "I'll send it tomorrow",
        "_data": {"id": {"fromMe": True, "_serialized": "true_919800000000@c.us_ABC"}},
    }
    assert payload_is_from_me(body) is True


def test_true_prefix_message_id_is_outbound():
    body = {
        "id": "true_919800000000@c.us_3EB0",
        "from": "919800000000@c.us",
        "to": "919811111111@c.us",
        "body": "Here is the quotation",
    }
    assert payload_is_from_me(body) is True


def test_own_number_as_sender_is_outbound():
    body = {"from": "919811111111@c.us", "chatId": "919800000000@c.us", "body": "Ok"}
    with patch(
        "app.services.whatsapp.waha_client.session_self_ids",
        return_value={"919811111111"},
    ):
        assert payload_is_from_me(body) is True


def test_own_message_is_filed_under_the_other_person():
    body = {
        "id": "true_919800000000@c.us_3EB0",
        "from": "919811111111@c.us",
        "to": "919800000000@c.us",
        "chatId": "919800000000@c.us",
        "body": "Sent from my phone",
    }
    assert _waha_peer_chat_id(body) == "919800000000@c.us"
