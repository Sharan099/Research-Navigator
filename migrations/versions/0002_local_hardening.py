"""Preserve uncertainty, correlate ingestion, and normalize activity/reference indexes."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    with op.batch_alter_table("publications") as batch:
        batch.alter_column("cited_by_count", existing_type=sa.Integer, nullable=True)
    with op.batch_alter_table("ingestion_runs") as batch:
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True)))
        for name in (
            "records_valid",
            "records_rejected",
            "records_duplicates",
            "records_excluded_dates",
            "records_usable",
            "duration_ms",
        ):
            batch.add_column(sa.Column(name, sa.Integer))
    with op.batch_alter_table("raw_publication_records") as batch:
        batch.add_column(sa.Column("ingestion_run_id", sa.Integer))
        batch.create_foreign_key("fk_raw_ingestion_run", "ingestion_runs", ["ingestion_run_id"], ["id"])
        batch.create_index("ix_raw_publication_records_ingestion_run_id", ["ingestion_run_id"])
    with op.batch_alter_table("chat_runs") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(80)))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False
            )
        )
    op.create_table(
        "chat_events",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("chat_runs.id"), primary_key=True),
        sa.Column("sequence", sa.Integer, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_table(
        "message_papers",
        sa.Column("message_id", sa.Integer, sa.ForeignKey("chat_messages.id"), primary_key=True),
        sa.Column("work_id", sa.String(100), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("title", sa.Text),
    )
    op.create_index("ix_message_papers_conversation_id", "message_papers", ["conversation_id"])
    op.create_index("ix_message_papers_work_id", "message_papers", ["work_id"])
    op.create_table(
        "daily_usage",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("resource", sa.String(30), primary_key=True),
        sa.Column("used", sa.Integer, nullable=False),
    )
    for table, columns in (
        ("chat_messages", ["conversation_id", "request_id"]),
        ("chat_runs", ["conversation_id"]),
        ("raw_publication_records", ["provider_work_id"]),
        ("publications", ["openalex_id", "doi"]),
    ):
        indexes = {index["name"] for index in sa.inspect(connection).get_indexes(table)}
        for column in columns:
            name = f"ix_{table}_{column}"
            if name not in indexes:
                op.create_index(name, table, [column])
    # Never infer a known zero from a missing provider count. Only valid raw evidence can recover it.
    from backend.app.repository import _valid_openalex_work

    publications = sa.table(
        "publications",
        sa.column("id", sa.Integer),
        sa.column("openalex_id", sa.String),
        sa.column("cited_by_count", sa.Integer),
    )
    raw = sa.table(
        "raw_publication_records",
        sa.column("id", sa.Integer),
        sa.column("provider_work_id", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("provider", sa.String),
    )
    for row in connection.execute(
        sa.select(publications.c.id, publications.c.openalex_id).where(publications.c.cited_by_count == 0)
    ).all():
        value = None
        records = connection.execute(
            sa.select(raw.c.payload)
            .where(raw.c.provider == "openalex", raw.c.provider_work_id == row.openalex_id)
            .order_by(raw.c.id.desc())
        ).scalars()
        for record in records:
            if _valid_openalex_work(record):
                value = record.get("cited_by_count")
                break
        connection.execute(publications.update().where(publications.c.id == row.id).values(cited_by_count=value))
    unknown_ids = set(
        connection.execute(
            sa.select(publications.c.openalex_id).where(publications.c.cited_by_count.is_(None))
        ).scalars()
    )
    # Preserve any historical zero that has actual valid source evidence.
    for work_id in list(unknown_ids):
        records = connection.execute(
            sa.select(raw.c.payload).where(raw.c.provider == "openalex", raw.c.provider_work_id == work_id)
        ).scalars()
        if any(_valid_openalex_work(record) and record.get("cited_by_count") == 0 for record in records):
            unknown_ids.discard(work_id)

    def repair(value):
        if isinstance(value, list):
            return [repair(item) for item in value]
        if isinstance(value, dict):
            result = {key: repair(item) for key, item in value.items()}
            if result.get("id") in unknown_ids and result.get("cited_by_count") == 0:
                result["cited_by_count"] = None
            return result
        return value

    for name, field in (("chat_messages", "result"), ("chat_runs", "result"), ("conversations", "state")):
        table = sa.table(
            name, sa.column("id", sa.String if name != "chat_messages" else sa.Integer), sa.column(field, sa.JSON)
        )
        for row in connection.execute(sa.select(table)).all():
            payload = getattr(row, field)
            repaired = repair(payload)
            if repaired != payload:
                connection.execute(table.update().where(table.c.id == row.id).values({field: repaired}))
    runs = sa.table(
        "chat_runs", sa.column("id", sa.String), sa.column("conversation_id", sa.String), sa.column("events", sa.JSON)
    )
    events = sa.table(
        "chat_events", sa.column("run_id", sa.String), sa.column("sequence", sa.Integer), sa.column("payload", sa.JSON)
    )
    for run in connection.execute(sa.select(runs)).all():
        for sequence, payload in enumerate(run.events or [], 1):
            payload = {**payload, "id": sequence, "request_id": run.id, "conversation_id": run.conversation_id}
            connection.execute(events.insert().values(run_id=run.id, sequence=sequence, payload=payload))
        connection.execute(runs.update().where(runs.c.id == run.id).values(events=[]))
    messages = sa.table(
        "chat_messages",
        sa.column("id", sa.Integer),
        sa.column("conversation_id", sa.String),
        sa.column("result", sa.JSON),
    )
    papers = sa.table(
        "message_papers",
        sa.column("message_id", sa.Integer),
        sa.column("conversation_id", sa.String),
        sa.column("work_id", sa.String),
        sa.column("position", sa.Integer),
        sa.column("title", sa.Text),
    )
    for message in connection.execute(sa.select(messages).where(messages.c.result.is_not(None))).all():
        result = message.result
        if not isinstance(result, dict):
            continue
        works = result.get("works", []) + result.get("sources", [])
        if result.get("work"):
            works.append(result["work"])
        seen = set()
        for position, work in enumerate(works):
            if work["id"] in seen:
                continue
            seen.add(work["id"])
            connection.execute(
                papers.insert().values(
                    message_id=message.id,
                    conversation_id=message.conversation_id,
                    work_id=work["id"],
                    position=position,
                    title=work.get("title"),
                )
            )


def downgrade():
    raise RuntimeError("Restore a verified backup to roll back a data migration")
