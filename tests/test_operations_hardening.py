"""Offline operational contracts: budgets, retries, configuration, and readiness."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.models import Base, DailyUsage
from backend.app.providers import ProviderBudgetExceeded, ProviderRuntime, request_json, retry_after


def settings(**overrides):
    return Settings(database_url="sqlite://", gemini_api_key="", openalex_api_key="", **overrides)


def runtime_with_response(handler, **overrides):
    runtime = ProviderRuntime(settings(**overrides))
    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return runtime


def test_settings_reads_environment_at_construction_and_hides_credentials(monkeypatch):
    monkeypatch.setenv("CACHE_TTL_HOURS", "7")
    first = Settings(
        gemini_api_key="synthetic-gemini-secret",
        openalex_api_key="synthetic-openalex-secret",
        database_url="postgresql+psycopg://user:synthetic-db-secret@localhost/example",
    )
    monkeypatch.setenv("CACHE_TTL_HOURS", "9")
    assert first.cache_ttl_hours == 7 and Settings().cache_ttl_hours == 9
    assert "synthetic-" not in repr(first)
    monkeypatch.setenv("CACHE_TTL_HOURS", "invalid")
    with pytest.raises(ValueError, match="CACHE_TTL_HOURS must be an integer"):
        Settings()


@pytest.mark.parametrize(
    "override",
    [
        {"database_url": "mysql://unsupported"},
        {"gemini_model": "../unsafe"},
        {"cache_ttl_hours": -1},
        {"ai_turns_per_day": -1},
        {"gemini_requests_per_day": -1},
        {"openalex_requests_per_day": -1},
        {"provider_concurrency": 0},
        {"max_active_requests": 0},
        {"provider_attempts": 0},
        {"provider_attempts": 4},
        {"provider_timeout_seconds": 0},
        {"request_timeout_seconds": 0},
        {"max_response_bytes": 0},
        {"chat_retention_days": 0},
    ],
)
def test_invalid_runtime_settings_fail_before_serving(override):
    with pytest.raises(ValueError):
        Settings(gemini_api_key="", openalex_api_key="", **override)


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_status_retries_once_and_recovers(status, monkeypatch):
    import backend.app.providers as providers

    attempts, delays = [], []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(providers.asyncio, "sleep", sleep)

    def respond(request):
        attempts.append(request)
        return (
            httpx.Response(status, headers={"Retry-After": "1"})
            if len(attempts) == 1
            else httpx.Response(200, json={"ok": True})
        )

    runtime = runtime_with_response(respond)

    async def run():
        try:
            with runtime.turn():
                return await request_json(runtime.settings, "openalex", "GET", "https://example.test/works")
        finally:
            await runtime.close()

    assert asyncio.run(run()) == {"ok": True}
    assert len(attempts) == 2 and delays == [1.0]


def test_long_retry_after_does_not_retry_early():
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    runtime = runtime_with_response(respond)

    async def run():
        try:
            with runtime.turn():
                await request_json(runtime.settings, "gemini", "POST", "https://example.test/generate")
        finally:
            await runtime.close()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert len(attempts) == 1


@pytest.mark.parametrize(
    "status,content,error",
    [
        (401, b'{"error":"secret input not authorized"}', httpx.HTTPStatusError),
        (200, b"not-json", ValueError),
        (200, b"{" + b" " * 100 + b"}", ValueError),
    ],
)
def test_rejected_payloads_and_auth_errors_are_not_retried(status, content, error):
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(status, content=content)

    runtime = runtime_with_response(respond, max_response_bytes=32)

    async def run():
        try:
            with runtime.turn():
                await request_json(runtime.settings, "gemini", "POST", "https://example.test/generate")
        finally:
            await runtime.close()

    with pytest.raises(error):
        asyncio.run(run())
    assert len(attempts) == 1


def test_ambiguous_gemini_timeout_is_not_retried():
    attempts = []

    def respond(request):
        attempts.append(request)
        raise httpx.ReadTimeout("Synthetic ambiguous timeout", request=request)

    runtime = runtime_with_response(respond)

    async def run():
        try:
            with runtime.turn():
                await request_json(runtime.settings, "gemini", "POST", "https://example.test/generate")
        finally:
            await runtime.close()

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(run())
    assert len(attempts) == 1


def test_get_transport_error_retries_with_bounded_attempts(monkeypatch):
    import backend.app.providers as providers

    attempts = []

    async def sleep(delay):
        pass

    monkeypatch.setattr(providers.asyncio, "sleep", sleep)

    def respond(request):
        attempts.append(request)
        raise httpx.ConnectError("Synthetic offline source", request=request)

    runtime = runtime_with_response(respond)

    async def run():
        try:
            with runtime.turn():
                await request_json(runtime.settings, "openalex", "GET", "https://example.test/works")
        finally:
            await runtime.close()

    with pytest.raises(httpx.ConnectError):
        asyncio.run(run())
    assert len(attempts) == 2


def test_daily_quota_is_persistent_and_ai_turn_charged_once(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'usage.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    config = settings(ai_turns_per_day=1, gemini_requests_per_day=4)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(200, json={"ok": True})

    async def run():
        runtime = ProviderRuntime(config, sessions)
        runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with runtime.turn():
                await request_json(config, "gemini", "POST", "https://example.test/plan")
                await request_json(config, "gemini", "POST", "https://example.test/answer")
            with runtime.turn(), pytest.raises(ProviderBudgetExceeded):
                await request_json(config, "gemini", "POST", "https://example.test/another-turn")
        finally:
            await runtime.close()
        restarted = ProviderRuntime(config, sessions)
        restarted.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with restarted.turn(), pytest.raises(ProviderBudgetExceeded):
                await request_json(config, "gemini", "POST", "https://example.test/restarted")
        finally:
            await restarted.close()

    asyncio.run(run())
    with sessions() as session:
        usage = {row.resource: row.used for row in session.scalars(select(DailyUsage))}
        assert usage == {"gemini": 2, "ai_turns": 1}
    assert len(attempts) == 2
    engine.dispose()


def test_zero_budget_and_retry_attempts_count_before_external_requests(tmp_path, monkeypatch):
    import backend.app.providers as providers

    async def sleep(delay):
        pass

    monkeypatch.setattr(providers.asyncio, "sleep", sleep)
    engine = create_engine(f"sqlite:///{tmp_path / 'retry-usage.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    config = settings(openalex_requests_per_day=1)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(503)

    runtime = ProviderRuntime(config, sessions)
    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    async def run():
        try:
            with runtime.turn(), pytest.raises(ProviderBudgetExceeded):
                await request_json(config, "openalex", "GET", "https://example.test/works")
        finally:
            await runtime.close()

    asyncio.run(run())
    assert len(attempts) == 1
    assert runtime.usage()["used"]["openalex"] == 1
    engine.dispose()


def test_retry_after_parses_http_dates_and_invalid_values():
    assert retry_after("nonsense") == retry_after(None) == retry_after("-10") == 0
    delay = retry_after(format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60)))
    assert 58 <= delay <= 60


def test_provider_metrics_export_usage_without_research_text_or_keys():
    secret = "synthetic-private-chat-and-api-key"
    runtime = runtime_with_response(
        lambda request: httpx.Response(
            200, json={"private": secret, "usageMetadata": {"promptTokenCount": 13, "candidatesTokenCount": 5}}
        )
    )

    async def run():
        try:
            with runtime.turn():
                await request_json(
                    runtime.settings,
                    "gemini",
                    "POST",
                    "https://example.test/generate",
                    headers={"x-goog-api-key": secret},
                    json={"question": secret},
                )
        finally:
            await runtime.close()

    asyncio.run(run())
    metrics = runtime.metrics.render()
    assert secret not in metrics and "question" not in metrics and "example.test" not in metrics
    assert 'research_provider_tokens_total{kind="promptTokenCount"} 13.0' in metrics
    assert 'research_provider_tokens_total{kind="candidatesTokenCount"} 5.0' in metrics
    assert 'research_provider_seconds_count{provider="gemini"} 1' in metrics


def test_zero_budget_stops_before_external_request(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'zero.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    runtime = ProviderRuntime(settings(openalex_requests_per_day=0), sessions)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(200, json={})

    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    async def run():
        try:
            with runtime.turn(), pytest.raises(ProviderBudgetExceeded):
                await request_json(runtime.settings, "openalex", "GET", "https://example.test/works")
        finally:
            await runtime.close()

    asyncio.run(run())
    assert attempts == [] and runtime.usage()["used"]["openalex"] == 0
    engine.dispose()


def test_cached_research_bypasses_exhausted_provider_quota(tmp_path):
    from copy import deepcopy

    from test_data_pipeline import SAMPLE_WORK

    from backend.app.interpreter import SearchInterpreter
    from backend.app.openalex import OpenAlexClient
    from backend.app.service import ResearchSearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'cached-budget.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    config = settings(openalex_requests_per_day=1, ai_turns_per_day=0, gemini_requests_per_day=0)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(200, json={"results": [deepcopy(SAMPLE_WORK)], "meta": {"count": 1}})

    runtime = ProviderRuntime(config, sessions)
    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service = ResearchSearchService(config, SearchInterpreter(config), OpenAlexClient(config))

    async def run():
        try:
            with runtime.turn():
                fresh = await service.search_owned("tactile sensors", sessions)
            with runtime.turn():
                cached = await service.search_owned("tactile sensors", sessions)
            with runtime.turn():
                fallback = await service.search_owned("robot sensors", sessions)
            return fresh, cached, fallback
        finally:
            await runtime.close()

    fresh, cached, fallback = asyncio.run(run())
    assert fresh.retrieval_source == "openalex_fresh"
    assert cached.retrieval_source == "local_cache"
    assert fallback.retrieval_source == "local_fallback"
    assert any("local daily OpenAlex budget is exhausted" in warning for warning in fallback.warnings)
    assert len(attempts) == runtime.usage()["used"]["openalex"] == 1
    engine.dispose()


def test_provider_total_deadline_becomes_retry_safe_http_timeout():
    attempts = []

    async def respond(request):
        attempts.append(request)
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    runtime = runtime_with_response(respond, request_timeout_seconds=1)

    async def run():
        try:
            with runtime.turn():
                await request_json(runtime.settings, "gemini", "POST", "https://example.test/generate")
        finally:
            await runtime.close()

    with pytest.raises(httpx.TimeoutException):
        asyncio.run(run())
    assert len(attempts) == 1


def test_provider_concurrency_limit_bounds_simultaneous_calls():
    active = maximum = 0

    async def respond(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"ok": True})

    runtime = runtime_with_response(respond, provider_concurrency=1)

    async def run():
        try:
            with runtime.turn():
                return await asyncio.gather(
                    *[request_json(runtime.settings, "openalex", "GET", "https://example.test/works") for _ in range(3)]
                )
        finally:
            await runtime.close()

    assert asyncio.run(run()) == [{"ok": True}] * 3
    assert maximum == 1 and active == 0


def legacy_database(tmp_path):
    """Create the last released schema, with synthetic evidence and saved activity."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table

    from backend.app.database import create_database_engine

    engine = create_database_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
    metadata = MetaData()
    tables = {
        name: Table(name, metadata, autoload_with=engine)
        for name in ("publications", "raw_publication_records", "conversations", "chat_messages", "chat_runs")
    }
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        for number, count in [(1, None), (2, 0), (3, 8)]:
            work_id = f"https://openalex.org/W{number}"
            connection.execute(
                tables["publications"]
                .insert()
                .values(
                    id=number,
                    openalex_id=work_id,
                    title=f"Synthetic migration fixture {number}",
                    cited_by_count=0 if count is None else count,
                    last_fetched_at=now,
                )
            )
            connection.execute(
                tables["raw_publication_records"]
                .insert()
                .values(
                    provider="openalex",
                    provider_work_id=work_id,
                    payload={"id": work_id, "cited_by_count": count},
                    fetched_at=now,
                )
            )
        connection.execute(
            tables["conversations"]
            .insert()
            .values(
                id="legacy-conversation",
                title="Synthetic saved topic",
                draft="saved draft",
                state={
                    "results": [{"id": "https://openalex.org/W1", "title": "Synthetic fixture", "cited_by_count": 0}]
                },
                updated_at=now,
            )
        )
        result = {
            "status": "results",
            "works": [
                {"id": "https://openalex.org/W1", "title": "Synthetic unknown fixture", "cited_by_count": 0},
                {"id": "https://openalex.org/W2", "title": "Synthetic zero fixture", "cited_by_count": 0},
            ],
        }
        connection.execute(
            tables["chat_messages"]
            .insert()
            .values(
                id=1,
                conversation_id="legacy-conversation",
                request_id="legacy-run",
                role="assistant",
                text="",
                result=result,
            )
        )
        connection.execute(
            tables["chat_runs"]
            .insert()
            .values(
                id="legacy-run",
                conversation_id="legacy-conversation",
                payload={},
                status="completed",
                events=[{"id": 1, "stage": "request", "status": "completed"}],
                result=result,
            )
        )
    return engine


