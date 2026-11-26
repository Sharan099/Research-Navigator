"""Safe local SQLite backup, restore validation, and explicit retention maintenance."""

import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter


@contextmanager
def readonly(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
        yield connection


def inventory(connection):
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


def backup(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if destination.exists() or destination == source:
        raise ValueError("Destination must be a new file; existing files are never overwritten.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    with readonly(source) as original, closing(sqlite3.connect(destination)) as copied:
        original.backup(copied)
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup integrity check failed.")
        counts = inventory(copied)
    return {
        "operation": "backup",
        "integrity": "ok",
        "rows": counts,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
    }


def restore(source, destination):
    # Online backup also restores safely to an entirely separate SQLite file.
    result = backup(source, destination)
    with readonly(source) as original, readonly(destination) as restored:
        if inventory(original) != inventory(restored):
            raise ValueError("Restored row counts do not match the backup.")
        if restored.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Restored database contains foreign-key violations.")
    return {**result, "operation": "restore", "row_counts_match": True, "foreign_keys": "ok"}


def cleanup(database, backup_directory, apply=False, chat_days=30, backup_days=7):
    database = Path(database).resolve(strict=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=chat_days)).isoformat()
    # Compare parsed dates, rather than lexical timezone separator ordering.
    with readonly(database) as connection:
        eligible = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM conversations WHERE active_request IS NULL AND julianday(updated_at) < julianday(?)",
                (cutoff,),
            )
        ]
    directory = Path(backup_directory).resolve()
    expiry = datetime.now(timezone.utc).timestamp() - backup_days * 86400
    candidates = []
    if directory.exists():
        for path in directory.iterdir():
            # Only owned, regular backup files within the explicit directory qualify.
            if (
                path.is_file()
                and not path.is_symlink()
                and path.name.startswith("research-")
                and path.suffix == ".sqlite3"
                and path.resolve().parent == directory
                and path.resolve() != database
                and path.stat().st_mtime < expiry
            ):
                candidates.append(path)
    removed = 0
    if apply:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            # Recheck in the write transaction: a topic may have become active since preview.
            for identifier in eligible:
                if not connection.execute(
                    "SELECT 1 FROM conversations WHERE id=? AND active_request IS NULL "
                    "AND julianday(updated_at) < julianday(?)",
                    (identifier, cutoff),
                ).fetchone():
                    continue
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "message_papers" in tables:
                    connection.execute("DELETE FROM message_papers WHERE conversation_id=?", (identifier,))
                if "chat_events" in tables:
                    connection.execute(
                        "DELETE FROM chat_events WHERE run_id IN (SELECT id FROM chat_runs WHERE conversation_id=?)",
                        (identifier,),
                    )
                connection.execute("DELETE FROM chat_messages WHERE conversation_id=?", (identifier,))
                connection.execute("DELETE FROM chat_runs WHERE conversation_id=?", (identifier,))
                connection.execute("DELETE FROM conversations WHERE id=?", (identifier,))
                removed += 1
        for path in candidates:
            # Revalidate containment immediately before unlinking, and do not follow symlinks.
            if not path.is_symlink() and path.resolve().parent == directory and path.resolve() != database:
                path.unlink()
    return {
        "operation": "retention",
        "dry_run": not apply,
        "chat_days": chat_days,
        "backup_days": backup_days,
        "eligible_conversations": len(eligible),
        "eligible_backups": len(candidates),
        "deleted_conversations": removed,
        "deleted_backups": len(candidates) if apply else 0,
        "research_records_deleted": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("backup", "restore"):
        child = commands.add_parser(name)
        child.add_argument("source", type=Path)
        child.add_argument("destination", type=Path)
    child = commands.add_parser("cleanup")
    child.add_argument("database", type=Path)
    child.add_argument("--backups", type=Path, default=Path("backups"))
    child.add_argument("--apply", action="store_true", help="Delete eligible data; default is preview only")
    args = parser.parse_args()
    try:
        if args.command == "cleanup":
            result = cleanup(args.database, args.backups, args.apply)
        else:
            result = globals()[args.command](args.source, args.destination)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Operation failed ({type(error).__name__}); check paths, permissions and database integrity.\n")


if __name__ == "__main__":
    main()
