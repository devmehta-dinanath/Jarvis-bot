from app.services.paddle_ocr.engine import OCRDependencyError, extract_text
from app.services.paddle_ocr.processor import process_frame, run_ocr_for_recording
from app.services.paddle_ocr.service import PaddleOcrService

__all__ = [
    "OCRDependencyError",
    "PaddleOcrService",
    "extract_text",
    "process_frame",
    "run_ocr_for_recording",
]