def test_migration_preserves_records_normalizes_activity_and_unknown_counts(tmp_path):
    from sqlalchemy import inspect, text

    from backend.app.migrations import migrate_database
    from backend.app.models import (
        ChatEvent,
        ChatMessage,
        ChatRun,
        Conversation,
        MessagePaper,
        Publication,
        RawPublicationRecord,
    )

    engine = legacy_database(tmp_path)
    try:
        migrate_database(engine)
        migrate_database(engine)
        with sessionmaker(engine)() as session:
            publications = session.scalars(select(Publication).order_by(Publication.id)).all()
            assert [row.cited_by_count for row in publications] == [None, 0, 8]
            raw = session.scalars(select(RawPublicationRecord)).all()
            assert [row.payload["cited_by_count"] for row in raw] == [None, 0, 8]
            assert len(raw) == 3
            assert session.get(Conversation, "legacy-conversation").draft == "saved draft"
            assert len(session.scalars(select(ChatEvent)).all()) == 1
            assert len(session.scalars(select(MessagePaper)).all()) == 2
            assert session.get(ChatRun, "legacy-run").events == []
            snapshot = session.get(ChatMessage, 1).result["works"]
            assert [work["cited_by_count"] for work in snapshot] == [None, 0]
            assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        columns = {column["name"]: column for column in inspect(engine).get_columns("publications")}
        assert columns["cited_by_count"]["nullable"]
    finally:
        engine.dispose()


