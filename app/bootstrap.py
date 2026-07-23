from pathlib import Path

from sqlalchemy import inspect, text

from app.config import DATA_DIR, MEDIA_ROOT
from app.database import Base, engine


FRAME_COLUMNS: dict[str, str] = {
    "screenpipe_frame_id": "INTEGER",
    "app_name": "VARCHAR(255)",
    "window_name": "VARCHAR(500)",
    "browser_url": "VARCHAR(1000)",
    "captured_at": "DATETIME",
    "activity_status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
    "screenpipe_ocr_text": "TEXT",
}


ACTIVITY_CHUNK_COLUMNS: dict[str, str] = {
    "transcript_status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
    "transcript_text": "TEXT",
    "transcript_error": "TEXT",
}


ACTIVITY_SUMMARY_COLUMNS: dict[str, str] = {
    "predictions_text": "TEXT",
}


WHATSAPP_CONTACT_COLUMNS: dict[str, str] = {
    "contact_type": "VARCHAR(20)",
    "last_replied_at": "DATETIME",
    "is_group": "BOOLEAN NOT NULL DEFAULT 0",
    "is_excluded": "BOOLEAN NOT NULL DEFAULT 0",
}


WHATSAPP_FEEDBACK_COLUMNS: dict[str, str] = {
    "correct_response": "TEXT",
}


WHATSAPP_MESSAGE_COLUMNS: dict[str, str] = {
    "priority": "VARCHAR(20)",
    "translation": "TEXT",
    "is_group": "BOOLEAN NOT NULL DEFAULT 0",
    "is_forwarded": "BOOLEAN NOT NULL DEFAULT 0",
}


WHATSAPP_SUGGESTION_COLUMNS: dict[str, str] = {
    "priority": "VARCHAR(20)",
    "lane": "VARCHAR(10)",
    "confidence": "INTEGER",
    "visible_after": "DATETIME",
}


RECORDING_COLUMNS: dict[str, str] = {
    "source_video_path": "VARCHAR(500)",
    "capture_command": "TEXT",
    "media_root": "VARCHAR(500)",
    "frames_dir": "VARCHAR(500)",
    "error_message": "TEXT",
    "total_frames": "INTEGER NOT NULL DEFAULT 0",
    "processed_frames": "INTEGER NOT NULL DEFAULT 0",
    "ocr_completed_frames": "INTEGER NOT NULL DEFAULT 0",
    "started_at": "DATETIME",
    "completed_at": "DATETIME",
    "device_id": "VARCHAR(64)",
    "client_recording_id": "INTEGER",
    "sync_status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
}


FRAME_SYNC_COLUMNS: dict[str, str] = {
    "sync_status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
}


ACTIVITY_CHUNK_SYNC_COLUMNS: dict[str, str] = {
    "client_chunk_id": "INTEGER",
    "sync_status": "VARCHAR(50) NOT NULL DEFAULT 'pending'",
}


def bootstrap_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _ensure_recording_columns()
    _ensure_frame_columns()
    _ensure_frame_sync_columns()
    _ensure_activity_chunk_columns()
    _ensure_activity_chunk_sync_columns()
    _ensure_activity_summary_columns()
    _ensure_columns("whatsapp_contacts", WHATSAPP_CONTACT_COLUMNS)
    _ensure_columns("whatsapp_messages", WHATSAPP_MESSAGE_COLUMNS)
    _ensure_columns("whatsapp_suggestions", WHATSAPP_SUGGESTION_COLUMNS)
    _ensure_columns("whatsapp_feedback", WHATSAPP_FEEDBACK_COLUMNS)
    _backfill_whatsapp_group_contacts()
    _seed_default_forwarding_rules()
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


# Rule 14 — "Forward to team": starting categories from the spec. team_member_wa_id is
# left blank on purpose — the Forward button only appears once the user actually assigns
# someone to a row in Settings, so seeding these can never silently forward anywhere.
DEFAULT_FORWARDING_RULES: tuple[dict[str, str | None], ...] = (
    {"label": "Accounts", "trigger_category": "payment", "trigger_payment_status": "received"},
    {"label": "Production", "trigger_category": "order", "trigger_payment_status": None},
    {"label": "Logistics", "trigger_category": "shipment", "trigger_payment_status": None},
    {"label": "Support", "trigger_category": "complaint", "trigger_payment_status": None},
)


