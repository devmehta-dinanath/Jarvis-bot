import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DATA_DIR = BASE_DIR / "data"
JARVIS_DATABASE_FILENAME = "jarvis.db"
DATABASE_PATH = DATA_DIR / JARVIS_DATABASE_FILENAME
LEGACY_DATABASE_PATH = DATA_DIR / "screenpipe.db"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_DATABASE_URL = f"sqlite:///{DATABASE_PATH.resolve().as_posix()}"

SCREENPIPE_DIR_NAME = "screenpipe"
FRAMES_DIR_NAME = "frames"
OCR_DIR_NAME = "ocr"
ACTIVITY_DIR_NAME = "activity"

DEFAULT_CAPTURE_FILENAME = "capture.mp4"
DEFAULT_FRAME_INTERVAL_SECONDS = 1.0

AUTO_START_SERVICES = os.getenv("AUTO_START_SERVICES", "true").lower() in {"1", "true", "yes"}
RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER", "false").lower() in {"1", "true", "yes"}
LIVE_RECORDING_TITLE = os.getenv("LIVE_RECORDING_TITLE", "Live capture")

# --- Screenpipe CLI (event-driven capture on screen change) ---
SCREENPIPE_ENABLED = os.getenv("SCREENPIPE_ENABLED", "true").lower() in {"1", "true", "yes"}
SCREENPIPE_CLI_COMMAND = os.getenv("SCREENPIPE_CLI_COMMAND", "screenpipe record")
SCREENPIPE_API_URL = os.getenv("SCREENPIPE_API_URL", "http://127.0.0.1:3030").rstrip("/")
# Bearer token for /frames, /search, etc. (not needed for /health). Also reads SCREENPIPE_LOCAL_API_KEY.
SCREENPIPE_API_TOKEN = os.getenv("SCREENPIPE_API_TOKEN", "").strip() or None
SCREENPIPE_START_CLI = os.getenv("SCREENPIPE_START_CLI", "true").lower() in {"1", "true", "yes"}
SCREENPIPE_POLL_INTERVAL_SECONDS = float(os.getenv("SCREENPIPE_POLL_INTERVAL_SECONDS", "2.0"))
SCREENPIPE_SYNC_LOOKBACK_SECONDS = float(os.getenv("SCREENPIPE_SYNC_LOOKBACK_SECONDS", "300"))
SCREENPIPE_SYNC_OVERLAP_SECONDS = float(os.getenv("SCREENPIPE_SYNC_OVERLAP_SECONDS", "60"))
SCREENPIPE_SYNC_BATCH_LIMIT = int(os.getenv("SCREENPIPE_SYNC_BATCH_LIMIT", "100"))
SCREENPIPE_HEALTH_TIMEOUT_SECONDS = float(os.getenv("SCREENPIPE_HEALTH_TIMEOUT_SECONDS", "120"))
# Screenpipe frame-diff tuning (native Linux/macOS when UI recorder works).
SCREENPIPE_VISUAL_CHECK_INTERVAL_MS = int(os.getenv("SCREENPIPE_VISUAL_CHECK_INTERVAL_MS", "500"))
SCREENPIPE_MIN_CAPTURE_INTERVAL_MS = int(os.getenv("SCREENPIPE_MIN_CAPTURE_INTERVAL_MS", "300"))
SCREENPIPE_VISUAL_CHANGE_THRESHOLD = float(os.getenv("SCREENPIPE_VISUAL_CHANGE_THRESHOLD", "0.005"))
# Disable Screenpipe's 30s idle timer — capture on change only (ms; 1h = backup safety net).
SCREENPIPE_IDLE_CAPTURE_INTERVAL_MS = int(os.getenv("SCREENPIPE_IDLE_CAPTURE_INTERVAL_MS", "3600000"))

# Linux Docker: X11 hash watcher captures on pixel change (Screenpipe UI recorder is off in Docker).
SCREEN_CHANGE_CHECK_INTERVAL_SECONDS = float(os.getenv("SCREEN_CHANGE_CHECK_INTERVAL_SECONDS", "0.4"))
SCREEN_CHANGE_MIN_CAPTURE_SECONDS = float(os.getenv("SCREEN_CHANGE_MIN_CAPTURE_SECONDS", "0.5"))
SCREEN_CHANGE_PROBE_COMMAND = os.getenv(
    "SCREEN_CHANGE_PROBE_COMMAND",
    "ffmpeg -loglevel error -y -f x11grab -video_size 320x180 -i {display} -frames:v 1 {output}",
)


def _env_auto_flag(name: str, *, default_when_auto: bool) -> bool:
    raw = os.getenv(name, "auto").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    return default_when_auto


