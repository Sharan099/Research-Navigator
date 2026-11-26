"""Connection integrity and an operating-system/database enforced local instance."""

import os
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

_guards = {}
_guard_lock = Lock()


def create_database_engine(url):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

    return engine


@contextmanager
def single_instance(engine):
    """A second process cannot serve this database. Nested in-process tests may reuse it."""
    if os.getenv("WEB_CONCURRENCY", "1") != "1":
        raise RuntimeError("Research Navigator supports one local application worker")
    key = engine.url.render_as_string(hide_password=True)
    with _guard_lock:
        if key in _guards:
            _guards[key][0] += 1
        elif engine.dialect.name == "postgresql":
            connection = engine.connect()
            if not connection.scalar(text("SELECT pg_try_advisory_lock(727364902)")):
                connection.close()
                raise RuntimeError("Another Research Navigator process is using this database")
            connection.commit()
            _guards[key] = [1, connection]
        else:
            database = engine.url.database
            lock_path = (
                Path(database + ".lock") if database and database != ":memory:" else Path(".runtime/memory.lock")
            )
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+b")
            try:
                if lock_path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                raise RuntimeError("Another Research Navigator process is using this database") from exc
            _guards[key] = [1, handle]
    try:
        yield
    finally:
        with _guard_lock:
            _guards[key][0] -= 1
            if not _guards[key][0]:
                resource = _guards.pop(key)[1]
                resource.close()
