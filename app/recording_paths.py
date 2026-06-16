from dataclasses import dataclass
from pathlib import Path

from app.config import (
    ACTIVITY_DIR_NAME,
    FRAMES_DIR_NAME,
    MEDIA_ROOT,
    OCR_DIR_NAME,
    SCREENPIPE_DIR_NAME,
)


@dataclass(frozen=True)
class RecordingPaths:
    root: Path
    screenpipe: Path
    frames: Path
    ocr: Path
    activity: Path


def _recording_root(recording_id: int) -> Path:
    return MEDIA_ROOT / f"recording_{recording_id}"


def get_recording_paths(recording_id: int) -> RecordingPaths:
    root = _recording_root(recording_id)
    return RecordingPaths(
        root=root,
        screenpipe=root / SCREENPIPE_DIR_NAME,
        frames=root / FRAMES_DIR_NAME,
        ocr=root / OCR_DIR_NAME,
        activity=root / ACTIVITY_DIR_NAME,
    )


def ensure_recording_dirs(recording_id: int) -> RecordingPaths:
    paths = get_recording_paths(recording_id)
    paths.screenpipe.mkdir(parents=True, exist_ok=True)
    paths.frames.mkdir(parents=True, exist_ok=True)
    paths.ocr.mkdir(parents=True, exist_ok=True)
    return paths
