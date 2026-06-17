"""Memory subsystem models: Memory, MemoryLink, MemoryCluster, MemoryClusterMember, MemoryCandidate, Entity, EntityRelation."""

from __future__ import annotations

from datetime import datetime, UTC

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ._base import Base


class Memory(Base):
    """Факты о владельце и контактах, извлекаемые из переписок и разговоров с ботом."""

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_mem_active_contact", "is_active", "contact_id"),
        Index("ix_mem_user_active", "user_id", "is_active"),
        Index("ix_memories_user_type_active", "user_id", "memory_type", "is_active"),
        Index("ix_memory_tags", "tags"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )
    fact: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )  # positive, negative, neutral
    source: Mapped[str] = mapped_column(String(16), default="chat")  # chat, user, auto
    confidence: Mapped[float] = mapped_column(
        Float, default=0.5
    )  # 0.0–1.0 уверенность в факте
    times_mentioned: Mapped[int] = mapped_column(
        Integer, default=1
    )  # сколько раз подтверждён
    source_quality: Mapped[float] = mapped_column(
        Float, default=0.5
    )  # 0.0–1.0 надёжность источника (chat < user < auto)
    corroboration_count: Mapped[int] = mapped_column(
        Integer, default=0
    )  # сколько раз факт подтверждён другими источниками
    last_corroborated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # когда последний раз подтверждён
    extraction_quality: Mapped[float] = mapped_column(
        Float, default=0.5
    )  # 0.0–1.0 качество самого извлечения (прямое утверждение > намёк)
    message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # исходное сообщение
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True
    )  # активен / опровергнут
    cluster_topic: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # тема-кластер
    embedding_hash: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )  # хеш для дедупликации
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    validity_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    validity_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    importance: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0–1.0
    decay_rate: Mapped[float] = mapped_column(Float, default=0.07)  # скорость забывания
    memory_tier: Mapped[int] = mapped_column(
        Integer, default=1
    )  # 1=эпизод, 2=недельное, 3=месячное
    temporal_layer: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )
    # null | "recent" (≤7d) | "medium" (8-30d) | "longterm" (>30d)

    tags: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )  # comma-separated: "работа,деньги"

    memory_type: Mapped[str | None] = mapped_column(
        String(24), nullable=True, index=True
    )
    # personal | contact_fact | relationship | task | preference | temporary

    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    related_memory_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, index=True
    )  # ссылка на другой Memory.id
    relation_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # cause, effect, contradicts, supports, continues, example_of


class MemoryVersion(Base):
    """Аудит-трейл правок памяти — who changed what, when, and why."""

    __tablename__ = "memory_versions"
    __table_args__ = (
        Index("ix_mv_memory_version", "memory_id", "version", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(default=1, index=True)
    fact_text: Mapped[str] = mapped_column(Text)
    edited_by: Mapped[str] = mapped_column(
        String(32), default="user"
    )  # "user" | "system" | "agent"
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemoryLink(Base):
    """Many-to-many связи между фактами памяти с весами."""

    __tablename__ = "memory_links"
    __table_args__ = (
        Index("ix_ml_source", "source_id"),
        Index("ix_ml_target", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    weight: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0-1.0 сила связи
    relation_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # cause/effect/contradicts/supports/continues/co_temporal/co_entity/preceded
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class MemoryCluster(Base):
    """Группа связанных фактов по теме."""

    __tablename__ = "memory_clusters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # LLM-саммари кластера
    fact_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class MemoryClusterMember(Base):
    """Связь many-to-many: факт → кластер."""

    __tablename__ = "memory_cluster_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    memory_id: Mapped[int] = mapped_column(
        ForeignKey("memories.id", ondelete="CASCADE"), index=True
    )
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("memory_clusters.id", ondelete="CASCADE"), index=True
    )
    relevance_score: Mapped[float] = mapped_column(Float, default=0.5)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class MemoryCandidate(Base):
    """Факты на подтверждение — черновик памяти."""

    __tablename__ = "memory_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fact: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    memory_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="chat")
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    decay_rate: Mapped[float] = mapped_column(Float, default=0.07)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )


class WorkingMemory(Base):
    """Рабочая память (scratchpad) — LLM-доступный key-value store
    для промежуточных результатов внутри задачи. Автоочистка через 1 час."""

    __tablename__ = "working_memory"
    __table_args__ = (
        Index("ix_wm_user_key", "user_id", "key", unique=True),
        Index("ix_wm_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )  # автоочистка через 1 час


# ── P3: Episodic Memory ──────────────────────────────────────────────────


class Episode(Base):
    """Эпизод — событие или разговор с полным контекстом.

    В отличие от Memory (которая хранит отдельные факты), Episode
    сохраняет целостный контекст взаимодействия: кто участвовал, когда,
    эмоциональный тон, итог.  Используется для ночной рефлексии и
    извлечения фактов, которые smart_extractor пропустил при первом проходе.
    """

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # LLM-саммари эпизода
    raw_sample: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # первые ~500 символов диалога
    emotional_valence: Mapped[float | None] = mapped_column(
        nullable=True
    )  # -1.0 (негатив) … 1.0 (позитив)
    importance: Mapped[float] = mapped_column(default=0.5)  # 0.0–1.0
    memory_ids: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON: [1,2,3] — ID связанных фактов Memory


class EpisodeContact(Base):
    """Связь эпизода с контактами — многие-ко-многим."""

    __tablename__ = "episode_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[int] = mapped_column(BigInteger)
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # "participant", "mentioned"


# ── Knowledge Graph: Entity & EntityRelation ─────────────────────────────


class Entity(Base):
    """Сущность в графе знаний: персона, проект, место, компания, тема.

    Извлекается LLM из фактов памяти и связывается через EntityRelation.
    """

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entity_user_type", "user_id", "type"),
        UniqueConstraint("user_id", "name", "type", name="uq_entity_user_name_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(
        String(32)
    )  # person, project, place, company, topic
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class EntityRelation(Base):
    """Связь между двумя сущностями в графе знаний.

    relation — тип связи: works_at, friend_of, expert_in, located_in, etc.
    weight — сила связи 0.0-1.0 (1.0 = высокая уверенность).
    source — источник: extraction (LLM), user_stated (пользователь указал явно).
    """

    __tablename__ = "entity_relations"
    __table_args__ = (
        Index("ix_er_user_relation", "user_id", "source_id", "target_id"),
        UniqueConstraint(
            "user_id",
            "source_id",
            "target_id",
            "relation",
            name="uq_er_user_src_tgt_rel",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("entities.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(
        String(64)
    )  # "works_at", "friend_of", "expert_in", "located_in"
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # "extraction", "user_stated"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
