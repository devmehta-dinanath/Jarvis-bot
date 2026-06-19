import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app import models
from app.config import WHATSAPP_VERIFY_TOKEN
from app.database import get_db
from app.services import service_manager
from app.services.whatsapp import actions
from app.services.whatsapp import client as wa_client
from app.services.whatsapp import repository as repo
from app.services.whatsapp import webhook as wa_webhook
from app.services.whatsapp.actions import WhatsAppActionError
from app.services.whatsapp.schemas import (
    AddToCalendarRequest,
    SendMessageRequest,
    SendReplyRequest,
    WhatsAppContactListResponse,
    WhatsAppContactResponse,
    WhatsAppMessageListResponse,
    WhatsAppMessageResponse,
    WhatsAppSendResult,
    WhatsAppSuggestionListResponse,
    WhatsAppSuggestionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp"])


# --- Webhook ---


@router.get("/webhook")
def verify_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    if mode == "subscribe" and token and token == WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=challenge or "")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webhook verification failed",
    )


@router.post("/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not wa_client.verify_signature(raw_body, signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        )

    logger.info("[WHATSAPP] Webhook received object=%s entries=%s", payload.get("object"), len(payload.get("entry", []) or []))

    try:
        stored = wa_webhook.process_webhook_payload(db, payload)
    except Exception:
        logger.exception("[WHATSAPP] Failed to process webhook payload")
        db.rollback()
        # Always 200 so Meta does not retry-storm; processing errors are logged.
        return {"received": True, "stored": 0}

    return {"received": True, "stored": stored}


# --- Read ---


@router.get("/contacts", response_model=WhatsAppContactListResponse)
def list_contacts(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> WhatsAppContactListResponse:
    items, total = repo.list_contacts(db, limit=limit, offset=offset)
    return WhatsAppContactListResponse(
        items=[_contact_response(c) for c in items],
        total=total,
    )


@router.get("/contacts/{wa_id}/messages", response_model=WhatsAppMessageListResponse)
def list_contact_messages(
    wa_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> WhatsAppMessageListResponse:
    contact = repo.get_contact_by_wa_id(db, wa_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    items, total = repo.list_messages(db, contact_id=contact.id, limit=limit, offset=offset)
    return WhatsAppMessageListResponse(
        items=[WhatsAppMessageResponse.model_validate(m) for m in items],
        total=total,
    )


@router.get("/suggestions", response_model=WhatsAppSuggestionListResponse)
def list_suggestions(
    status: str | None = Query(default="pending"),
    kind: str | None = Query(default=None),
    contact_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionListResponse:
    items, total = repo.list_suggestions(
        db,
        status=status,
        kind=kind,
        contact_id=contact_id,
        limit=limit,
        offset=offset,
    )
    return WhatsAppSuggestionListResponse(
        items=[_suggestion_response(s, db) for s in items],
        total=total,
    )


# --- Actions ---


@router.post("/suggestions/{suggestion_id}/send-reply", response_model=WhatsAppSendResult)
def send_reply(
    suggestion_id: int,
    payload: SendReplyRequest | None = None,
    db: Session = Depends(get_db),
) -> WhatsAppSendResult:
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    body = payload or SendReplyRequest()
    try:
        message = actions.send_reply(
            db,
            suggestion,
            text=body.text,
            mode=body.mode,
            template_name=body.template_name,
            template_language=body.template_language,
            template_components=body.template_components,
        )
    except WhatsAppActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except wa_client.WhatsAppApiError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc
    return WhatsAppSendResult(
        ok=True,
        wa_message_id=message.wa_message_id,
        message_id=message.id,
    )


@router.post("/suggestions/{suggestion_id}/add-to-calendar")
def add_to_calendar(
    suggestion_id: int,
    payload: AddToCalendarRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    body = payload or AddToCalendarRequest()
    try:
        event = actions.add_to_calendar(
            db,
            suggestion,
            title=body.title,
            agenda=body.agenda,
            start=body.start,
            end=body.end,
            calendar_id=body.calendar_id,
            conference=body.conference,
        )
    except WhatsAppActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Calendar error: {exc}",
        ) from exc
    return {"ok": True, "event_id": event.get("id"), "html_link": event.get("htmlLink")}


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=WhatsAppSuggestionResponse)
def dismiss_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    from datetime import datetime

    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    suggestion.status = "dismissed"
    suggestion.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(suggestion)
    return _suggestion_response(suggestion, db)


@router.post("/messages/send", response_model=WhatsAppSendResult)
def send_message(
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
) -> WhatsAppSendResult:
    contact = repo.upsert_contact(db, wa_id=payload.to)
    db.flush()
    try:
        message = actions.send_message(
            db,
            contact=contact,
            mode=payload.mode,
            text=payload.body,
            template_name=payload.template_name,
            template_language=payload.template_language,
            template_components=payload.template_components,
        )
    except WhatsAppActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except wa_client.WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return WhatsAppSendResult(
        ok=True,
        wa_message_id=message.wa_message_id,
        message_id=message.id,
    )


# --- Helpers ---


def _contact_response(contact) -> WhatsAppContactResponse:
    data = WhatsAppContactResponse.model_validate(contact)
    data.within_customer_window = repo.within_customer_window(contact)
    return data


def _suggestion_response(suggestion, db: Session | None = None) -> WhatsAppSuggestionResponse:
    details = None
    if suggestion.details:
        try:
            details = json.loads(suggestion.details)
        except json.JSONDecodeError:
            details = None

    contact_name = None
    wa_id = None
    message_body = None
    message_summary = None
    if db is not None:
        contact = db.get(models.WhatsAppContact, suggestion.contact_id)
        if contact:
            contact_name = contact.profile_name
            wa_id = contact.wa_id
        if suggestion.message_id:
            message = db.get(models.WhatsAppMessage, suggestion.message_id)
            if message:
                message_body = message.body
                message_summary = message.summary

    return WhatsAppSuggestionResponse(
        id=suggestion.id,
        contact_id=suggestion.contact_id,
        message_id=suggestion.message_id,
        kind=suggestion.kind,
        category=suggestion.category,
        status=suggestion.status,
        draft_text=suggestion.draft_text,
        details=details,
        created_at=suggestion.created_at,
        resolved_at=suggestion.resolved_at,
        sent_message_id=suggestion.sent_message_id,
        contact_name=contact_name,
        wa_id=wa_id,
        message_body=message_body,
        message_summary=message_summary,
    )
