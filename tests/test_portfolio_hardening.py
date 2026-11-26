"""Regressions for measured audit gaps. All publication records here are synthetic."""

import asyncio
import os
import sqlite3
from contextlib import closing
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import create_database_engine
from backend.app.http_policy import LocalPolicy
from backend.app.main import create_app
from backend.app.maintenance import daily_backup
from backend.app.models import Base, Publication, RawPublicationRecord, SearchCache, utc_now
from backend.app.quality import is_present, present_condition
from backend.app.repository import local_search, matches_dates, save_openalex_work
from backend.app.schemas import SearchIntent
from backend.app.telemetry import Metrics
from scripts.evaluate import database_metrics
from scripts.synthetic_provider import SyntheticProvider


@pytest.fixture
def store(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'isolated.db'}")
    Base.metadata.create_all(engine)
    try:
        yield engine, sessionmaker(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "day,year,expected",
    [
        (date(2022, 1, 1), 2022, True),
        (date(2024, 12, 31), 2024, True),
        (date(1900, 1, 1), 1900, False),
        (None, 2023, True),
        (None, None, False),
    ],
)
def test_year_bounds_agree_for_memory_and_sql(store, day, year, expected):
    _, sessions = store
    intent = SearchIntent(query="synthetic robot", year_from=2022, year_to=2024)
    work = Publication(
        openalex_id="https://openalex.org/W1", title="synthetic robot", publication_date=day, publication_year=year
    )
    assert matches_dates(work, intent) is expected
    with sessions() as session:
        session.add(work)
        session.commit()
        assert bool(local_search(session, intent)) is expected


def test_day_bounds_do_not_invent_missing_dates(store):
    _, sessions = store
    intent = SearchIntent(query="synthetic", date_from=date(2024, 1, 1), date_to=date(2024, 1, 31))
    work = Publication(openalex_id="https://openalex.org/W1", title="synthetic", publication_year=2024)
    assert not matches_dates(work, intent)
    with sessions() as session:
        session.add(work)
        session.commit()
        assert local_search(session, intent) == []


def test_conflicting_dates_preserve_raw_without_cleaned_mutation(store):
    _, sessions = store
    with sessions() as session:
        record = {"id": "https://openalex.org/W1", "publication_year": 1900, "publication_date": "2026-01-01"}
        assert save_openalex_work(session, record) is None
        session.commit()
        assert session.scalar(select(func.count()).select_from(Publication)) == 0
        assert session.scalar(select(RawPublicationRecord)).payload == record
        # A later invalid observation must also leave an existing good snapshot intact.
        good = {**record, "publication_year": 2026}
        assert save_openalex_work(session, good) == record["id"]
        assert save_openalex_work(session, record) is None
        session.commit()
        assert session.scalar(select(Publication)).publication_year == 2026
        assert session.scalar(select(func.count()).select_from(RawPublicationRecord)) == 3


@pytest.mark.parametrize("sort", ["relevance", "newest", "most_cited"])
def test_rank_before_limit_and_stable_ties(store, sort):
    _, sessions = store
    with sessions() as session:
        session.add_all(
            [
                Publication(
                    openalex_id=f"https://openalex.org/W{i}",
                    title="synthetic",
                    cited_by_count=0,
                    publication_date=date(2020, 1, 1),
                )
                for i in range(1, 301)
            ]
        )
        session.add(
            Publication(
                openalex_id="https://openalex.org/W999",
                title="synthetic robot learning",
                cited_by_count=100,
                publication_date=date(2026, 1, 1),
            )
        )
        session.commit()
        intent = SearchIntent(query="synthetic robot learning", max_results=2, sort=sort)
        first = [row.openalex_id for row in local_search(session, intent)]
        assert first[0].endswith("W999")
        assert first == [row.openalex_id for row in local_search(session, intent)]


@pytest.mark.parametrize("value,expected", [(None, False), ("", False), (" \t\n\u2003\xa0", False), (" x ", True)])
def test_completeness_sql_and_python_agree(store, value, expected):
    _, sessions = store
    assert is_present(value) is expected
    assert is_present(0)
    with sessions() as session:
        session.add(Publication(openalex_id="https://openalex.org/W1", title=value))
        session.commit()
        present = session.scalar(
            select(func.count()).select_from(Publication).where(present_condition(Publication.title))
        )
        assert present == int(expected)


@pytest.mark.parametrize("broken", [b"", b"not sqlite"])
def test_daily_backup_replaces_invalid_existing_file(store, tmp_path, broken):
    engine, sessions = store
    with sessions() as session:
        session.add(Publication(openalex_id="https://openalex.org/W1"))
        session.commit()
    directory = tmp_path / "isolated-backups"
    directory.mkdir()
    target = directory / f"research-{utc_now().date().isoformat()}.sqlite3"
    target.write_bytes(broken)
    daily_backup(engine)
    with closing(sqlite3.connect(target)) as copied:
        assert copied.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1
    original = target.read_bytes()
    daily_backup(engine)
    assert target.read_bytes() == original
    assert list(directory.glob("*.tmp")) == []
    # A readable database with the same table names but changed columns is not our snapshot schema.
    with closing(sqlite3.connect(target)) as copied:
        copied.execute("ALTER TABLE publications ADD COLUMN synthetic_damage TEXT")
        copied.commit()
    daily_backup(engine)
    with closing(sqlite3.connect(target)) as copied:
        assert "synthetic_damage" not in {row[1] for row in copied.execute("PRAGMA table_info(publications)")}


