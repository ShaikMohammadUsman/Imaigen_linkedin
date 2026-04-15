from enum import Enum


class MessageStatus(Enum):
    SENT = "sent"
    SKIPPED = "skipped"
    SKIPPED_NOT_CONNECTED = "skipped_not_connected"
    FALLBACK_PENDING = "fallback_pending"


class ProfileState(str, Enum):
    DISCOVERED = "discovered"
    ENRICHED = "enriched"
    SCREENED = "screened"
    PENDING = "pending"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
