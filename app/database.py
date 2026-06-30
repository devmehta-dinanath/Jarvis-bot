import logging
import os
import shutil
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import DATA_DIR, DATABASE_PATH, DEFAULT_DATABASE_URL, LEGACY_DATABASE_PATH

logger = logging.getLogger(__name__)


def _migrate_legacy_database() -> None:
    """Copy screenpipe.db → jarvis.db once when upgrading existing installs."""
    if DATABASE_PATH.exists() and DATABASE_PATH.stat().st_size > 0:
        return
    if not LEGACY_DATABASE_PATH.exists() or LEGACY_DATABASE_PATH.stat().st_size == 0:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LEGACY_DATABASE_PATH, DATABASE_PATH)
    logger.info(
        "Migrated SQLite data %s → %s (%s bytes)",
        LEGACY_DATABASE_PATH.name,
        DATABASE_PATH.name,
        DATABASE_PATH.stat().st_size,
    )


_migrate_legacy_database()

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def sqlite_database_path() -> Path | None:
    """Resolved path to jarvis.db when using the default SQLite backend."""
    if not DATABASE_URL.startswith("sqlite"):
        return None
    if DATABASE_URL == DEFAULT_DATABASE_URL:
        return DATABASE_PATH.resolve()
    # Custom sqlite URL (e.g. Docker): .../jarvis.db
    raw = DATABASE_URL.removeprefix("sqlite:///")
    return Path(raw).resolve()


_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if DATABASE_URL.startswith("sqlite")
    else {}
)
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()