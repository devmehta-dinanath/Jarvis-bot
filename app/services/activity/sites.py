import re
from urllib.parse import urlparse

from app.services.activity.categories import ActivityCategory

_URL_IN_TEXT = re.compile(
    r"(?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s]*)?",
    re.IGNORECASE,
)
MEETING_TEXT_SIGNALS = re.compile(
    r"(meet\.google\.com|zoom\.us/|webex\.com|"
    r"gmeetid|google meet|daily standup|"
    r"\(presenting|\(annotating|leave call|you're presenting|"
    r"share screen|participants?\s*\(|waiting room|unmute|mute mic)",
    re.IGNORECASE,
)


# Order matters: first match wins — put specific rules before broad ones.
_SITE_RULES: list[tuple[ActivityCategory, tuple[str, ...], tuple[str, ...]]] = [
    (
        ActivityCategory.MEETINGS,
        (
            "meet.google.com",
            "zoom.us",
            "teams.microsoft.com/l/meetup",
            "teams.live.com/meet",
            "whereby.com",
            "webex.com",
        ),
        ("google meet", "zoom meeting", "zoom -", "webex", "in a meeting", "you're in the meeting"),
    ),
    (
        ActivityCategory.CODE,
        (
            "github.com",
            "gitlab.com",
            "bitbucket.org",
            "stackoverflow.com",
            "stackexchange.com",
            "localhost:",
            "127.0.0.1",
            "0.0.0.0:",
            "replit.com",
            "codesandbox.io",
        ),
        (
            "visual studio code",
            "pull request",
            "stack overflow",
            "merge request",
            "pycharm",
            "intellij",
            ".py -",
            ".ts -",
            ".js -",
        ),
    ),
    (
        ActivityCategory.EMAIL,
        (
            "mail.google.com",
            "outlook.live.com/mail",
            "outlook.office.com/mail",
            "mail.yahoo.com",
            "proton.me/mail",
            "protonmail.com",
        ),
        ("gmail", "inbox -", "outlook mail", "yahoo mail"),
    ),
    (
        ActivityCategory.CALENDAR,
        ("calendar.google.com", "outlook.live.com/calendar", "outlook.office.com/calendar"),
        ("google calendar", "outlook calendar", "calendar -"),
    ),
    (
        ActivityCategory.MESSAGES,
        (
            "slack.com",
            "discord.com",
            "web.whatsapp.com",
            "messages.google.com",
            "web.telegram.org",
            "teams.microsoft.com/l/channel",
            "teams.microsoft.com/_",
        ),
        ("slack", "discord", "whatsapp", "google chat", "microsoft teams"),
    ),
    (
        ActivityCategory.DOCUMENTS,
        (
            "docs.google.com",
            "drive.google.com",
            "notion.so",
            "figma.com",
            "canva.com",
            "dropbox.com",
            "onedrive.live.com",
        ),
        ("google docs", "google sheets", "google slides", "notion", "figma", "canva"),
    ),
    (
        ActivityCategory.LINKEDIN,
        ("linkedin.com",),
        ("linkedin",),
    ),
    (
        ActivityCategory.SOCIAL,
        (
            "twitter.com",
            "x.com",
            "facebook.com",
            "instagram.com",
            "reddit.com",
            "threads.net",
            "tiktok.com",
            "pinterest.com",
            "mastodon.",
        ),
        ("twitter", "x.com", "facebook", "instagram", "reddit", "threads"),
    ),
    (
        ActivityCategory.SHOPPING,
        ("amazon.", "ebay.", "flipkart.", "shopify.", "etsy.com", "walmart.com"),
        ("amazon", "ebay", "flipkart", "shop"),
    ),
    (
        ActivityCategory.VIDEO,
        ("youtube.com", "youtu.be", "netflix.com", "twitch.tv", "vimeo.com", "primevideo."),
        ("youtube", "netflix", "twitch", "prime video"),
    ),
]


def classify_from_url(url: str | None) -> ActivityCategory | None:
    if not url or not url.strip():
        return None

    parsed = urlparse(url.strip())
    haystack = f"{parsed.netloc.casefold()}{parsed.path.casefold()}"

    for category, url_patterns, _window_patterns in _SITE_RULES:
        if any(pattern in haystack for pattern in url_patterns):
            return category
    return None


def classify_from_text(text: str | None) -> ActivityCategory | None:
    """Detect category from OCR/accessibility text (URLs and UI phrases)."""
    if not text or not text.strip():
        return None

    haystack = text.casefold()
    if MEETING_TEXT_SIGNALS.search(haystack):
        return ActivityCategory.MEETINGS

    for match in _URL_IN_TEXT.findall(text):
        normalized = match if "://" in match else f"https://{match}"
        category = classify_from_url(normalized)
        if category is not None:
            return category

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if 8 <= len(line) <= 120:
            category = classify_from_window(line)
            if category is not None:
                return category

    return None


def classify_from_window(window_name: str | None) -> ActivityCategory | None:
    if not window_name or not window_name.strip():
        return None

    window = window_name.casefold()
    for category, _url_patterns, window_patterns in _SITE_RULES:
        if any(pattern in window for pattern in window_patterns):
            return category
    return None
