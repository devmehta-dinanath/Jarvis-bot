import re

from app.services.activity.categories import ActivityCategory
from app.services.activity.sites import (
    MEETING_TEXT_SIGNALS,
    classify_from_text,
    classify_from_url,
    classify_from_window,
)


_EMAIL_APPS = (
    "mail",
    "gmail",
    "outlook",
    "thunderbird",
    "superhuman",
    "spark",
    "mutt",
)
_MESSAGES_APPS = (
    "slack",
    "discord",
    "telegram",
    "whatsapp",
    "signal",
    "messages",
    "imessage",
    "mattermost",
    "rocket.chat",
)
_BROWSER_APPS = (
    "chrome",
    "chromium",
    "firefox",
    "safari",
    "edge",
    "brave",
    "opera",
    "arc",
    "vivaldi",
)
_DOCUMENT_APPS = (
    "docs",
    "word",
    "notion",
    "obsidian",
    "libreoffice",
    "pages",
    "preview",
    "acrobat",
    "pdf",
    "sheets",
    "excel",
    "numbers",
    "keynote",
    "powerpoint",
    "slides",
)
_CODE_APPS = (
    "code",
    "cursor",
    "vscode",
    "visual studio",
    "intellij",
    "pycharm",
    "webstorm",
    "sublime",
    "neovim",
    "vim",
    "antigravity",
    "windsurf",
)
_MEETING_APPS = (
    "zoom",
    "meet",
    "facetime",
    "webex",
    "gotomeeting",
    "around",
    "whereby",
)
_CALENDAR_APPS = (
    "calendar",
    "gnome-calendar",
    "ical",
)

_EMAIL_KEYWORDS = re.compile(
    r"\b(inbox|sent mail|drafts|unread messages|subject:|bcc:|cc:)\b",
    re.IGNORECASE,
)
_MESSAGES_KEYWORDS = re.compile(
    r"\b(slack|discord|direct message|#general|typing\.\.\.|unread channel)\b",
    re.IGNORECASE,
)
_MEETING_KEYWORDS = re.compile(
    r"\b(meeting|participants|mute|unmute|share screen|join call|leave call|waiting room)\b",
    re.IGNORECASE,
)
_DOCUMENT_KEYWORDS = re.compile(
    r"\b(document|spreadsheet|presentation|untitled document|\.docx?|\.pdf|\.xlsx?|\.pptx?)\b",
    re.IGNORECASE,
)
_LINKEDIN_KEYWORDS = re.compile(
    r"\b(linkedin|connect|endorse|people also viewed|job alert|my network)\b",
    re.IGNORECASE,
)
_SOCIAL_KEYWORDS = re.compile(
    r"\b(twitter|facebook|instagram|reddit|tiktok|retweet|news feed|timeline)\b",
    re.IGNORECASE,
)
_CODE_KEYWORDS = re.compile(
    r"\b(def |class |import |function |const |git commit|git push|breakpoint|pull request)\b",
    re.IGNORECASE,
)


def classify_activity(
    *,
    app_name: str | None,
    window_name: str | None = None,
    browser_url: str | None = None,
    text: str | None = None,
) -> ActivityCategory:
    """Classify activity using OCR text first, then capture metadata, then app heuristics."""
    app = (app_name or "").casefold()
    window = (window_name or "").casefold()
    ocr = text or ""
    combined = f"{app} {window} {ocr[:800]}".casefold()

    if ocr and MEETING_TEXT_SIGNALS.search(ocr):
        return ActivityCategory.MEETINGS

    text_category = classify_from_text(ocr)
    if text_category is not None:
        return text_category

    url_category = classify_from_url(browser_url)
    if url_category is not None:
        return url_category

    window_category = classify_from_window(window_name)
    if window_category is not None:
        return window_category

    if _matches_any(app, _MEETING_APPS) or _MEETING_KEYWORDS.search(combined):
        return ActivityCategory.MEETINGS

    if _matches_any(app, _EMAIL_APPS) or _EMAIL_KEYWORDS.search(combined):
        return ActivityCategory.EMAIL

    if _matches_any(app, _MESSAGES_APPS) and _MESSAGES_KEYWORDS.search(combined):
        return ActivityCategory.MESSAGES

    if _matches_any(app, _CODE_APPS) or _CODE_KEYWORDS.search(combined):
        return ActivityCategory.CODE

    if _matches_any(app, _CALENDAR_APPS) or "calendar" in window:
        return ActivityCategory.CALENDAR

    if _LINKEDIN_KEYWORDS.search(combined):
        return ActivityCategory.LINKEDIN

    if _matches_any(app, _DOCUMENT_APPS) or _DOCUMENT_KEYWORDS.search(combined):
        return ActivityCategory.DOCUMENTS

    if _matches_any(app, _MESSAGES_APPS):
        return ActivityCategory.MESSAGES

    if _matches_any(app, _BROWSER_APPS) or (browser_url and browser_url.strip()):
        if _SOCIAL_KEYWORDS.search(combined):
            return ActivityCategory.SOCIAL
        return ActivityCategory.BROWSING

    if _matches_any(app, _CODE_APPS):
        return ActivityCategory.CODE

    return ActivityCategory.OTHER


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for pattern in patterns)
