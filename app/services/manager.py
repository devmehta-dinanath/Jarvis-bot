import logging

from app.config import AUTO_START_SERVICES
from app.services.activity.service import ActivityClassificationService
from app.services.paddle_ocr.service import PaddleOcrService
from app.services.screenpipe.service import ScreenpipeService

logger = logging.getLogger(__name__)


class ServiceManager:
    """Starts and stops background services when the API server boots."""

    def __init__(self) -> None:
        self.screenpipe = ScreenpipeService()
        self.paddle_ocr = PaddleOcrService()
        self.activity = ActivityClassificationService()
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
            logger.info("Background services started (screenpipe + paddle_ocr + activity)")
        else:
            logger.info("AUTO_START_SERVICES is disabled; background services not started")
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.screenpipe.stop()
        self.paddle_ocr.stop()
        self.activity.stop()
        self._started = False
        logger.info("Background services stopped")


service_manager = ServiceManager()
