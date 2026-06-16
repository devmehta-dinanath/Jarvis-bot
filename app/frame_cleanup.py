import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.config import (
    DELETE_FRAME_IMAGES_AFTER_CHROMA_INDEX,
    FRAME_IMAGE_RETENTION_MAX,
    FRAME_IMAGE_RETENTION_PURGE,
)

logger = logging.getLogger(__name__)


def enforce_frame_image_retention(
    db: Session,
    *,
    recording_id: int | None = None,
) -> int:
    """Delete oldest frame JPGs when over retention limit (processed frames only)."""
    if not DELETE_FRAME_IMAGES_AFTER_CHROMA_INDEX:
        return 0

    query = db.query(models.Frame).filter(models.Frame.activity_status == "processed")
    if recording_id is not None:
        query = query.filter(models.Frame.recording_id == recording_id)

    frames = query.order_by(models.Frame.id.asc()).all()
    on_disk = [
        frame
        for frame in frames
        if frame.file_path.lower().endswith((".jpg", ".jpeg", ".png"))
        and Path(frame.file_path).is_file()
    ]

    if len(on_disk) <= FRAME_IMAGE_RETENTION_MAX:
        return 0

    excess = len(on_disk) - FRAME_IMAGE_RETENTION_MAX
    delete_count = min(max(excess, FRAME_IMAGE_RETENTION_PURGE), len(on_disk))
    deleted = 0

    for frame in on_disk[:delete_count]:
        path = Path(frame.file_path)
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            logger.warning("[CLEANUP] Failed to delete frame image %s", path)

    if deleted:
        logger.info(
            "[CLEANUP] Removed %s old frame image(s) (%s on disk, max=%s)",
            deleted,
            len(on_disk) - deleted,
            FRAME_IMAGE_RETENTION_MAX,
        )
    return deleted
