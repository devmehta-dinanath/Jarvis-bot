"""One-shot WhatsApp reprocess after a Docker deploy.

Runs from the server entrypoint, before uvicorn workers start. A release id
recorded on the data volume stops the same deploy from running again on restart
or in another worker. Nothing here sends a WhatsApp message.
"""

from __future__ import annotations

import fcntl
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.config import DATA_DIR
from app.database import SessionLocal
from app.services.whatsapp import reprocess as wa_reprocess
from app.services.whatsapp import settings as wa_settings

logger = logging.getLogger(__name__)

_RELEASE_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class DeployRefreshResult:
    status: str
    dry_run: bool = False
    release: str = ""
    examined_messages: int = 0
    commitments_invalidated: int = 0
    client_commitments_invalidated: int = 0
    deadlines_cleared: int = 0
    reply_drafts_cleared: int = 0
    outbound_reanalyzed: int = 0
    inbound_reclassified: int = 0
    open_commitments_remaining: int = 0
    pending_reply_drafts: int = 0
    detail: str = ""

    def log_line(self) -> str:
        return (
            "[WHATSAPP] Deploy refresh status=%s release=%s dry_run=%s "
            "examined=%s invalidated_owner=%s invalidated_client=%s "
            "deadlines_cleared=%s drafts_cleared=%s outbound_replayed=%s "
            "inbound_replayed=%s open_commitments=%s pending_replies=%s"
            % (
                self.status,
                self.release or "-",
                self.dry_run,
                self.examined_messages,
                self.commitments_invalidated,
                self.client_commitments_invalidated,
                self.deadlines_cleared,
                self.reply_drafts_cleared,
                self.outbound_reanalyzed,
                self.inbound_reclassified,
                self.open_commitments_remaining,
                self.pending_reply_drafts,
            )
        )


