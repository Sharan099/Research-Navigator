"""SQLAlchemy tables. The same models work with PostgreSQL and test SQLite."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    doi: Mapped[str | None] = mapped_column(String(500), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    publication_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    authors: Mapped[list["PublicationAuthor"]] = relationship(
        back_populates="publication", cascade="all, delete-orphan", order_by="PublicationAuthor.position"
    )


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(Text)


class PublicationAuthor(Base):
    __tablename__ = "publication_authors"
    __table_args__ = (UniqueConstraint("publication_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    position: Mapped[int] = mapped_column(Integer)

    publication: Mapped[Publication] = relationship(back_populates="authors")
    author: Mapped[Author] = relationship()


class RawPublicationRecord(Base):
    __tablename__ = "raw_publication_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60))
    provider_work_id: Mapped[str] = mapped_column(String(120), index=True)
    # Preserve even malformed JSON entries (including scalars and null).
    payload: Mapped[object] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ingestion_run_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_runs.id"), nullable=True, index=True)


class SearchCache(Base):
    __tablename__ = "search_cache"

    fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent: Mapped[dict] = mapped_column(JSON)
    work_ids: Mapped[list] = mapped_column(JSON)
    total_matches: Mapped[int] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_valid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_rejected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_duplicates: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_excluded_dates: Mapped[int | None] = mapped_column(Integer, nullable=True)
    records_usable: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    visitor_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(120), default="New topic")
    draft: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    active_request: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(12))
    text: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ChatRun(Base):
    """One deduplicated turn and its safe, durable activity log."""

    __tablename__ = "chat_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="running")
    events: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ChatEvent(Base):
    __tablename__ = "chat_events"

    run_id: Mapped[str] = mapped_column(ForeignKey("chat_runs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class MessagePaper(Base):
    """Small searchable references; full result snapshots stay in messages."""

    __tablename__ = "message_papers"

    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), primary_key=True)
    work_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailyUsage(Base):
    __tablename__ = "daily_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    resource: Mapped[str] = mapped_column(String(30), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)
