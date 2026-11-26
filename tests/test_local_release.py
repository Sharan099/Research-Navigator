"""Local safety, bounded admission, retention and hard request deadlines."""

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.chats import ChatInput
from backend.app.config import Settings
from backend.app.database import create_database_engine, single_instance
from backend.app.main import create_app
from backend.app.maintenance import maintain
from backend.app.models import Base, ChatRun, Conversation, Publication, utc_now


def test_local_http_policy_rejects_external_origin_host_and_oversized_body(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'policy.db'}", gemini_api_key=""))
    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        assert client.get("/api/health", headers={"Host": "outside.example"}).status_code == 400
        assert client.get("/api/health", headers={"Host": "[invalid"}).status_code == 400
        assert client.post("/api/conversations", headers={"Origin": "https://outside.example"}).status_code == 403
        assert client.post("/api/search", content="x" * 64001).status_code == 413
        assert client.post("/api/search", content="message=robots").status_code == 415
        response = client.post("/api/conversations", headers={"Origin": "http://testserver"})
        assert response.status_code == 201
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_second_process_cannot_serve_the_same_sqlite_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'single.db'}"
    engine = create_database_engine(url)
    script = """
import sys
from backend.app.database import create_database_engine, single_instance
engine = create_database_engine(sys.argv[1])
try:
    with single_instance(engine):
        sys.exit(2)
except RuntimeError:
    sys.exit(0)
finally:
    engine.dispose()
"""
    try:
        with single_instance(engine):
            result = subprocess.run([sys.executable, "-c", script, url], capture_output=True, timeout=20)
            assert result.returncode == 0, result.stderr.decode()
    finally:
        engine.dispose()


def test_daily_backup_retention_is_idempotent_and_keeps_busy_chat_and_research(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as session:
        session.add(Publication(openalex_id="https://openalex.org/W1", cited_by_count=None))
        session.add_all(
            [
                Conversation(id="old", title="Old", draft="", state={}, updated_at=utc_now() - timedelta(days=31)),
                Conversation(
                    id="busy",
                    title="Busy",
                    draft="",
                    state={},
                    active_request="active",
                    updated_at=utc_now() - timedelta(days=31),
                ),
                Conversation(id="recent", title="Recent", draft="", state={}),
            ]
        )
        session.commit()
    directory = tmp_path / "retention-backups"
    directory.mkdir()
    expired = directory / "research-2000-01-01.sqlite3"
    expired.write_bytes(b"Expired owned backup fixture")
    os.utime(expired, (1, 1))
    unrelated = directory / "keep.txt"
    unrelated.write_text("Unrelated file")
    try:
        assert maintain(engine, sessions)["deleted_conversations"] == 1
        assert maintain(engine, sessions)["deleted_conversations"] == 0
        assert not expired.exists() and unrelated.exists()
        assert len(list(directory.glob("research-*.sqlite3"))) == 1
        with sessions() as session:
            assert set(session.scalars(select(Conversation.id))) == {"busy", "recent"}
            assert session.scalar(select(Publication)).cited_by_count is None
    finally:
        engine.dispose()


def test_deadline_returns_terminal_error_and_releases_conversation(tmp_path):
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'deadline.db'}", gemini_api_key="", request_timeout_seconds=1)
    )

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)

    app.state.search_service.search_owned = slow
    with TestClient(app) as client:
        conversation_id = client.post("/api/conversations").json()["id"]
        command = {"request_id": str(uuid4()), "message": "Find synthetic test records"}
        response = client.post(f"/api/conversations/{conversation_id}/messages", json=command)
        assert "event: error" in response.text and "time limit" in response.text
        saved = client.get(f"/api/conversations/{conversation_id}").json()
        assert not saved["busy"]
        assert saved["runs"][0]["status"] == "interrupted"
        assert saved["messages"][-1]["error"]


def test_chat_admission_is_atomic_across_worker_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException

    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'admission.db'}", gemini_api_key="", max_active_requests=1)
    )
    with TestClient(app) as client:
        topics = [client.post("/api/conversations").json()["id"] for _ in range(4)]

        def reserve(conversation_id):
            try:
                return app.state.chats.reserve(
                    conversation_id, ChatInput(request_id=str(uuid4()), message="Find robots")
                )
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=4) as workers:
            statuses = list(workers.map(reserve, topics))
        assert statuses.count(True) == 1 and statuses.count(429) == 3
        with app.state.session_factory() as session:
            assert len(session.scalars(select(ChatRun).where(ChatRun.status == "running")).all()) == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires disposable PostgreSQL test service")
def test_postgresql_versioned_migration_in_isolated_schema():
    from sqlalchemy import create_engine, inspect, text

    from backend.app.migrations import migrate_database

    url = os.environ["TEST_DATABASE_URL"]
    assert url.startswith("postgresql")
    schema = "migration_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        migrate_database(engine)
        migrate_database(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"
        assert {"chat_events", "message_papers", "daily_usage"} <= set(inspect(engine).get_table_names())
        with sessionmaker(engine)() as session:
            session.add(Publication(openalex_id="https://openalex.org/W1", cited_by_count=None))
            session.commit()
            assert session.scalar(select(Publication)).cited_by_count is None
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
