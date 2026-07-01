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
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


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