def use_x11_change_capture() -> bool:
    """Probe X11 pixels and capture when the desktop image changes."""
    return _env_auto_flag("X11_CHANGE_CAPTURE_ENABLED", default_when_auto=RUNNING_IN_DOCKER)


def use_screenpipe_frame_sync() -> bool:
    """Pull frame images from the Screenpipe API (off when X11 capture is primary)."""
    return _env_auto_flag("SCREENPIPE_SYNC_FRAMES", default_when_auto=not use_x11_change_capture())
# When false (default), Screenpipe subprocess logs only errors/panics — not audio/D-Bus noise.
SCREENPIPE_LOG_CLI_VERBOSE = os.getenv("SCREENPIPE_LOG_CLI_VERBOSE", "false").lower() in {
    "1",
    "true",
    "yes",
}
# When false (default), INFO logs are limited to [CAPTURE] and [OCR] pipeline work.
PIPELINE_VERBOSE_LOGS = os.getenv("PIPELINE_VERBOSE_LOGS", "false").lower() in {"1", "true", "yes"}

# Fallback when SCREENPIPE_ENABLED=false (fixed-interval ffmpeg grab)
FRAME_INTERVAL_SECONDS = float(
    os.getenv("FRAME_INTERVAL_SECONDS", str(DEFAULT_FRAME_INTERVAL_SECONDS))
)
DEFAULT_FRAME_CAPTURE_COMMAND = os.getenv(
    "DEFAULT_FRAME_CAPTURE_COMMAND",
    "ffmpeg -y -f x11grab -video_size 1920x1080 -i :0.0 -frames:v 1 {output}",
)
DEFAULT_VIDEO_CAPTURE_COMMAND = os.getenv(
    "DEFAULT_VIDEO_CAPTURE_COMMAND",
    "ffmpeg -y -f x11grab -video_size 1920x1080 -i :0.0 -t 10 {output}",
)

OCR_POLL_INTERVAL_SECONDS = float(os.getenv("OCR_POLL_INTERVAL_SECONDS", "0.5"))

# --- Activity classification ---
ACTIVITY_POLL_INTERVAL_SECONDS = float(os.getenv("ACTIVITY_POLL_INTERVAL_SECONDS", "2.0"))
ACTIVITY_CHUNK_GAP_SECONDS = float(os.getenv("ACTIVITY_CHUNK_GAP_SECONDS", "120"))

# --- ChromaDB vector store ---
CHROMA_ENABLED = os.getenv("CHROMA_ENABLED", "true").lower() in {"1", "true", "yes"}
CHROMA_PATH = Path(os.getenv("CHROMA_PATH", str(DATA_DIR / "chroma")))
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "activity_chunks")
CHROMA_LOG_EMBEDDINGS = os.getenv("CHROMA_LOG_EMBEDDINGS", "true").lower() in {"1", "true", "yes"}

# --- Storage: cleaned text lives in SQLite + ChromaDB (not per-frame txt files) ---
SAVE_FRAME_OCR_FILES = os.getenv("SAVE_FRAME_OCR_FILES", "false").lower() in {"1", "true", "yes"}
SAVE_ACTIVITY_JSON_FILES = os.getenv("SAVE_ACTIVITY_JSON_FILES", "false").lower() in {"1", "true", "yes"}
# Rolling frame JPG retention: keep the newest N on disk (e.g. 300 → delete 100 oldest → 200 latest).
# Never deletes .txt, .json, or Chroma embeddings. Only removes JPGs already indexed in Chroma.
DELETE_FRAME_IMAGES_AFTER_CHROMA_INDEX = os.getenv(
    "DELETE_FRAME_IMAGES_AFTER_CHROMA_INDEX",
    "true",
).lower() in {"1", "true", "yes"}
FRAME_IMAGE_RETENTION_MAX = int(os.getenv("FRAME_IMAGE_RETENTION_MAX", "200"))
FRAME_IMAGE_RETENTION_PURGE = int(os.getenv("FRAME_IMAGE_RETENTION_PURGE", "100"))

