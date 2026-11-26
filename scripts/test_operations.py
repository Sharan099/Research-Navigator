"""Regression checks for backup/restore isolation and explicit retention rules."""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from operations import backup, cleanup, restore


class OperationsTests(unittest.TestCase):
    def test_backup_restore_never_overwrites_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.sqlite3"
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE evidence(id INTEGER PRIMARY KEY, title TEXT); "
                    "INSERT INTO evidence VALUES(1, 'Synthetic fixture');"
                )
            self.assertEqual(backup(source, root / "backup.sqlite3")["rows"], {"evidence": 1})
            self.assertTrue(restore(root / "backup.sqlite3", root / "restored.sqlite3")["row_counts_match"])
            with self.assertRaises(ValueError):
                backup(source, source)
            with self.assertRaises(ValueError):
                restore(source, root / "restored.sqlite3")

    def test_retention_dry_run_active_guard_and_backup_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "source.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript("""
                    CREATE TABLE conversations(id TEXT PRIMARY KEY, active_request TEXT, updated_at TEXT);
                    CREATE TABLE chat_messages(conversation_id TEXT REFERENCES conversations(id));
                    CREATE TABLE chat_runs(id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id));
                    CREATE TABLE chat_events(run_id TEXT REFERENCES chat_runs(id));
                    CREATE TABLE message_papers(conversation_id TEXT REFERENCES conversations(id));
                    CREATE TABLE publications(id INTEGER PRIMARY KEY);
                    INSERT INTO conversations VALUES('old', NULL, '2000-01-01T00:00:00+00:00');
                    INSERT INTO conversations VALUES('active', 'request', '2000-01-01T00:00:00+00:00');
                    INSERT INTO chat_messages VALUES('old');
                    INSERT INTO chat_runs VALUES('run', 'old');
                    INSERT INTO chat_events VALUES('run');
                    INSERT INTO message_papers VALUES('old');
                    INSERT INTO publications VALUES(1);
                """)
            directory = root / "backups"
            directory.mkdir()
            stale = directory / "research-old.sqlite3"
            stale.write_bytes(b"owned backup")
            os.utime(stale, (1, 1))
            unrelated = directory / "unrelated.sqlite3"
            unrelated.write_bytes(b"must stay")
            os.utime(unrelated, (1, 1))
            result = cleanup(database, directory)
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["eligible_conversations"], 1)
            self.assertTrue(stale.exists())
            result = cleanup(database, directory, apply=True)
            self.assertEqual(result["deleted_conversations"], 1)
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.exists())
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT id FROM conversations").fetchall(), [("active",)])
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
