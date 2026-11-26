"""Adopt the original local schema without replacing existing tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    def create(name, *columns):
        if name not in existing:
            op.create_table(name, *columns)

    create(
        "publications",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("openalex_id", sa.String(100), nullable=False, unique=True),
        sa.Column("doi", sa.String(500)),
        sa.Column("title", sa.Text),
        sa.Column("publication_year", sa.Integer),
        sa.Column("publication_date", sa.Date),
        sa.Column("publication_type", sa.String(80)),
        sa.Column("abstract", sa.Text),
        sa.Column("cited_by_count", sa.Integer, nullable=False),
        sa.Column("source", sa.Text),
        sa.Column("landing_page_url", sa.Text),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    create(
        "authors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("openalex_id", sa.String(100), unique=True),
        sa.Column("display_name", sa.Text, nullable=False),
    )
    create(
        "publication_authors",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("publication_id", sa.Integer, sa.ForeignKey("publications.id"), nullable=False),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("authors.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("publication_id", "position"),
    )
    create(
        "ingestion_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("records_received", sa.Integer, nullable=False),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    )
    create(
        "raw_publication_records",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("provider_work_id", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    create(
        "search_cache",
        sa.Column("fingerprint", sa.String(64), primary_key=True),
        sa.Column("intent", sa.JSON, nullable=False),
        sa.Column("work_ids", sa.JSON, nullable=False),
        sa.Column("total_matches", sa.Integer, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    create(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("draft", sa.Text, nullable=False),
        sa.Column("state", sa.JSON, nullable=False),
        sa.Column("active_request", sa.String(36)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    create(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("result", sa.JSON),
    )
    create(
        "chat_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("events", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON),
        sa.Column("error", sa.Text),
    )
    # Legacy databases may predate the publication-date column.
    from backend.app.migrations import migrate_publication_dates

    migrate_publication_dates(op.get_bind())


def downgrade():
    raise RuntimeError("Destructive schema rollback is disabled; restore a verified backup instead")