def test_migration_failure_rolls_back_schema_data_and_version(tmp_path):
    from sqlalchemy import event, inspect, text

    from backend.app.migrations import migrate_database

    engine = legacy_database(tmp_path)
    triggered = []

    def fail_after_rebuild(connection, cursor, statement, parameters, context, executemany):
        if "_alembic_tmp_publications" in statement and "RENAME TO" in statement and not triggered:
            triggered.append(True)
            raise RuntimeError("Synthetic migration failure")

    event.listen(engine, "after_cursor_execute", fail_after_rebuild)
    try:
        with pytest.raises(RuntimeError, match="Synthetic migration failure"):
            migrate_database(engine)
        event.remove(engine, "after_cursor_execute", fail_after_rebuild)
        assert triggered
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0001"
            assert connection.execute(text("SELECT COUNT(*) FROM publications")).scalar_one() == 3
            assert connection.execute(text("SELECT COUNT(*) FROM raw_publication_records")).scalar_one() == 3
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert "completed_at" not in {column["name"] for column in inspect(engine).get_columns("ingestion_runs")}
        assert "_alembic_tmp_publications" not in inspect(engine).get_table_names()
        migrate_database(engine)
    finally:
        if event.contains(engine, "after_cursor_execute", fail_after_rebuild):
            event.remove(engine, "after_cursor_execute", fail_after_rebuild)
        engine.dispose()


