import logging
from datetime import datetime
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)


def extract_frames_from_video(
    recording: models.Recording,
    video_path: Path,
    frames_dir: Path,
    frame_interval_seconds: float,
    db: Session,
) -> None:
    """Extract JPG frames from a video file at a fixed interval."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 1.0
    step = max(int(fps * frame_interval_seconds), 1)
    frame_number = 0
    saved = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_number % step == 0:
            saved += 1
            frame_path = frames_dir / f"frame_{saved:06d}.jpg"
            if not cv2.imwrite(str(frame_path), frame):
                raise RuntimeError(f"Failed to write frame: {frame_path}")

            db.add(
                models.Frame(
                    recording_id=recording.id,
                    frame_index=saved,
                    file_path=str(frame_path),
                    ocr_status="queued",
                    activity_status="pending",
                    captured_at=datetime.utcnow(),
                )
            )
        frame_number += 1

    cap.release()
    recording.total_frames = saved  
    db.commit()
    logger.info(
        "[CAPTURE] Extracted %s frames from %s → %s",
        saved,
        video_path.name,
        frames_dir,
    )
