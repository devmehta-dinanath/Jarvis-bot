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
}


def bootstrap_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    _ensure_recording_columns()
    _ensure_frame_columns()
    _ensure_activity_chunk_columns()
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)


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