def test_backup_failure_preserves_old_files_and_cleans_temporary(store, tmp_path, monkeypatch):
    import backend.app.maintenance as maintenance

    engine, _ = store
    directory = tmp_path / "isolated-backups"
    directory.mkdir()
    old = directory / "research-2000-01-01.sqlite3"
    old.write_bytes(b"older evidence")
    os.utime(old, (1, 1))

    def fail(*args):
        raise RuntimeError("Synthetic validation failure")

    monkeypatch.setattr(maintenance, "_validate_backup", fail)
    with pytest.raises(RuntimeError, match="Synthetic"):
        daily_backup(engine)
    assert old.read_bytes() == b"older evidence"
    assert not list(directory.glob("*.tmp"))


def test_backup_count_mismatch_blocks_publication_and_retention(store, tmp_path, monkeypatch):
    import backend.app.maintenance as maintenance

    engine, _ = store
    directory = tmp_path / "isolated-backups"
    directory.mkdir()
    old = directory / "research-2000-01-01.sqlite3"
    old.write_bytes(b"older evidence")
    os.utime(old, (1, 1))
    monkeypatch.setattr(maintenance, "_validate_backup", lambda *args: {"wrong": 0})
    with pytest.raises(RuntimeError, match="row counts"):
        daily_backup(engine)
    assert old.exists()
    assert not list(directory.glob("*.tmp"))


def test_request_logging_enabled_and_content_free():
    import io
    import json
    import logging

    from backend.app.telemetry import logger

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        policy = LocalPolicy(None, Metrics())

        async def send(message):
            pass

        asyncio.run(
            policy(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/private-query-text",
                    "headers": [(b"host", b"invalid.example")],
                },
                None,
                send,
            )
        )
        entry = json.loads(stream.getvalue())
        assert logger.isEnabledFor(logging.INFO)
        assert entry["status"] == 400
        assert "private-query-text" not in stream.getvalue()
    finally:
        logger.removeHandler(handler)


def test_early_rejection_counted_once_with_correlation():
    metrics = Metrics()

    async def downstream(*args):
        raise AssertionError("Rejected request reached application")

    policy = LocalPolicy(downstream, metrics, max_active_requests=1)
    policy.active = 1
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(
        policy(
            {"type": "http", "method": "POST", "path": "/api/search", "headers": [(b"host", b"127.0.0.1")]}, None, send
        )
    )
    assert messages[0]["status"] == 429
    assert dict(messages[0]["headers"])[b"x-request-id"]
    assert 'status="429"} 1.0' in metrics.render()
    assert sum(metrics.counters.values()) == 1
    assert policy.active == 1


def test_api_quality_and_evaluator_agree_and_cache_counts_once(tmp_path):
    database = tmp_path / "app.db"
    app = create_app(
        Settings(database_url=f"sqlite:///{database}", gemini_api_key="", groq_api_key=""),
        openalex_client=SyntheticProvider(),
    )
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add(Publication(openalex_id="https://openalex.org/W1", title=" \t", abstract="\u2003"))
            session.commit()
        quality = client.get("/api/quality").json()
        assert quality["field_completeness"]["title"]["present"] == 0
        assert database_metrics(database)["missing_title_rate"] == 1
        payload = {
            "message": "synthetic robot",
            "action": "search",
            "reuse_query": True,
            "previous_query": {"query": "synthetic robot", "max_results": 1},
        }
        assert client.post("/api/search", json=payload).json()["retrieval_source"] == "openalex_fresh"
        assert client.post("/api/search", json=payload).json()["retrieval_source"] == "local_cache"
        counters = app.state.metrics.render()
        assert 'research_cache_total{decision="miss"} 1.0' in counters
        assert 'research_cache_total{decision="hit"} 1.0' in counters


def test_out_of_year_live_and_cached_records_are_not_served(tmp_path):
    class OutsideYear(SyntheticProvider):
        async def search(self, intent):
            records, total = await super().search(intent)
            records[0].update(publication_year=1900, publication_date="1900-01-01")
            return records, total

    app = create_app(
        Settings(database_url=f"sqlite:///{tmp_path / 'date.db'}", gemini_api_key="", groq_api_key=""),
        openalex_client=OutsideYear(),
    )
    payload = {
        "message": "synthetic robot",
        "action": "search",
        "reuse_query": True,
        "previous_query": {"query": "synthetic robot", "year_from": 2022, "max_results": 1},
    }
    with TestClient(app) as client:
        result = client.post("/api/search", json=payload).json()
        assert result["works"] == []
        with app.state.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(RawPublicationRecord)) == 1
            assert session.scalar(select(func.count()).select_from(SearchCache)) == 0
        # Seed a legacy bad cache entry to test validation before reuse.
        from backend.app.repository import fingerprint

        intent = SearchIntent(**payload["previous_query"])
        with app.state.session_factory() as session:
            session.add(
                SearchCache(
                    fingerprint=fingerprint(intent),
                    intent=intent.model_dump(mode="json"),
                    work_ids=["https://openalex.org/W900000001"],
                    total_matches=1,
                    fetched_at=utc_now(),
                )
            )
            session.commit()
        assert client.post("/api/search", json=payload).json()["works"] == []
        assert 'research_cache_total{decision="incomplete"} 1.0' in app.state.metrics.render()
