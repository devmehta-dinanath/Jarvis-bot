import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = DATA_DIR / "screenpipe.db"
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
SCREENPIPE_HEALTH_TIMEOUT_SECONDS = float(os.getenv("SCREENPIPE_HEALTH_TIMEOUT_SECONDS", "120"))

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

# --- Meeting audio + calendar highlights ---
MEETING_AUDIO_SYNC_ENABLED = os.getenv("MEETING_AUDIO_SYNC_ENABLED", "true").lower() in {
    "1",
    "true",
    "yes",
}
MEETING_AUDIO_SEARCH_LIMIT = int(os.getenv("MEETING_AUDIO_SEARCH_LIMIT", "20"))
MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES = int(
    os.getenv("MEETING_HIGHLIGHT_DEFAULT_DURATION_MINUTES", "30")
)
CALENDAR_DEFAULT_TIMEZONE = os.getenv("CALENDAR_DEFAULT_TIMEZONE", "UTC")

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