# --- Meeting audio + calendar highlights ---
MEETING_AUDIO_SYNC_ENABLED = os.getenv("MEETING_AUDIO_SYNC_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
MEETING_AUDIO_SEARCH_LIMIT = int(os.getenv("MEETING_AUDIO_SEARCH_LIMIT", "20"))
MEETING_AUDIO_SEARCH_PADDING_SECONDS = float(
    os.getenv("MEETING_AUDIO_SEARCH_PADDING_SECONDS", "300")
)
MEETING_TRANSCRIPT_SYNC_INTERVAL_SECONDS = float(
    os.getenv("MEETING_TRANSCRIPT_SYNC_INTERVAL_SECONDS", "30.0")
)
MEETING_TRANSCRIPT_RETRY_MAX_AGE_HOURS = float(
    os.getenv("MEETING_TRANSCRIPT_RETRY_MAX_AGE_HOURS", "6")
)
MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES = int(
    os.getenv("MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES", "30")
)
CALENDAR_DEFAULT_TIMEZONE = os.getenv("CALENDAR_DEFAULT_TIMEZONE", "UTC")

# --- OpenAI daily insights (OCR activity → today summary + tomorrow predictions) ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
SUMMARY_ENABLED = os.getenv("SUMMARY_ENABLED", "true").lower() in {"1", "true", "yes"}
SUMMARY_POLL_INTERVAL_SECONDS = float(os.getenv("SUMMARY_POLL_INTERVAL_SECONDS", "60"))
SUMMARY_MAX_CHUNK_PREVIEW_CHARS = int(os.getenv("SUMMARY_MAX_CHUNK_PREVIEW_CHARS", "0"))
SUMMARY_MAX_INPUT_CHARS = int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "8000"))
# Max OCR batches per day (0 = unlimited, expand batch size to fit all chunks)
SUMMARY_MAX_BATCHES_PER_HOUR = int(os.getenv("SUMMARY_MAX_BATCHES_PER_HOUR", "0"))
# Legacy; daily-only summaries no longer use period windows.
SUMMARY_PERIOD_MINUTES = int(os.getenv("SUMMARY_PERIOD_MINUTES", "60"))

# --- Google Calendar ---
GOOGLE_CALENDAR_CREDENTIALS_PATH = Path(
    os.getenv(
        "GOOGLE_CALENDAR_CREDENTIALS_PATH",
        str(DATA_DIR / "google_calendar_credentials.json"),
    )
)
GOOGLE_CALENDAR_TOKEN_PATH = Path(
    os.getenv(
        "GOOGLE_CALENDAR_TOKEN_PATH",
        str(DATA_DIR / "google_calendar_token.json"),
    )
)
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CALENDAR_REDIRECT_URI = os.getenv(
    "GOOGLE_CALENDAR_REDIRECT_URI",
    "http://127.0.0.1:8000/api/v1/calendar/auth/callback",
)
GOOGLE_CALENDAR_SCOPES = [
    scope.strip()
    for scope in os.getenv(
        "GOOGLE_CALENDAR_SCOPES",
        "https://www.googleapis.com/auth/calendar",
    ).split(",")
    if scope.strip()
]

# --- WhatsApp Cloud API pipeline (webhook → AI classify → suggestions) ---
WHATSAPP_ENABLED = os.getenv("WHATSAPP_ENABLED", "true").lower() in {"1", "true", "yes"}
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v21.0")
WHATSAPP_API_BASE = os.getenv("WHATSAPP_API_BASE", "https://graph.facebook.com").rstrip("/")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip() or None
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip() or None
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip() or None
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip() or None
WHATSAPP_POLL_INTERVAL_SECONDS = float(os.getenv("WHATSAPP_POLL_INTERVAL_SECONDS", "30"))
WHATSAPP_HISTORY_CONTEXT_LIMIT = int(os.getenv("WHATSAPP_HISTORY_CONTEXT_LIMIT", "20"))
# Free-form replies are only allowed within this many hours of the customer's last message.
WHATSAPP_CUSTOMER_WINDOW_HOURS = float(os.getenv("WHATSAPP_CUSTOMER_WINDOW_HOURS", "24"))
# Default meeting length (minutes) when the conversation gives no end time.
WHATSAPP_DEFAULT_MEETING_MINUTES = int(os.getenv("WHATSAPP_DEFAULT_MEETING_MINUTES", "60"))
# Default language code used when sending a template message.
WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE = os.getenv("WHATSAPP_TEMPLATE_DEFAULT_LANGUAGE", "en_US")
# When a meeting time is free, auto-create a Google Calendar event (requires calendar OAuth).
WHATSAPP_AUTO_ADD_CALENDAR = os.getenv("WHATSAPP_AUTO_ADD_CALENDAR", "false").lower() in {
    "1",
    "true",
    "yes",
}
# When true, only the WhatsApp worker starts at boot (no screenpipe/OCR/activity/summary).
WHATSAPP_ONLY_MODE = os.getenv("WHATSAPP_ONLY_MODE", "false").lower() in {"1", "true", "yes"}

# --- Anand Sandesh subscriptions (PostgreSQL anand_sandesh_subscription) ---
# Razorpay plan IDs map to category: 1-year → General(I), 5-year → Five Years(I)
ANAND_SANDESH_PLAN_ID_ONE_YEAR = os.getenv("ANAND_SANDESH_PLAN_ID_ONE_YEAR", "").strip() or None
ANAND_SANDESH_PLAN_ID_FIVE_YEAR = os.getenv("ANAND_SANDESH_PLAN_ID_FIVE_YEAR", "").strip() or None
