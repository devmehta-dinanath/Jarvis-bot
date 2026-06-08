import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

PaddleOCR = None
_import_error: Exception | None = None

try:
    from paddleocr import PaddleOCR as _PaddleOCR
    PaddleOCR = _PaddleOCR
except Exception as exc:  # ImportError, missing libGL, paddle mismatch, etc.
    _import_error = exc
    logger.error("PaddleOCR import failed: %s", exc)


class OCRDependencyError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_ocr_engine() -> "PaddleOCR":
    if PaddleOCR is None:
        detail = f" ({_import_error})" if _import_error else ""
        raise OCRDependencyError(
            "PaddleOCR is not available in this environment." + detail
        )
    logger.info("[OCR] PaddleOCR engine loaded")
    return PaddleOCR(use_angle_cls=True, lang="en", show_log=False)


def extract_text(image_path: str) -> str:
    engine = get_ocr_engine()
    result = engine.ocr(str(Path(image_path)), cls=True)
    lines: list[str] = []
    for item in result or []:
        for block in item or []:
            if len(block) > 1 and block[1]:
                lines.append(str(block[1][0]))
    return "\n".join(lines).strip()
