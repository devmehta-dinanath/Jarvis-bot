import re
from urllib.parse import urlparse

from app.services.activity.sites import _URL_IN_TEXT

_PRIORITY_HOSTS: tuple[str, ...] = (
    "meet.google.com",
    "mail.google.com",
    "calendar.google.com",
    "docs.google.com",
    "drive.google.com",
    "github.com",
    "gitlab.com",
    "linkedin.com",
    "slack.com",
    "discord.com",
    "web.whatsapp.com",
    "zoom.us",
    "teams.microsoft.com",
)

_COMMON_TLDS: frozenset[str] = frozenset(
    {
        "com",
        "org",
        "net",
        "io",
        "dev",
        "app",
        "ai",
        "co",
        "us",
        "in",
        "uk",
        "edu",
        "gov",
    }
)

_APP_PATTERNS: tuple[tuple[str, str], ...] = (
    ("google chrome", "Google Chrome"),
    ("chromium", "Chromium"),
    ("firefox", "Firefox"),
    ("microsoft edge", "Microsoft Edge"),
    ("cursor", "Cursor"),
    ("visual studio code", "VS Code"),
    ("antigravity", "Antigravity"),
    ("whatsapp", "WhatsApp"),
    ("slack", "Slack"),
    ("discord", "Discord"),
    ("zoom", "Zoom"),
    ("terminal", "Terminal"),
    ("gnome-terminal", "Terminal"),
    ("libreoffice", "LibreOffice"),
    ("notion", "Notion"),
    ("figma", "Figma"),
)

_WINDOW_LINE = re.compile(
    r"^(.{8,120})$",
)


def infer_metadata_from_text(text: str | None) -> dict[str, str | None]:
    """Infer app/window/url fields from OCR or Screenpipe accessibility text."""
    if not text or not text.strip():
        return {"app_name": None, "window_name": None, "browser_url": None}

    haystack = text.casefold()
    browser_url = _first_url(text)
    app_name = _infer_app_name(haystack, browser_url)
    window_name = _infer_window_name(text, browser_url)

    return {
        "app_name": app_name,
        "window_name": window_name,
        "browser_url": browser_url,
    }


def merge_metadata(
    *,
    app_name: str | None,
    window_name: str | None,
    browser_url: str | None,
    text: str | None,
) -> dict[str, str | None]:
    """Fill missing metadata fields using OCR/accessibility text."""
    inferred = infer_metadata_from_text(text)
    return {
        "app_name": app_name or inferred["app_name"],
        "window_name": window_name or inferred["window_name"],
        "browser_url": browser_url or inferred["browser_url"],
    }


def _first_url(text: str) -> str | None:
    matches: list[str] = []
    for match in _URL_IN_TEXT.findall(text):
        normalized = match if "://" in match else f"https://{match}"
        host = urlparse(normalized).netloc.casefold()
        if host and _is_plausible_host(host):
            matches.append(normalized)

    if not matches:
        return None

    for priority_host in _PRIORITY_HOSTS:
        for url in matches:
            if priority_host in urlparse(url).netloc.casefold():
                return url
    return matches[0]


def _is_plausible_host(host: str) -> bool:
    labels = host.split(".")
    if len(labels) < 2:
        return False
    tld = labels[-1]
    if tld not in _COMMON_TLDS:
        return False
    if any(len(label) < 2 for label in labels):
        return False
    return True


def _infer_app_name(haystack: str, browser_url: str | None) -> str | None:
    if browser_url:
        host = urlparse(browser_url).netloc.casefold()
        if host and not host.endswith(".local"):
            for pattern, name in _APP_PATTERNS:
                if pattern in ("google chrome", "chromium", "firefox", "microsoft edge"):
                    if pattern in haystack:
                        return name
            return "Google Chrome"

    for pattern, name in _APP_PATTERNS:
        if pattern in haystack:
            return name
    return None


def _infer_window_name(text: str, browser_url: str | None) -> str | None:
    if browser_url:
        parsed = urlparse(browser_url)
        if parsed.netloc:
            return parsed.netloc

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) < 8:
            continue
        lower = line.casefold()
        if any(token in lower for token in ("http://", "https://", "www.")):
            continue
        if _WINDOW_LINE.match(line) and not line.isdigit():
            return line[:500]
    return None