def test_sqlite_runtime_connections_enforce_foreign_keys(tmp_path):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from backend.app.database import create_database_engine
    from backend.app.models import RawPublicationRecord

    engine = create_database_engine(f"sqlite:///{tmp_path / 'foreign-keys.db'}")
    Base.metadata.create_all(engine)
    try:
        with sessionmaker(engine)() as session:
            assert session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
            session.add(
                RawPublicationRecord(
                    provider="openalex", provider_work_id="synthetic", payload={}, ingestion_run_id=999
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
            assert session.scalars(select(RawPublicationRecord)).all() == []
    finally:
        engine.dispose()


def test_readiness_reports_database_failure_without_leaking_details(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'readiness.db'}", gemini_api_key="", openalex_api_key="")
    )
    with TestClient(app) as client:
        assert client.get("/api/ready").status_code == 200
        engine = app.state.session_factory.kw["bind"]

        def unavailable(*args, **kwargs):
            raise RuntimeError("synthetic-database-password-private-host")

        with monkeypatch.context() as patch:
            patch.setattr(engine, "connect", unavailable)
            response = client.get("/api/ready")
            assert response.status_code == 503
            assert "synthetic-database-password" not in response.text
            assert "private-host" not in response.text
            assert client.get("/api/health").status_code == 200
        assert client.get("/api/ready").status_code == 200


def test_application_metrics_exclude_chat_content_and_credentials(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    secret = "synthetic-private-draft-and-key"
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'metrics.db'}", gemini_api_key=secret, openalex_api_key=secret)
    )
    with TestClient(app) as client:
        conversation = client.post("/api/conversations").json()
        assert (
            client.patch(
                f"/api/conversations/{conversation['id']}", json={"draft": secret, "title": secret}
            ).status_code
            == 200
        )
        assert client.get(f"/api/conversations/{conversation['id']}").status_code == 200
        response = client.get("/metrics")
        assert response.status_code == 200
        assert secret not in response.text and conversation["id"] not in response.text
        assert "research_" in response.text


