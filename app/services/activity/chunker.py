from dataclasses import dataclass
from datetime import datetime, timedelta

from app import models
from app.config import ACTIVITY_CHUNK_GAP_SECONDS


@dataclass(frozen=True)
class FrameChunk:
    frames: tuple[models.Frame, ...]

    @property
    def app_name(self) -> str | None:
        for frame in self.frames:
            if frame.app_name:
                return frame.app_name
        return None

    @property
    def window_name(self) -> str | None:
        for frame in reversed(self.frames):
            if frame.window_name:
                return frame.window_name
        return None

    @property
    def browser_url(self) -> str | None:
        for frame in reversed(self.frames):
            if frame.browser_url:
                return frame.browser_url
        return None

    @property
    def timestamp(self) -> datetime:
        return _frame_timestamp(self.frames[0])

    @property
    
    
    def end_timestamp(self) -> datetime:
        return _frame_timestamp(self.frames[-1])


def chunk_frames(
    frames: list[models.Frame],
    gap_seconds: float = ACTIVITY_CHUNK_GAP_SECONDS,
) -> list[FrameChunk]:
    """Group consecutive frames by app and time proximity."""
    if not frames:
        return []

    sorted_frames = sorted(frames, key=_frame_timestamp)
    gap = timedelta(seconds=gap_seconds)
    chunks: list[list[models.Frame]] = []
    current: list[models.Frame] = [sorted_frames[0]]

    for frame in sorted_frames[1:]:
        prev = current[-1]
        same_app = _same_app(prev, frame)
        within_gap = _frame_timestamp(frame) - _frame_timestamp(prev) <= gap
        if same_app and within_gap:
            current.append(frame)
        else:
            chunks.append(current)
            current = [frame]

    chunks.append(current)
    return [FrameChunk(frames=tuple(group)) for group in chunks if group]


def _frame_timestamp(frame: models.Frame) -> datetime:
    return frame.captured_at or frame.processed_at or frame.created_at


def _same_app(left: models.Frame, right: models.Frame) -> bool:
    left_app = (left.app_name or "").casefold()
    right_app = (right.app_name or "").casefold()
    if left_app and right_app:
        return left_app == right_app
    if left_app or right_app:
        return False
    left_window = (left.window_name or "").casefold()
    right_window = (right.window_name or "").casefold()
    return bool(left_window and right_window and left_window == right_window)
