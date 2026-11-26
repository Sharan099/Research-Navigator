"""Versioned upgrades and the preserved legacy date backfill."""

from contextlib import nullcontext
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, delete, inspect, select, text, update

from .models import Publication, RawPublicationRecord, SearchCache
from .repository import _valid_openalex_work
from .schemas import iso_date


def migrate_publication_dates(engine: Engine | Connection) -> None:
    # Inspect before using the ORM: an existing database may lack the new column.
    with nullcontext(engine) if isinstance(engine, Connection) else engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("publications")}
        if "publication_date" not in columns:
            connection.execute(text("ALTER TABLE publications ADD COLUMN publication_date DATE"))
            connection.execute(delete(SearchCache))
        # NULL-only backfill is safe to resume after interruption. The newest valid
        # raw entry determines the value; a missing date stays missing.
        missing = connection.execute(
            select(Publication.id, Publication.openalex_id).where(Publication.publication_date.is_(None))
        ).all()
        for publication_id, work_id in missing:
            records = connection.execute(
                select(RawPublicationRecord.payload)
                .where(RawPublicationRecord.provider == "openalex", RawPublicationRecord.provider_work_id == work_id)
                .order_by(RawPublicationRecord.id.desc())
            ).scalars()
            for record in records:
                if _valid_openalex_work(record):
                    value = iso_date(record.get("publication_date"))
                    if value is not None:
                        connection.execute(
                            update(Publication).where(Publication.id == publication_id).values(publication_date=value)
                        )
                    break


def migrate_database(engine: Engine) -> None:
    """Upgrade atomically. SQLite batch table rebuilds temporarily disable FK enforcement."""
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    with engine.connect() as connection:
        sqlite = connection.dialect.name == "sqlite"
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, "head")
            if sqlite and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                raise RuntimeError("Migration would violate existing foreign-key integrity")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
