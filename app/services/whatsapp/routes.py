import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app import models
from app.config import WHATSAPP_PROVIDER, WHATSAPP_VERIFY_TOKEN
from app.database import get_db
from app.services import service_manager
from app.services.whatsapp import actions
from app.services.whatsapp import client as wa_client
from app.services.whatsapp import inbox as wa_inbox
from app.services.whatsapp import meeting_scope
from app.services.whatsapp import repository as repo
from app.services.whatsapp import taxonomy as wa_taxonomy
from app.services.whatsapp import webhook as wa_webhook
from app.services.whatsapp.actions import WhatsAppActionError
from app.services.whatsapp.schemas import (
    AddToCalendarRequest,
    ClarifyAnswerRequest,
    CreateForwardingRuleRequest,
    CreateInstructionRequest,
    DismissAllResponse,
    FeedbackRequest,
    FeedbackResponse,
    InboxStatusResponse,
    RefreshPendingResponse,
    RemindMeRequest,
    SendMessageRequest,
    SendReplyRequest,
    SetContactExcludedRequest,
    TeamForwardingRuleListResponse,
    TeamForwardingRuleResponse,
    UpdateForwardingRuleRequest,
    UpdateInstructionRequest,
    UserInstructionListResponse,
    UserInstructionResponse,
    WhatsAppTaxonomyResponse,
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

@router.get("/categories", response_model=WhatsAppTaxonomyResponse)
def list_categories() -> WhatsAppTaxonomyResponse:
    return WhatsAppTaxonomyResponse(**wa_taxonomy.taxonomy_payload())

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
    # Meta uses X-Hub-Signature-256; WAHA may send X-Webhook-Hmac / similar.
    signature = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Webhook-Hmac")
        or request.headers.get("X-Waha-Hmac")
    )
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

    if WHATSAPP_PROVIDER == "waha" or wa_webhook.is_waha_payload(payload):
        logger.info(
            "[WHATSAPP] WAHA webhook event=%s session=%s",
            payload.get("event"),
            payload.get("session"),
        )
    else:
        logger.info(
            "[WHATSAPP] Webhook received object=%s entries=%s",
            payload.get("object"),
            len(payload.get("entry", []) or []),
        )

    try:
        stored = wa_webhook.process_webhook_payload(db, payload)
    except Exception:
        logger.exception("[WHATSAPP] Failed to process webhook payload")
        db.rollback()
        return {"received": True, "stored": 0}

    return {"received": True, "stored": stored}

@router.get("/instructions", response_model=UserInstructionListResponse)
def list_instructions(db: Session = Depends(get_db)) -> UserInstructionListResponse:
    """Standing plain-language rules configured in the settings panel — see
    UserInstruction in models.py for how these are fed into the classifier/drafter."""
    items = repo.list_instructions(db)
    return UserInstructionListResponse(
        items=[UserInstructionResponse.model_validate(i) for i in items]
    )


