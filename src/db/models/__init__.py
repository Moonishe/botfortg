"""SQLAlchemy ORM models — backward-compatible re-exports."""

from src.db.models._base import Base, User
from src.db.models._auth import UserSettings, TelegramSession, ApiKey, LlmKeySlot
from src.db.models._contacts import Contact, ContactProfile, ConversationState
from src.db.models._messaging import (
    Message,
    AutoReplyLog,
    TranscriptionCache,
    PendingAction,
    Notification,
    NewsTopic,
    Commitment,
    IndexJob,
    Folder,
)
from src.db.models._memory import (
    Memory,
    MemoryLink,
    MemoryCluster,
    MemoryClusterMember,
    MemoryCandidate,
)
from src.db.models._cache import SmartCacheEntry
from src.db.models._embedding_cache import EmbeddingCacheEntry
from src.db.models._learning import (
    AgentCache,
    SelfProfile,
    AdaptivePersona,
    SoulSnapshot,
    Trajectory,
    Skill,
    SkillUsage,
    InstructionProfile,
    InstructionCandidate,
    InstructionEvent,
)

__all__ = [
    "Base",
    "User",
    "UserSettings",
    "TelegramSession",
    "ApiKey",
    "LlmKeySlot",
    "Contact",
    "ContactProfile",
    "ConversationState",
    "Message",
    "AutoReplyLog",
    "TranscriptionCache",
    "PendingAction",
    "Notification",
    "NewsTopic",
    "Commitment",
    "IndexJob",
    "Folder",
    "Memory",
    "MemoryLink",
    "MemoryCluster",
    "MemoryClusterMember",
    "MemoryCandidate",
    "SmartCacheEntry",
    "EmbeddingCacheEntry",
    "AgentCache",
    "SelfProfile",
    "AdaptivePersona",
    "SoulSnapshot",
    "Trajectory",
    "Skill",
    "SkillUsage",
    "InstructionProfile",
    "InstructionCandidate",
    "InstructionEvent",
]
