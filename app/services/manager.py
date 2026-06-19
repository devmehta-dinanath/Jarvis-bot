import logging

from app.config import AUTO_START_SERVICES, WHATSAPP_ONLY_MODE
from app.services.activity.service import ActivityClassificationService
from app.services.meetings.sync_worker import MeetingTranscriptSyncService
from app.services.paddle_ocr.service import PaddleOcrService
from app.services.screenpipe.service import ScreenpipeService
from app.services.summary.service import SummaryService
from app.services.whatsapp.service import WhatsAppService

logger = logging.getLogger(__name__)


class ServiceManager:
    """Starts and stops background services when the API server boots."""

    def __init__(self) -> None:
        self.screenpipe = ScreenpipeService()
        self.paddle_ocr = PaddleOcrService()
        self.activity = ActivityClassificationService()
        self.meeting_transcripts = MeetingTranscriptSyncService()
        self.summary = SummaryService()
        self.whatsapp = WhatsAppService()
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started:
            return
        if not AUTO_START_SERVICES:
            logger.info("AUTO_START_SERVICES is disabled; background services not started")
            self._started = True
            return

        if WHATSAPP_ONLY_MODE:
            self.whatsapp.start()
            logger.info("Background services started (whatsapp only — screenpipe/OCR disabled)")
            self._started = True
            return

        self.screenpipe.start()
        self.paddle_ocr.start()
        self.activity.start()
        self.meeting_transcripts.start()
        self.summary.start()
        self.whatsapp.start()
        logger.info(
            "Background services started (screenpipe + paddle_ocr + activity + meeting_transcripts + summary + whatsapp)"
        )
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        if WHATSAPP_ONLY_MODE:
            self.whatsapp.stop()
        else:
            self.screenpipe.stop()
            self.paddle_ocr.stop()
            self.activity.stop()
            self.meeting_transcripts.stop()
            self.summary.stop()
            self.whatsapp.stop()
        self._started = False
        logger.info("Background services stopped")


service_manager = ServiceManager()