def parse_id_list(raw: str | None) -> list[int]:
    ids: list[int] = []
    for part in (raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        ids.append(int(text))
    return ids


def parse_wa_list(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def release_marker_path(release: str, *, data_dir: Path | None = None) -> Path:
    safe = _RELEASE_SAFE.sub("_", release).strip("._") or "release"
    root = data_dir or DATA_DIR
    return root / "whatsapp_reprocess_releases" / f"{safe}.done"


def release_already_applied(release: str, *, data_dir: Path | None = None) -> bool:
    return release_marker_path(release, data_dir=data_dir).is_file()


def mark_release_applied(release: str, *, data_dir: Path | None = None) -> None:
    path = release_marker_path(release, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ok\n", encoding="utf-8")


def _counts(db: Session, contact_ids: list[int]) -> tuple[int, int]:
    if not contact_ids:
        return 0, 0
    open_commitments = (
        db.query(models.WhatsAppCommitment)
        .filter(
            models.WhatsAppCommitment.contact_id.in_(contact_ids),
            models.WhatsAppCommitment.fulfilled_at.is_(None),
        )
        .count()
    )
    pending_replies = (
        db.query(models.WhatsAppSuggestion)
        .filter(
            models.WhatsAppSuggestion.contact_id.in_(contact_ids),
            models.WhatsAppSuggestion.status == "pending",
            models.WhatsAppSuggestion.kind == "reply",
        )
        .count()
    )
    return open_commitments, pending_replies


def run_deploy_refresh(
    db: Session,
    *,
    enabled: bool,
    dry_run: bool,
    release: str,
    contact_ids: list[int],
    wa_ids: list[str],
    reclassify_inbound: bool,
    max_inbound: int,
    data_dir: Path | None = None,
) -> DeployRefreshResult:
    """Reconcile and optionally replay stored chats. Caller owns the session."""
    if not enabled:
        result = DeployRefreshResult(status="disabled", detail="WHATSAPP_REPROCESS_ON_DEPLOY is false")
        logger.info(result.log_line())
        return result
    if not release:
        result = DeployRefreshResult(
            status="skipped",
            detail="WHATSAPP_REPROCESS_RELEASE is empty",
        )
        logger.warning(result.log_line() + " detail=missing_release")
        return result
    if release_already_applied(release, data_dir=data_dir):
        result = DeployRefreshResult(status="already_applied", release=release, dry_run=dry_run)
        logger.info(result.log_line())
        return result
    lock_root = data_dir or DATA_DIR
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_handle = open(lock_root / "whatsapp_deploy_refresh.lock", "a", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    try:
        return _run_locked(
            db,
            dry_run=dry_run,
            release=release,
            contact_ids=contact_ids,
            wa_ids=wa_ids,
            reclassify_inbound=reclassify_inbound,
            max_inbound=max_inbound,
            data_dir=data_dir,
        )
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def _run_locked(
    db: Session,
    *,
    dry_run: bool,
    release: str,
    contact_ids: list[int],
    wa_ids: list[str],
    reclassify_inbound: bool,
    max_inbound: int,
    data_dir: Path | None,
) -> DeployRefreshResult:
    if release_already_applied(release, data_dir=data_dir):
        result = DeployRefreshResult(status="already_applied", release=release, dry_run=dry_run)
        logger.info(result.log_line())
        return result

    contacts = wa_reprocess.resolve_contact_ids(
        db, contact_ids=contact_ids or None, wa_ids=wa_ids or None
    )
    if not contacts:
        result = DeployRefreshResult(
            status="skipped",
            release=release,
            detail="no matching contacts",
        )
        logger.warning(result.log_line() + " detail=no_matching_contacts")
        return result
    scoped_ids = [c.id for c in contacts]
    try:
        summary = wa_reprocess.reprocess_contacts(
            db,
            contact_ids=contact_ids or None,
            wa_ids=wa_ids or None,
            dry_run=dry_run,
            message_limit=wa_settings.WHATSAPP_CHAT_CONTEXT_MESSAGES,
            reanalyze_outbound=True,
            reclassify_inbound=reclassify_inbound,
            max_inbound_reclassify=max_inbound,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "[WHATSAPP] Deploy refresh failed release=%s contacts=%s",
            release,
            len(scoped_ids),
        )
        raise

    if dry_run:
        db.rollback()
    else:
        db.commit()
        mark_release_applied(release, data_dir=data_dir)

    examined = sum(c.messages_in_window for c in summary.contacts)
    open_commitments, pending_replies = _counts(db, scoped_ids)
    result = DeployRefreshResult(
        status="dry_run" if dry_run else "applied",
        dry_run=dry_run,
        release=release,
        examined_messages=examined,
        commitments_invalidated=summary.commitments_invalidated,
        client_commitments_invalidated=summary.client_commitments_invalidated,
        deadlines_cleared=summary.deadlines_cleared,
        reply_drafts_cleared=summary.reply_drafts_cleared,
        outbound_reanalyzed=summary.outbound_reanalyzed,
        inbound_reclassified=summary.inbound_reclassified,
        open_commitments_remaining=open_commitments,
        pending_reply_drafts=pending_replies,
    )
    logger.info(result.log_line())
    return result


def main() -> int:
    if not wa_settings.WHATSAPP_REPROCESS_ON_DEPLOY:
        logger.info("[WHATSAPP] Deploy refresh disabled")
        return 0
    db = SessionLocal()
    try:
        run_deploy_refresh(
            db,
            enabled=True,
            dry_run=wa_settings.WHATSAPP_REPROCESS_DRY_RUN,
            release=wa_settings.WHATSAPP_REPROCESS_RELEASE,
            contact_ids=parse_id_list(wa_settings.WHATSAPP_REPROCESS_CONTACT_IDS),
            wa_ids=parse_wa_list(wa_settings.WHATSAPP_REPROCESS_WA_IDS),
            reclassify_inbound=wa_settings.WHATSAPP_REPROCESS_RECLASSIFY_INBOUND,
            max_inbound=wa_settings.WHATSAPP_REPROCESS_MAX_INBOUND,
        )
    except Exception:
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
