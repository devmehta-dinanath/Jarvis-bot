import logging

from app.config import APP_ROLE, AUTO_START_SERVICES, is_client_role, is_server_role
from app.services.activity.service import ActivityClassificationService
from app.services.meetings.sync_worker import MeetingTranscriptSyncService
from app.services.paddle_ocr.service import PaddleOcrService
from app.services.screenpipe.service import ScreenpipeService
from app.services.sync.service import SyncUploaderService

logger = logging.getLogger(__name__)


class ServiceManager:
    """Starts and stops background services based on APP_ROLE."""

    def __init__(self) -> None:
        self.screenpipe = ScreenpipeService()
        self.paddle_ocr = PaddleOcrService()
        self.activity = ActivityClassificationService()
        self.meeting_transcripts = MeetingTranscriptSyncService()
        self.sync_uploader = SyncUploaderService()
        self._summary = None
        self._whatsapp = None
        self._started = False

    @property
    def summary(self):
        if self._summary is None:
            from app.services.summary.service import SummaryService

            self._summary = SummaryService()
        return self._summary

    @property
    def whatsapp(self):
        if self._whatsapp is None:
            from app.services.whatsapp.service import WhatsAppService

            self._whatsapp = WhatsAppService()
        return self._whatsapp

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

        if is_server_role():
            from app.services.whatsapp.auth import start_refresh_worker

            start_refresh_worker()
            self.summary.start()
            self.whatsapp.start()
            logger.info(
                "Background services started (server: summary + whatsapp, role=%s)",
                APP_ROLE,
            )
            self._started = True
            return

        if is_client_role():
            self.screenpipe.start()
            self.paddle_ocr.start()
            self.activity.start()
            self.meeting_transcripts.start()
            self.sync_uploader.start()
            logger.info(
                "Background services started (client: capture + sync, role=%s)",
                APP_ROLE,
            )
            self._started = True
            return

        logger.warning("Unknown APP_ROLE=%s; no workers started", APP_ROLE)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        if is_server_role():
            self.summary.stop()
            self.whatsapp.stop()
            from app.services.whatsapp.auth import stop_refresh_worker

            stop_refresh_worker()
        elif is_client_role():
            self.sync_uploader.stop()
            self.meeting_transcripts.stop()
            self.activity.stop()
            self.paddle_ocr.stop()
            self.screenpipe.stop()
        self._started = False
        logger.info("Background services stopped")


service_manager = ServiceManager()