def _seed_default_forwarding_rules() -> None:
    inspector = inspect(engine)
    if "team_forwarding_rules" not in inspector.get_table_names():
        return

    with engine.begin() as connection:
        existing = connection.execute(text("SELECT COUNT(*) FROM team_forwarding_rules")).scalar()
        if existing:
            return
        for rule in DEFAULT_FORWARDING_RULES:
            connection.execute(
                text(
                    "INSERT INTO team_forwarding_rules "
                    "(label, trigger_category, trigger_payment_status, is_active, created_at) "
                    "VALUES (:label, :trigger_category, :trigger_payment_status, 1, CURRENT_TIMESTAMP)"
                ),
                rule,
            )


def _backfill_whatsapp_group_contacts() -> None:
    """One-time repair for contacts created before is_group tracking existed.

    _ensure_columns adds `is_group` with DEFAULT 0, so any contact row that
    predates the column (or whose group-detection missed on first message)
    is stuck showing as a regular contact even though its wa_id is a group
    or newsletter JID. The suffix is unambiguous ground truth, so reconcile
    it here every startup (cheap no-op once rows are fixed).
    """
    inspector = inspect(engine)
    if "whatsapp_contacts" not in inspector.get_table_names():
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE whatsapp_contacts SET is_group = 1 "
                "WHERE is_group = 0 AND (wa_id LIKE '%@g.us' OR wa_id LIKE '%@newsletter')"
            )
        )


def _ensure_columns(table: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns(table)}
    missing = {name: ddl for name, ddl in columns.items() if name not in existing}
    if not missing:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing.items():
            connection.execute(
                text(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_type}")
            )


def _ensure_frame_sync_columns() -> None:
    _ensure_columns("frames", FRAME_SYNC_COLUMNS)


def _ensure_activity_chunk_sync_columns() -> None:
    _ensure_columns("activity_chunks", ACTIVITY_CHUNK_SYNC_COLUMNS)


def _ensure_recording_columns() -> None:
    inspector = inspect(engine)
    if "recordings" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("recordings")}
    missing = {
        name: ddl
        for name, ddl in RECORDING_COLUMNS.items()
        if name not in existing
    }
    if not missing:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing.items(): 
            connection.execute(
                text(f"ALTER TABLE recordings ADD COLUMN {column_name} {column_type}")
            )

 
def _ensure_activity_chunk_columns() -> None: 
    inspector = inspect(engine) 
    if "activity_chunks" not in inspector.get_table_names(): 
        return 
 
    existing = {column["name"] for column in inspector.get_columns("activity_chunks")} 
    missing = { 
        name: ddl  
        for name, ddl in ACTIVITY_CHUNK_COLUMNS.items() 
        if name not in existing  
    } 
    if not missing:
        return  

    with engine.begin() as connection: 
        for column_name, column_type in missing.items():
            connection.execute( 
                text(f"ALTER TABLE activity_chunks ADD COLUMN {column_name} {column_type}")
            ) 


def _ensure_activity_summary_columns() -> None:
    inspector = inspect(engine)
    if "activity_summaries" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("activity_summaries")}
    missing = {
        name: ddl
        for name, ddl in ACTIVITY_SUMMARY_COLUMNS.items()
        if name not in existing
    }
    if not missing:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing.items():
            connection.execute(
                text(f"ALTER TABLE activity_summaries ADD COLUMN {column_name} {column_type}")
            )


def _ensure_frame_columns() -> None:
    inspector = inspect(engine)
    if "frames" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("frames")}
    missing = {name: ddl for name, ddl in FRAME_COLUMNS.items() if name not in existing}
    if not missing:
        return

    with engine.begin() as connection:
        for column_name, column_type in missing.items():
            connection.execute(
                text(f"ALTER TABLE frames ADD COLUMN {column_name} {column_type}")
            )
