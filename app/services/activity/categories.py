from enum import StrEnum


class ActivityCategory(StrEnum):
    EMAIL = "email"
    MESSAGES = "messages"
    BROWSING = "browsing"
    DOCUMENTS = "documents"
    MEETINGS = "meetings"
    LINKEDIN = "linkedin"
    SOCIAL = "social"
    SHOPPING = "shopping"
    VIDEO = "video"
    CODE = "code"
    CALENDAR = "calendar"
    OTHER = "other"


ALL_CATEGORIES = frozenset(ActivityCategory)
