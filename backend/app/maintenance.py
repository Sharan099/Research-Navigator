"""Daily local backups and chat retention; research provenance is never pruned."""

import re
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from .chats import remove_conversation
from .models import Conversation, utc_now


def _inventory(connection):
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: connection.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0]
        for table in tables
    }


def _schema(connection):
    return dict(
        connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _validate_backup(path, expected, schema):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as copied:
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Daily backup integrity check failed")
        counts = _inventory(copied)
        if not expected or set(counts) != set(expected) or _schema(copied) != schema:
            raise RuntimeError("Daily backup schema does not match the source")
        if copied.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Daily backup contains foreign-key violations")
        return counts


def daily_backup(engine):
    now = utc_now()
    if engine.dialect.name == "sqlite" and engine.url.database not in (None, ":memory:"):
        database = Path(engine.url.database).resolve()
        if not database.exists():
            return
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            expected = _inventory(source)
            schema = _schema(source)
        # A new, empty database has no research state to protect before migrations.
        if not expected:
            return
        directory = database.parent / (
            "backups" if database.name == "research_navigator.db" else database.stem + "-backups"
        )
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"research-{now.date().isoformat()}.sqlite3"
        valid = False
        if target.exists() and not target.is_symlink():
            try:
                _validate_backup(target, expected, schema)
                valid = True
            except (sqlite3.Error, RuntimeError):
                pass
        if not valid:
            temporary = directory / (".backup-" + uuid4().hex + ".tmp")
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                    # Hold one read snapshot for counts and the online backup.
                    source.execute("BEGIN")
                    expected = _inventory(source)
                    schema = _schema(source)
                    with closing(sqlite3.connect(temporary)) as copied:
                        source.backup(copied)
                if _validate_backup(temporary, expected, schema) != expected:
                    raise RuntimeError("Daily backup snapshot row counts differ")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        for candidate in directory.iterdir():
            if (
                re.fullmatch(r"research-\d{4}-\d{2}-\d{2}\.sqlite3", candidate.name)
                and candidate.is_file()
                and not candidate.is_symlink()
                and candidate.resolve().parent == directory
                and candidate.stat().st_mtime < (now - timedelta(days=7)).timestamp()
            ):
                candidate.unlink()


def maintain(engine, session_factory, retention_days=30):
    daily_backup(engine)
    now = utc_now()
    cutoff = now - timedelta(days=retention_days)
    with session_factory() as session:
        identifiers = session.scalars(
            select(Conversation.id).where(Conversation.active_request.is_(None), Conversation.updated_at < cutoff)
        ).all()
    removed = 0
    for identifier in identifiers:
        with session_factory() as session:
            if engine.dialect.name == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            row = session.scalar(
                select(Conversation)
                .where(
                    Conversation.id == identifier,
                    Conversation.active_request.is_(None),
                    Conversation.updated_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
            if row:
                remove_conversation(session, row)
                removed += 1
    return {"deleted_conversations": removed, "research_records_deleted": 0}