@router.post(
    "/instructions",
    response_model=UserInstructionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_instruction(
    payload: CreateInstructionRequest,
    db: Session = Depends(get_db),
) -> UserInstructionResponse:
    instruction = repo.create_instruction(db, payload.text)
    db.commit()
    db.refresh(instruction)
    return UserInstructionResponse.model_validate(instruction)


@router.patch("/instructions/{instruction_id}", response_model=UserInstructionResponse)
def update_instruction(
    instruction_id: int,
    payload: UpdateInstructionRequest,
    db: Session = Depends(get_db),
) -> UserInstructionResponse:
    instruction = repo.update_instruction(
        db, instruction_id, text=payload.text, is_active=payload.is_active
    )
    if instruction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instruction not found")
    db.commit()
    db.refresh(instruction)
    return UserInstructionResponse.model_validate(instruction)


@router.delete("/instructions/{instruction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instruction(instruction_id: int, db: Session = Depends(get_db)) -> None:
    deleted = repo.delete_instruction(db, instruction_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Instruction not found")
    db.commit()


@router.get("/forwarding-rules", response_model=TeamForwardingRuleListResponse)
def list_forwarding_rules(db: Session = Depends(get_db)) -> TeamForwardingRuleListResponse:
    """Rule 14 — 'forward to team' category → team member mappings configured in
    Settings. See TeamForwardingRule in models.py."""
    items = repo.list_forwarding_rules(db)
    return TeamForwardingRuleListResponse(
        items=[TeamForwardingRuleResponse.model_validate(r) for r in items]
    )


@router.post(
    "/forwarding-rules",
    response_model=TeamForwardingRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_forwarding_rule(
    payload: CreateForwardingRuleRequest,
    db: Session = Depends(get_db),
) -> TeamForwardingRuleResponse:
    rule = repo.create_forwarding_rule(
        db,
        label=payload.label,
        trigger_category=payload.trigger_category,
        trigger_payment_status=payload.trigger_payment_status,
        team_member_name=payload.team_member_name,
        team_member_wa_id=payload.team_member_wa_id,
    )
    db.commit()
    db.refresh(rule)
    return TeamForwardingRuleResponse.model_validate(rule)


@router.patch("/forwarding-rules/{rule_id}", response_model=TeamForwardingRuleResponse)
def update_forwarding_rule(
    rule_id: int,
    payload: UpdateForwardingRuleRequest,
    db: Session = Depends(get_db),
) -> TeamForwardingRuleResponse:
    fields = payload.model_dump(exclude_unset=True)
    rule = repo.update_forwarding_rule(db, rule_id, **fields)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forwarding rule not found")
    db.commit()
    db.refresh(rule)
    return TeamForwardingRuleResponse.model_validate(rule)


@router.delete("/forwarding-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_forwarding_rule(rule_id: int, db: Session = Depends(get_db)) -> None:
    deleted = repo.delete_forwarding_rule(db, rule_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Forwarding rule not found")
    db.commit()


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


@router.patch("/contacts/{wa_id}/exclude", response_model=WhatsAppContactResponse)
def set_contact_excluded(
    wa_id: str,
    payload: SetContactExcludedRequest,
    db: Session = Depends(get_db),
) -> WhatsAppContactResponse:
    """"Stop reading this group" button and the settings-page toggle both call this."""
    contact = repo.set_contact_excluded(db, wa_id, payload.excluded)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    db.commit()
    db.refresh(contact)
    return _contact_response(contact)


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
    lane: str | None = Query(
        default=None,
        description="work | life — filter suggestions by rendering lane",
    ),
    contact_id: int | None = Query(default=None),
    has_reminder: bool | None = Query(
        default=None,
        description="Only suggestions with a personal reminder set (see actions.set_reminder)",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionListResponse:
    # Over-fetch a bit when meetings-only so post-filter still fills the page.
    fetch_limit = limit
    if meeting_scope.MEETINGS_REMINDERS_ONLY and status == "pending":
        fetch_limit = min(200, max(limit * 3, limit + 20))
    items, total = repo.list_suggestions(
        db,
        status=status,
        kind=kind,
        lane=lane,
        contact_id=contact_id,
        has_reminder=has_reminder,
        limit=fetch_limit,
        offset=offset,
    )
    if meeting_scope.MEETINGS_REMINDERS_ONLY and status == "pending":
        filtered: list = []
        for suggestion in items:
            details = {}
            raw = suggestion.details
            if isinstance(raw, dict):
                details = raw
            elif isinstance(raw, str) and raw.strip():
                try:
                    import json

                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        details = parsed
                except Exception:
                    details = {}
            body = None
            if suggestion.message_id is not None:
                message = db.get(models.WhatsAppMessage, suggestion.message_id)
                body = message.body if message is not None else None
            if meeting_scope.is_surfaceable_meeting_chip(
                category=suggestion.category,
                kind=suggestion.kind,
                body=body,
                details=details,
            ):
                filtered.append(suggestion)
        items = filtered[:limit]
        total = len(filtered) if offset == 0 else max(total, len(filtered))
    elif status == "pending":
        # Hide reply/nudge chips once the owner's latest message is outbound (already replied).
        # Keep meeting chips and owner→them follow-ups (those exist *because* latest is outbound).
        preserve_kinds = {
            "meeting",
            "owner_followup_nudge",
            "commitment_reminder",
            "client_commitment_reminder",
            "life_nudge",
        }
        reply_kinds = {
            "reply",
            "nudge",
            "greeting",
            "clarify",
            "followup_nudge",
            "payment",
            "lead",
            "document",
            "complaint",
            "shipment",
            "order",
            "timeline",
            "budget",
            "scope",
            "other",
            "blank",
            "unconfident",
        }
        kept: list = []
        for suggestion in items:
            if suggestion.kind in preserve_kinds or suggestion.category in (
                "meeting",
                "family_plan",
                "personal_date",
                "personal_task",
                "pending_commitment",
                "client_commitment",
            ):
                kept.append(suggestion)
                continue
            if suggestion.kind in reply_kinds or (
                suggestion.category
                and suggestion.category
                not in (
                    "meeting",
                    "family_plan",
                    "personal_date",
                    "personal_task",
                    "awaiting_reply",
                )
                and suggestion.kind != "meeting"
            ):
                if repo.contact_latest_is_outbound(db, suggestion.contact_id):
                    continue
            kept.append(suggestion)
        items = kept

    # Groups: only @tag / name mentions — hide leftover untagged chips from older builds.
    if status == "pending" and items:
        items = [
            s for s in items if not wa_inbox._is_untagged_group_suggestion(db, s)
        ]
        if offset == 0:
            total = len(items)
    return WhatsAppSuggestionListResponse(
        items=[_suggestion_response(s, db) for s in items],
        total=total,
    )


@router.get("/inbox/status", response_model=InboxStatusResponse)
def get_inbox_status(db: Session = Depends(get_db)) -> InboxStatusResponse:
    return InboxStatusResponse(**wa_inbox.inbox_status(db))


@router.post("/inbox/refresh-pending", response_model=RefreshPendingResponse)
def refresh_pending_inbox(
    lookback_hours: int = Query(default=168, ge=1, le=720),
    db: Session = Depends(get_db),
) -> RefreshPendingResponse:
    return RefreshPendingResponse(**wa_inbox.refresh_pending_suggestions(db, lookback_hours=lookback_hours))


@router.post("/inbox/backfill-history")
def backfill_chat_history(
    chat_limit: int = Query(default=40, ge=1, le=200),
    messages_per_chat: int = Query(default=40, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """Pull recent WAHA chats into jarvis.db for draft context (no new Inbox chips)."""
    from app.services.whatsapp import history_backfill

    try:
        return history_backfill.backfill_recent_history(
            db, chat_limit=chat_limit, messages_per_chat=messages_per_chat
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc


@router.post("/inbox/ensure-waha-webhooks")
def ensure_waha_webhooks() -> dict:
    """Ensure WAHA session webhook includes message + message.any."""
    from app.services.whatsapp import waha_client

    ok = waha_client.ensure_session_webhooks()
    return {"ok": ok, "webhook_url": __import__("os").getenv("WAHA_WEBHOOK_URL") or None}

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


@router.post("/suggestions/{suggestion_id}/clarify", response_model=WhatsAppSuggestionResponse)
def answer_clarification(
    suggestion_id: int,
    payload: ClarifyAnswerRequest,
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    """Rule 13 — the user tapped one of the clarifying tap options; regenerate the
    draft using their answer, then return the updated card."""
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    try:
        actions.answer_clarification(db, suggestion, answer=payload.answer)
    except WhatsAppActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _suggestion_response(suggestion, db)


@router.post("/suggestions/{suggestion_id}/forward", response_model=WhatsAppSuggestionResponse)
def forward_to_team(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    """Rule 14 — one-tap forward of the original message to the assigned team member."""
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    try:
        actions.forward_to_team(db, suggestion)
    except WhatsAppActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except wa_client.WhatsAppApiError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return _suggestion_response(suggestion, db)


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
            send_confirmation=body.send_confirmation,
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
    return {
        "ok": True,
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "meet_link": event.get("hangoutLink") or event.get("htmlLink"),
        "reply_sent": event.get("reply_sent", False),
        "reply_error": event.get("reply_error"),
        "sent_message_id": event.get("sent_message_id"),
    }


@router.post("/suggestions/{suggestion_id}/remind")
def remind_me(
    suggestion_id: int,
    payload: RemindMeRequest | None = None,
    db: Session = Depends(get_db),
) -> dict:
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    body = payload or RemindMeRequest()
    try:
        event = actions.set_reminder(
            db,
            suggestion,
            remind_at=body.remind_at,
            title=body.title,
            calendar_id=body.calendar_id,
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
    except Exception as exc:
        logger.exception("[WHATSAPP] Remind me failed for suggestion %s", suggestion_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not create reminder: {exc}",
        ) from exc
    return {
        "ok": True,
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "reminder_at": event.get("reminder_at"),
    }


@router.post("/suggestions/{suggestion_id}/feedback", response_model=FeedbackResponse)
def record_feedback(
    suggestion_id: int,
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
) -> FeedbackResponse:
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")

    message_snippet: str | None = None
    if suggestion.message_id:
        msg = db.get(models.WhatsAppMessage, suggestion.message_id)
        if msg and msg.body:
            message_snippet = msg.body.strip()[:300]

    feedback = repo.record_feedback(
        db,
        suggestion_id=suggestion.id,
        feedback_type=payload.feedback_type,
        original_category=suggestion.category,
        original_confidence=getattr(suggestion, "confidence", None),
        message_snippet=message_snippet,
        contact_id=suggestion.contact_id,
        message_id=suggestion.message_id,
        correct_response=payload.correct_response,
    )
    db.commit()
    logger.info(
        "[WHATSAPP] Feedback recorded suggestion=%s type=%s category=%s",
        suggestion_id,
        payload.feedback_type,
        suggestion.category,
    )

    # "Wrong" corrections are an internal note, never sent to the contact — regenerate
    # the draft in place so the card shows the corrected wording immediately.
    updated_draft: str | None = None
    if payload.feedback_type == "wrong" and payload.correct_response and suggestion.message_id:
        try:
            actions.redraft_with_correction(
                db, suggestion, correct_response=payload.correct_response
            )
            updated_draft = suggestion.draft_text
        except WhatsAppActionError as exc:
            logger.warning(
                "[WHATSAPP] Could not redraft suggestion %s after correction: %s",
                suggestion_id,
                exc,
            )

    return FeedbackResponse(
        ok=True,
        feedback_id=feedback.id,
        feedback_type=feedback.feedback_type,
        updated_draft=updated_draft,
    )


@router.post("/suggestions/dismiss-all", response_model=DismissAllResponse)
def dismiss_all_suggestions(db: Session = Depends(get_db)) -> DismissAllResponse:
    dismissed = repo.dismiss_all_pending(db)
    return DismissAllResponse(dismissed=dismissed)


def _suggestion_details(suggestion) -> dict:
    raw = suggestion.details
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _commitment_id_from_suggestion(suggestion) -> int | None:
    details = _suggestion_details(suggestion)
    raw = details.get("commitment_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=WhatsAppSuggestionResponse)
def dismiss_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    # Dismiss stops further reminders for linked commitments (fulfill so they don't reappear).
    commitment_id = _commitment_id_from_suggestion(suggestion)
    if commitment_id is not None:
        repo.fulfill_commitment(db, commitment_id)
        repo.dismiss_commitment_suggestions(db, commitment_id)
    suggestion.status = "dismissed"
    suggestion.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(suggestion)
    return _suggestion_response(suggestion, db)


@router.post("/suggestions/{suggestion_id}/done", response_model=WhatsAppSuggestionResponse)
def mark_suggestion_done(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    """Done / Mark done — fulfill linked commitment and dismiss the card."""
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    commitment_id = _commitment_id_from_suggestion(suggestion)
    if commitment_id is not None:
        repo.fulfill_commitment(db, commitment_id)
        repo.dismiss_commitment_suggestions(db, commitment_id)
    suggestion.status = "done"
    suggestion.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(suggestion)
    return _suggestion_response(suggestion, db)


@router.post("/suggestions/{suggestion_id}/snooze", response_model=WhatsAppSuggestionResponse)
def snooze_suggestion(
    suggestion_id: int,
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
) -> WhatsAppSuggestionResponse:
    """Snooze / Remind me later — postpone and hide the current chip."""
    suggestion = repo.get_suggestion(db, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Suggestion not found")
    commitment_id = _commitment_id_from_suggestion(suggestion)
    now = datetime.utcnow()
    if commitment_id is not None:
        commitment = db.get(models.WhatsAppCommitment, commitment_id)
        if commitment is not None and commitment.fulfilled_at is None:
            base = commitment.deadline_at or now
            if base < now:
                base = now
            commitment.deadline_at = base + timedelta(hours=hours)
            commitment.last_reminded_at = now
    suggestion.status = "dismissed"
    suggestion.resolved_at = now
    details = _suggestion_details(suggestion)
    details["snoozed_until"] = (now + timedelta(hours=hours)).isoformat()
    suggestion.details = json.dumps(details)
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
    is_group = False
    message_body = None
    message_summary = None
    message_translation = None
    message_language = None
    if db is not None:
        contact = db.get(models.WhatsAppContact, suggestion.contact_id)
        if contact:
            contact_name = contact.profile_name
            wa_id = contact.wa_id
            is_group = bool(contact.is_group)
        if suggestion.message_id:
            message = db.get(models.WhatsAppMessage, suggestion.message_id)
            if message:
                message_body = message.body
                message_summary = message.summary
                message_translation = message.translation
                message_language = message.language

    # Rule 9 timing used to hide draft_text until visible_after. With AI drafts enabled
    # (STEP 2), always return the draft so Inbox shows a suggestion immediately.
    from app.services.whatsapp.settings import WHATSAPP_AI_DRAFTS_ENABLED as _drafts_on

    draft_ready = (
        _drafts_on
        or suggestion.visible_after is None
        or suggestion.visible_after <= datetime.utcnow()
    )
    draft_text = suggestion.draft_text if draft_ready else None

    # Rule 14 — Forward to team: show a one-tap Forward button only when an active rule
    # is configured (a real team member wa_id assigned) for this category, and only
    # while it hasn't already been forwarded.
    forward_label = None
    forwarded_to = (details or {}).get("forwarded_to")
    if db is not None and not forwarded_to:
        rule = repo.find_forwarding_rule(
            db,
            category=suggestion.category,
            payment_status=(details or {}).get("payment_status"),
        )
        if rule is not None:
            forward_label = rule.label

    return WhatsAppSuggestionResponse(
        id=suggestion.id,
        contact_id=suggestion.contact_id,
        message_id=suggestion.message_id,
        kind=suggestion.kind,
        category=suggestion.category,
        priority=suggestion.priority,
        lane=getattr(suggestion, "lane", None),
        confidence=getattr(suggestion, "confidence", None),
        status=suggestion.status,
        draft_text=draft_text,
        details=details,
        created_at=suggestion.created_at,
        resolved_at=suggestion.resolved_at,
        sent_message_id=suggestion.sent_message_id,
        contact_name=contact_name,
        wa_id=wa_id,
        is_group=is_group,
        message_body=message_body,
        message_summary=message_summary,
        message_translation=message_translation,
        message_language=message_language,
        visible_after=suggestion.visible_after,
        forward_label=forward_label,
        forwarded_to=forwarded_to,
    )
