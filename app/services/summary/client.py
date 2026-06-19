import logging

from openai import APIError, OpenAI, RateLimitError

from app.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)


class SummaryOpenAIError(Exception):
    """OpenAI call failed for summary generation."""

    def __init__(self, message: str, *, quota_exceeded: bool = False) -> None:
        super().__init__(message)
        self.quota_exceeded = quota_exceeded


_ACTIVITY_BATCH_SYSTEM = (
    "You summarize OCR text extracted from the user's computer screen. "
    "This is ONE segment of a longer day — only describe activity present in this segment. "
    "Infer tasks from app names, window titles, URLs, and on-screen text. "
    "Ignore OCR noise (menu bars, UI chrome). "
    "Use bullet points. Be factual; do not invent details not supported by the text."
)

_ACTIVITY_MERGE_SYSTEM = (
    "You merge segment summaries from the same day of screen OCR activity into one "
    "complete activity digest. Preserve all distinct apps, tasks, and meetings mentioned "
    "across segments. Remove duplicates. Do not add facts that are not in the segment summaries."
)

_DAILY_INSIGHT_SYSTEM = (
    "You produce end-of-day insights from screen activity OCR. "
    "Use exactly this output format:\n\n"
    "TODAY:\n"
    "<bullet points: focus areas, apps used, meetings seen on screen, accomplishments>\n\n"
    "TOMORROW:\n"
    "### Scheduled meetings\n"
    "<list confirmed calendar events if provided; otherwise say none scheduled>\n"
    "### Likely tasks\n"
    "<tasks inferred from unfinished work, todos, or patterns in today's activity>\n"
    "### Follow-ups\n"
    "<items that need a reply, review, or continuation>\n\n"
    "Be factual for TODAY. For TOMORROW, clearly separate confirmed calendar events "
    "from predictions inferred from today's activity. Do not invent calendar events "
    "that are not listed in the calendar section."
)


def get_openai_client() -> OpenAI | None:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def summarize_activity_multi_pass(
    *,
    period_label: str,
    ocr_batches: list[str],
) -> tuple[str, int, int]:
    """Summarize all OCR batches for one day, merging when split across multiple calls."""
    if not ocr_batches:
        raise ValueError("ocr_batches must not be empty")

    total_prompt = 0
    total_completion = 0
    segment_summaries: list[str] = []

    for index, ocr_text in enumerate(ocr_batches, start=1):
        logger.info(
            "[SUMMARY] OpenAI segment %s/%s for %s (%s chars OCR)",
            index,
            len(ocr_batches),
            period_label,
            len(ocr_text),
        )
        segment_text, prompt_tokens, completion_tokens = summarize_activity_batch(
            period_label=period_label,
            ocr_text=ocr_text,
            batch_index=index,
            batch_total=len(ocr_batches),
        )
        segment_summaries.append(segment_text)
        total_prompt += prompt_tokens
        total_completion += completion_tokens
        logger.info(
            "[SUMMARY] Day segment %s/%s summarized (%s chars OCR → %s chars summary)",
            index,
            len(ocr_batches),
            len(ocr_text),
            len(segment_text),
        )

    if len(segment_summaries) == 1:
        return segment_summaries[0], total_prompt, total_completion

    logger.info(
        "[SUMMARY] Merging %s segment(s) for %s",
        len(segment_summaries),
        period_label,
    )
    merged_text, prompt_tokens, completion_tokens = merge_activity_summaries(
        period_label=period_label,
        segment_summaries=segment_summaries,
    )
    return (
        merged_text,
        total_prompt + prompt_tokens,
        total_completion + completion_tokens,
    )


def summarize_activity_batch(
    *,
    period_label: str,
    ocr_text: str,
    batch_index: int,
    batch_total: int,
) -> tuple[str, int, int]:
    user_content = (
        f"Date: {period_label}\n"
        f"Segment: {batch_index} of {batch_total}\n\n"
        f"OCR activity lines:\n{ocr_text}\n\n"
        "Write a factual summary for this segment only:"
    )
    return _chat(_ACTIVITY_BATCH_SYSTEM, user_content, max_tokens=500)


def merge_activity_summaries(
    *,
    period_label: str,
    segment_summaries: list[str],
) -> tuple[str, int, int]:
    parts = [f"Date: {period_label}", ""]
    for index, summary in enumerate(segment_summaries, start=1):
        parts.append(f"Segment {index} summary:")
        parts.append(summary)
        parts.append("")
    parts.append("Write one complete activity digest covering all segments above:")
    return _chat(_ACTIVITY_MERGE_SYSTEM, "\n".join(parts), max_tokens=800)


def summarize_daily_insight(
    *,
    day_label: str,
    tomorrow_label: str,
    activity_digest: str,
    calendar_context: str | None,
) -> tuple[str, str, int, int]:
    calendar_section = calendar_context or "No calendar connected or no events found."
    user_content = (
        f"Date: {day_label}\n"
        f"Tomorrow: {tomorrow_label}\n\n"
        f"Today's screen activity:\n{activity_digest}\n\n"
        f"Tomorrow's calendar:\n{calendar_section}\n\n"
        "Write the daily insight using the required TODAY / TOMORROW format:"
    )
    raw_text, prompt_tokens, completion_tokens = _chat(
        _DAILY_INSIGHT_SYSTEM,
        user_content,
        max_tokens=900,
    )
    today_text, tomorrow_text = parse_daily_insight_response(raw_text)
    return today_text, tomorrow_text, prompt_tokens, completion_tokens


def parse_daily_insight_response(text: str) -> tuple[str, str]:
    marker = "\nTOMORROW:\n"
    if marker in text:
        today_part, tomorrow_part = text.split(marker, 1)
        today = today_part.removeprefix("TODAY:").strip()
        return today, tomorrow_part.strip()
    if text.upper().startswith("TODAY:"):
        return text[6:].strip(), ""
    return text.strip(), ""


def _chat(system: str, user_content: str, *, max_tokens: int = 500) -> tuple[str, int, int]:
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    logger.debug(
        "[SUMMARY] OpenAI call model=%s input_chars=%s max_tokens=%s",
        OPENAI_MODEL,
        len(user_content),
        max_tokens,
    )

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except RateLimitError as exc:
        quota_exceeded = _is_quota_exceeded(exc)
        if quota_exceeded:
            logger.error(
                "[SUMMARY] OpenAI quota exceeded — check billing at "
                "https://platform.openai.com/account/billing"
            )
        else:
            logger.warning("[SUMMARY] OpenAI rate limited (retry later): %s", exc)
        raise SummaryOpenAIError(str(exc), quota_exceeded=quota_exceeded) from exc
    except APIError as exc:
        logger.error("[SUMMARY] OpenAI API error: %s", exc)
        raise SummaryOpenAIError(str(exc)) from exc

    choice = response.choices[0].message.content or ""
    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0
    logger.info(
        "[SUMMARY] OpenAI %s tokens in=%s out=%s",
        OPENAI_MODEL,
        prompt_tokens,
        completion_tokens,
    )
    return choice.strip(), prompt_tokens, completion_tokens


def _is_quota_exceeded(exc: RateLimitError) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", {})
        if error.get("code") == "insufficient_quota":
            return True
    return "insufficient_quota" in str(exc)
