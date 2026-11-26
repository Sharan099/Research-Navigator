"""Synthetic pipeline records exercise accounting and concurrent database writes."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, get_ident
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from test_data_pipeline import SAMPLE_WORK

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Base, IngestionRun, Publication, PublicationAuthor, RawPublicationRecord
from backend.app.repository import save_openalex_work
from backend.app.schemas import SearchRequest


class Provider:
    def __init__(self, records):
        self.records = records

    async def search(self, intent):
        return self.records, len(self.records)


def test_missing_count_is_unknown_and_known_zero_stays_zero(tmp_path):
    missing, zero = deepcopy(SAMPLE_WORK), deepcopy(SAMPLE_WORK)
    missing["cited_by_count"] = None
    zero.update(id="https://openalex.org/W222", cited_by_count=0)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'counts.db'}", gemini_api_key="")
    app = create_app(settings, Provider([missing, zero]))
    with TestClient(app) as client:
        response = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert [work["cited_by_count"] for work in response["works"]] == [None, 0]


def test_ingestion_accounting_reconciles_raw_and_unique_usable(tmp_path):
    valid = deepcopy(SAMPLE_WORK)
    valid.update(publication_date="2026-03-01", publication_year=2026)
    outside = deepcopy(valid)
    outside.update(id="https://openalex.org/W222", publication_date="2025-01-01", publication_year=2025)
    records = [valid, deepcopy(valid), {"id": "broken"}, None, outside]
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'quality.db'}", gemini_api_key="")
    app = create_app(settings, Provider(records))
    with TestClient(app) as client:
        response = client.post(
            "/api/search",
            json={
                "message": "tactile sensors",
                "filters": {"date_range": "custom", "date_from": "2026-01-01", "date_to": "2026-12-31"},
            },
        )
        assert response.status_code == 200
        with app.state.session_factory() as session:
            run = session.scalar(select(IngestionRun))
            assert (run.records_received, run.records_valid, run.records_rejected) == (5, 3, 2)
            assert (run.records_duplicates, run.records_excluded_dates, run.records_usable) == (1, 1, 1)
            assert run.records_received == run.records_valid + run.records_rejected
            assert run.records_valid == run.records_duplicates + run.records_excluded_dates + run.records_usable
            assert run.completed_at is not None and run.duration_ms >= 0
            raw = session.scalars(select(RawPublicationRecord)).all()
            assert len(raw) == run.records_received
            assert {record.ingestion_run_id for record in raw} == {run.id}
            assert [record.payload for record in raw] == records


def test_concurrent_native_upsert_preserves_raw_and_ordered_author_links(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'concurrent.db'}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    barrier = Barrier(2)

    def store():
        barrier.wait(timeout=10)
        with sessions() as session:
            save_openalex_work(session, deepcopy(SAMPLE_WORK))
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: store(), range(2)))
    with sessions() as session:
        assert len(session.scalars(select(Publication)).all()) == 1
        assert len(session.scalars(select(RawPublicationRecord)).all()) == 2
        links = session.scalars(select(PublicationAuthor).order_by(PublicationAuthor.position)).all()
        assert [link.position for link in links] == list(range(len(SAMPLE_WORK["authorships"])))
        assert [link.author.openalex_id for link in links] == [
            entry["author"]["id"] for entry in SAMPLE_WORK["authorships"]
        ]
    engine.dispose()


def test_owned_search_keeps_database_sessions_off_event_loop(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'threads.db'}", gemini_api_key="")
    app = create_app(settings, Provider([deepcopy(SAMPLE_WORK)]))
    with TestClient(app):
        engine = app.state.session_factory.kw["bind"]
        threads = []

        def capture(*args):
            threads.append(get_ident())

        event.listen(engine, "before_cursor_execute", capture)

        async def search():
            loop_thread = get_ident()
            response = await app.state.search_service.search_owned(
                "tactile sensors", app.state.session_factory, request=SearchRequest(message="tactile sensors")
            )
            return loop_thread, response

        loop_thread, response = asyncio.run(search())
        event.remove(engine, "before_cursor_execute", capture)
        assert response.status == "results"
        assert threads and loop_thread not in threads


def test_owned_retry_rolls_back_partial_raw_before_fresh_transaction(tmp_path):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'retry.db'}", gemini_api_key="")
    app = create_app(settings, Provider([deepcopy(SAMPLE_WORK)]))
    with TestClient(app):
        engine = app.state.session_factory.kw["bind"]
        failed = []

        def fail_once(connection, cursor, statement, parameters, context, executemany):
            if "INSERT INTO publications" in statement and not failed:
                failed.append(True)
                raise OperationalError(statement, parameters, sqlite3.OperationalError("database is locked"))

        event.listen(engine, "before_cursor_execute", fail_once)
        response = asyncio.run(app.state.search_service.search_owned("tactile sensors", app.state.session_factory))
        event.remove(engine, "before_cursor_execute", fail_once)
        assert response.status == "results" and failed
        with app.state.session_factory() as session:
            assert len(session.scalars(select(IngestionRun)).all()) == 1
            assert len(session.scalars(select(RawPublicationRecord)).all()) == 1


def test_repeated_work_date_uses_final_snapshot_for_preview(tmp_path):
    earlier = deepcopy(SAMPLE_WORK)
    earlier.update(publication_date="2026-03-01", publication_year=2026)
    later = deepcopy(earlier)
    later.update(publication_date="2025-01-01", publication_year=2025)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'repeated.db'}", gemini_api_key="")
    app = create_app(settings, Provider([earlier, later]))
    with TestClient(app) as client:
        result = client.post(
            "/api/search",
            json={
                "message": "tactile sensors",
                "filters": {"date_range": "custom", "date_from": "2026-01-01", "date_to": "2026-12-31"},
            },
        ).json()
        assert result["works"] == []
        with app.state.session_factory() as session:
            run = session.scalar(select(IngestionRun))
            assert (run.records_valid, run.records_duplicates, run.records_excluded_dates, run.records_usable) == (
                2,
                1,
                1,
                0,
            )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Requires disposable PostgreSQL test service")
def test_postgresql_concurrent_identity_upserts_in_isolated_schema():
    from sqlalchemy import text

    url = os.environ["TEST_DATABASE_URL"]
    if not url.startswith("postgresql"):
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")
    schema = "pipeline_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine)
        barrier = Barrier(2)

        def store():
            barrier.wait(timeout=10)
            with sessions() as session:
                save_openalex_work(session, deepcopy(SAMPLE_WORK))
                session.commit()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: store(), range(2)))
        with sessions() as session:
            assert len(session.scalars(select(Publication)).all()) == 1
            assert len(session.scalars(select(RawPublicationRecord)).all()) == 2
            links = session.scalars(select(PublicationAuthor).order_by(PublicationAuthor.position)).all()
            assert [link.position for link in links] == list(range(len(SAMPLE_WORK["authorships"])))
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_cache_decision_events_distinguish_refresh_stale_and_incomplete(tmp_path):
    from datetime import timedelta

    from backend.app.models import SearchCache, utc_now

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'cache-events.db'}", gemini_api_key="")
    app = create_app(settings, Provider([deepcopy(SAMPLE_WORK)]))
    with TestClient(app):
        decisions = []

        async def emit(stage, status, details=None):
            if stage == "cache" and status == "completed":
                decisions.append(details["decision"])

        async def run(refresh=False):
            return await app.state.search_service.search_owned(
                "tactile sensors",
                app.state.session_factory,
                request=SearchRequest(message="tactile sensors", refresh=refresh),
                emit=emit,
            )

        asyncio.run(run())
        asyncio.run(run())
        asyncio.run(run(refresh=True))
        with app.state.session_factory() as session:
            session.scalar(select(SearchCache)).fetched_at = utc_now() - timedelta(days=3)
            session.commit()
        asyncio.run(run())
        with app.state.session_factory() as session:
            session.scalar(select(SearchCache)).work_ids = ["https://openalex.org/W999999999"]
            session.commit()
        result = asyncio.run(run())
        assert decisions == ["miss", "hit", "refresh", "stale", "incomplete"]
        assert any("Saved preview coverage is incomplete" in warning for warning in result.warnings)