def test_concurrent_runtime_instances_cannot_overspend_shared_quota(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic-budget.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    config = settings(openalex_requests_per_day=1)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(200, json={"ok": True})

    runtimes = [ProviderRuntime(config, sessions), ProviderRuntime(config, sessions)]
    for runtime in runtimes:
        runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    async def run(runtime):
        with runtime.turn():
            return await request_json(config, "openalex", "GET", "https://example.test/works")

    async def scenario():
        try:
            return await asyncio.gather(*(run(runtime) for runtime in runtimes), return_exceptions=True)
        finally:
            for runtime in runtimes:
                await runtime.close()

    outcomes = asyncio.run(scenario())
    assert len(attempts) == 1
    assert sum(isinstance(result, ProviderBudgetExceeded) for result in outcomes) == 1
    assert sum(result == {"ok": True} for result in outcomes) == 1
    with sessions() as session:
        assert session.scalar(select(DailyUsage.used).where(DailyUsage.resource == "openalex")) == 1
    engine.dispose()


@pytest.mark.parametrize("failing_statement", ["INSERT INTO chat_events", "INSERT INTO message_papers"])
def test_chat_persistence_failure_finishes_safely_and_keeps_committed_research(tmp_path, failing_statement):
    import sqlite3
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from test_chats import events
    from test_topics import Papers, paper

    from backend.app.main import create_app
    from backend.app.models import ChatMessage, ChatRun, Conversation, Publication

    provider = Papers([paper()])
    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'failure.db'}", gemini_api_key="", openalex_api_key=""), provider
    )
    with TestClient(app) as client:
        conversation_id = client.post("/api/conversations").json()["id"]
        engine = app.state.engine
        failed = []

        def fail_once(connection, cursor, statement, parameters, context, executemany):
            if failing_statement in statement and not failed:
                failed.append(True)
                raise OperationalError(
                    statement, parameters, sqlite3.OperationalError("synthetic-private-database-password")
                )

        event.listen(engine, "before_cursor_execute", fail_once)
        request_id = str(uuid4())
        try:
            response = client.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"request_id": request_id, "message": "Find tactile sensors"},
            )
        finally:
            event.remove(engine, "before_cursor_execute", fail_once)
        assert failed and "synthetic-private-database-password" not in response.text
        assert events(response)[-1][0] == "error"
        with app.state.session_factory() as session:
            run = session.get(ChatRun, request_id)
            assert run.status == "failed"
            assert session.get(Conversation, conversation_id).active_request is None
            assistant = session.scalar(
                select(ChatMessage).where(ChatMessage.request_id == request_id, ChatMessage.role == "assistant")
            )
            assert assistant.text and assistant.result is None
            publications = session.scalars(select(Publication)).all()
            assert len(publications) == (1 if failing_statement == "INSERT INTO message_papers" else 0)


