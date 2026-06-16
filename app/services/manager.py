import logging

from app.config import AUTO_START_SERVICES
from app.services.activity.service import ActivityClassificationService
from app.services.meetings.sync_worker import MeetingTranscriptSyncService
from app.services.paddle_ocr.service import PaddleOcrService
from app.services.screenpipe.service import ScreenpipeService
from app.services.summary.service import SummaryService

logger = logging.getLogger(__name__)


class ServiceManager:
    """Starts and stops background services when the API server boots."""

    def __init__(self) -> None:
        self.screenpipe = ScreenpipeService()
        self.paddle_ocr = PaddleOcrService()
        self.activity = ActivityClassificationService()
        self.meeting_transcripts = MeetingTranscriptSyncService()
        self.summary = SummaryService()
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            return
        if AUTO_START_SERVICES:
            self.screenpipe.start()
            self.paddle_ocr.start()
            self.activity.start()
            self.meeting_transcripts.start()
            self.summary.start()
            logger.info(
                "Background services started (screenpipe + paddle_ocr + activity + meeting_transcripts + summary)"
            )
        else:
            logger.info("AUTO_START_SERVICES is disabled; background services not started")
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.screenpipe.stop()
        self.paddle_ocr.stop()
        self.activity.stop()
        self.meeting_transcripts.stop()
        self.summary.stop()
        self._started = False
        logger.info("Background services stopped")


service_manager = ServiceManager()