def test_chat_pagination_is_bounded_ordered_and_isolated(tmp_path):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.models import ChatEvent, ChatMessage, ChatRun

    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'pages.db'}", gemini_api_key="", openalex_api_key="")
    )
    with TestClient(app) as client:
        conversation_id = client.post("/api/conversations").json()["id"]
        other_id = client.post("/api/conversations").json()["id"]
        run_id = str(uuid4())
        with app.state.session_factory() as session:
            rows = [
                ChatMessage(
                    conversation_id=conversation_id,
                    request_id=run_id,
                    role="user",
                    text=f"Synthetic history fixture {index}",
                )
                for index in range(65)
            ]
            session.add_all(rows)
            session.add(ChatRun(id=run_id, conversation_id=conversation_id, payload={}, status="completed"))
            session.flush()
            ids = [row.id for row in rows]
            session.add_all(
                [ChatEvent(run_id=run_id, sequence=index, payload={"id": index}) for index in range(1, 151)]
            )
            session.commit()
        recent = client.get(f"/api/conversations/{conversation_id}").json()
        assert [row["id"] for row in recent["messages"]] == ids[-30:]
        first = client.get(
            f"/api/conversations/{conversation_id}/messages", params={"before_id": recent["messages_before"]}
        ).json()
        second = client.get(
            f"/api/conversations/{conversation_id}/messages", params={"before_id": first["next_before"]}
        ).json()
        restored = second["messages"] + first["messages"] + recent["messages"]
        assert [row["id"] for row in restored] == ids and second["next_before"] is None
        activity = client.get(f"/api/conversations/{conversation_id}/runs/{run_id}/events").json()
        assert [event["id"] for event in activity["events"]] == list(range(1, 101))
        assert activity["has_more"]
        tail = client.get(
            f"/api/conversations/{conversation_id}/runs/{run_id}/events", params={"after_id": activity["next_after"]}
        ).json()
        assert [event["id"] for event in tail["events"]] == list(range(101, 151))
        assert not tail["has_more"]
        assert client.get(f"/api/conversations/{other_id}/runs/{run_id}/events").status_code == 404
        assert client.get(f"/api/conversations/{conversation_id}/messages?limit=101").status_code == 422


def test_stale_claim_recovers_without_clearing_fresh_request(tmp_path):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from backend.app.chats import ChatInput
    from backend.app.main import create_app
    from backend.app.models import ChatRun, Conversation, utc_now

    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'claims.db'}", gemini_api_key="", openalex_api_key="")
    )
    with TestClient(app) as client:
        stale_id = client.post("/api/conversations").json()["id"]
        fresh_id = client.post("/api/conversations").json()["id"]
        stale_command = ChatInput(request_id=str(uuid4()), message="Synthetic stale request")
        fresh_command = ChatInput(request_id=str(uuid4()), message="Synthetic active request")
        app.state.chats.reserve(stale_id, stale_command)
        app.state.chats.reserve(fresh_id, fresh_command)
        with app.state.session_factory() as session:
            session.get(ChatRun, stale_command.request_id).heartbeat_at = utc_now() - timedelta(seconds=121)
            session.commit()
        replacement = ChatInput(request_id=str(uuid4()), message="Synthetic replacement request")
        assert app.state.chats.reserve(stale_id, replacement)
        with app.state.session_factory() as session:
            assert session.get(ChatRun, stale_command.request_id).status == "interrupted"
            assert session.get(Conversation, stale_id).active_request == replacement.request_id
            assert session.get(ChatRun, fresh_command.request_id).status == "running"
            assert session.get(Conversation, fresh_id).active_request == fresh_command.request_id


def test_exhausted_gemini_budget_explains_rules_and_evidence_fallback(tmp_path):
    from test_topics import Papers, paper

    from backend.app.interpreter import SearchInterpreter
    from backend.app.schemas import SearchRequest
    from backend.app.service import ResearchSearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'ai-fallback.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    config = Settings(
        database_url="sqlite://", gemini_api_key="synthetic-test-key", openalex_api_key="", gemini_requests_per_day=0
    )
    runtime = ProviderRuntime(config, sessions)
    attempts = []

    def respond(request):
        attempts.append(request)
        return httpx.Response(200, json={})

    runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    service = ResearchSearchService(config, SearchInterpreter(config), Papers([paper()]))

    async def run():
        try:
            with runtime.turn():
                results = await service.search_owned("tactile sensors", sessions)
            with runtime.turn():
                answer = await service.search_owned(
                    "Explain these papers",
                    sessions,
                    request=SearchRequest(
                        message="Explain these papers", action="answer", work_ids=[work.id for work in results.works]
                    ),
                )
            return results, answer
        finally:
            await runtime.close()

    results, answer = asyncio.run(run())
    assert results.parser_mode == "rules" and answer.answer_mode == "evidence_only"
    assert any("local daily Gemini budget is exhausted" in warning for warning in results.warnings)
    assert any("local daily Gemini budget is exhausted" in warning for warning in answer.warnings)
    assert answer.sources and attempts == []
    engine.dispose()
