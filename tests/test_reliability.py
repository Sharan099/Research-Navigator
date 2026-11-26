"""Regression scenarios use synthetic records and mocked HTTP, never live APIs."""

import asyncio
import json
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from test_data_pipeline import SAMPLE_WORK, FakeOpenAlex

from backend.app.config import Settings
from backend.app.interpreter import SearchInterpreter
from backend.app.main import create_app
from backend.app.models import Base, IngestionRun, Publication, RawPublicationRecord, SearchCache, utc_now
from backend.app.openalex import OpenAlexClient
from backend.app.repository import abstract_from_index, fingerprint, normalize_doi
from backend.app.schemas import ClarificationTurn, SearchIntent
from backend.app.service import is_fresh


def settings(tmp_path, **kwargs):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'reliability.db'}", gemini_api_key="", openalex_api_key="", **kwargs
    )


@pytest.mark.parametrize("message", ["   ", "\t\n ", " x ", "Find robots since 2025 before 2020"])
def test_invalid_request_returns_422_without_retrieval(tmp_path, message):
    fake = FakeOpenAlex()
    with TestClient(create_app(settings(tmp_path), fake)) as client:
        assert client.post("/api/search", json={"message": message}).status_code == 422
    assert fake.calls == 0


def test_unknown_request_field_is_rejected(tmp_path):
    fake = FakeOpenAlex()
    with TestClient(create_app(settings(tmp_path), fake)) as client:
        response = client.post("/api/search", json={"message": "tactile sensors", "sql": "SELECT 1"})
        assert response.status_code == 422
    assert fake.calls == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"query": "  "},
        {"query": "robots", "sql": "DROP TABLE publications"},
        {"query": "robots", "year_from": "2024"},
        {"query": "robots", "max_results": True},
        {"query": "robots", "sort": "invented"},
        {"query": "robots", "max_results": 21},
        {"query": "robots", "year_from": 2025, "year_to": 2020},
    ],
)
def test_invalid_structured_intent_is_rejected(fields):
    with pytest.raises(ValidationError):
        SearchIntent.model_validate(fields)


def test_fingerprint_covers_all_search_parameters():
    base = SearchIntent(query=" Robot   sensors ")
    assert fingerprint(base) == fingerprint(SearchIntent(query="robot sensors"))
    for change in ({"year_from": 2024}, {"year_to": 2020}, {"sort": "newest"}, {"max_results": 1}):
        assert fingerprint(base) != fingerprint(base.model_copy(update=change))


def test_freshness_handles_sqlite_naive_dates_zero_ttl_and_future_dates():
    now = utc_now()
    assert is_fresh(now.replace(tzinfo=None), 24)
    assert not is_fresh(now - timedelta(hours=24), 24)
    assert not is_fresh(now, 0)
    assert not is_fresh(now, -1)
    assert not is_fresh(now + timedelta(hours=1), 24)


@pytest.mark.parametrize("damage", ["missing_work", "missing_id", "short_preview", "duplicate_id", "stale", "future"])
def test_incomplete_or_expired_cache_refreshes(tmp_path, damage):
    fake = FakeOpenAlex()
    app = create_app(settings(tmp_path), fake)
    with TestClient(app) as client:
        assert client.post("/api/search", json={"message": "tactile sensors"}).status_code == 200
        with app.state.session_factory() as session:
            cached = session.scalar(select(SearchCache))
            if damage == "missing_work":
                session.delete(session.scalar(select(Publication)))
            elif damage == "missing_id":
                cached.work_ids = ["https://openalex.org/W999"]
            elif damage == "short_preview":
                cached.total_matches = 2
            elif damage == "duplicate_id":
                cached.work_ids = cached.work_ids * 2
            elif damage == "stale":
                cached.fetched_at = utc_now() - timedelta(hours=25)
            else:
                cached.fetched_at = utc_now() + timedelta(hours=1)
            session.commit()
        result = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert result["retrieval_source"] == "openalex_fresh"
        assert len(result["works"]) == 1
        assert fake.calls == 2


def test_empty_remote_result_is_cached_and_outage_is_distinct(tmp_path, mock_http):
    requests = mock_http({"results": [], "meta": {"count": 0}})
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        first = client.post("/api/search", json={"message": "tactile sensors"}).json()
        repeat = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert first["retrieval_source"] == "openalex_fresh"
        assert repeat["retrieval_source"] == "local_cache"
        assert repeat["total_matches"] == 0
        assert repeat["works"] == []
        assert len(requests) == 1
        mock_http(status=429)
        fallback = client.post("/api/search", json={"message": "unseen topic"}).json()
        assert fallback["retrieval_source"] == "local_fallback"
        assert "does not establish" in fallback["answer"]
        assert fallback["total_matches"] is None
        assert fallback["fetched_at"] is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {},
        {"results": None},
        {"results": [], "meta": None},
        {"results": [], "meta": {"count": "0"}},
        {"results": [], "meta": {"count": True}},
        {"results": [], "meta": {"count": -1}},
        {"results": [{}], "meta": {"count": 0}},
        {"results": [], "meta": {"count": 2_147_483_648}},
    ],
)
def test_malformed_envelope_uses_fallback(tmp_path, mock_http, payload):
    mock_http(payload)
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/search", json={"message": "tactile sensors"})
        assert response.status_code == 200
        assert response.json()["retrieval_source"] == "local_fallback"
        with app.state.session_factory() as session:
            assert session.scalar(select(SearchCache)) is None
            assert session.scalar(select(IngestionRun)).status == "failed"


@pytest.mark.parametrize("failure", ["rate_limit", "server", "timeout", "invalid_json"])
def test_upstream_failure_preserves_saved_data(tmp_path, mock_http, failure):
    mock_http({"results": [SAMPLE_WORK], "meta": {"count": 1}})
    app = create_app(settings(tmp_path, cache_ttl_hours=0))
    with TestClient(app) as client:
        client.post("/api/search", json={"message": "tactile sensors"})
        if failure == "timeout":
            mock_http(error=httpx.ReadTimeout("Synthetic timeout"))
        elif failure == "invalid_json":
            mock_http(raw="not JSON")
        else:
            mock_http(status=429 if failure == "rate_limit" else 503)
        result = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert result["retrieval_source"] == "local_fallback"
        assert len(result["works"]) == 1
        assert client.get("/api/stats").json()["raw_records"] == 1


@pytest.mark.parametrize(
    "patch",
    [
        {"id": None},
        {"id": "not-an-openalex-id"},
        {"id": "https://openalex.org/W" + "1" * 100},
        {"title": {}},
        {"doi": []},
        {"doi": "x" * 501},
        {"type": "x" * 81},
        {"publication_year": True},
        {"publication_year": "2024"},
        {"publication_year": 10000},
        {"cited_by_count": -1},
        {"cited_by_count": True},
        {"cited_by_count": 2_147_483_648},
        {"abstract_inverted_index": []},
        {"primary_location": "wrong shape"},
        {"primary_location": {"source": []}},
        {"primary_location": {"source": {"display_name": 5}}},
        {"primary_location": {"landing_page_url": {}}},
        {"authorships": {}},
        {"authorships": [None]},
        {"authorships": [{"author": []}]},
        {"authorships": [{"author": {"display_name": 3}}]},
        {"authorships": [{"author": {"id": "bad", "display_name": "Synthetic author"}}]},
    ],
)
def test_bad_work_does_not_discard_valid_work_or_raw_history(tmp_path, mock_http, patch):
    bad = deepcopy(SAMPLE_WORK)
    bad.update(patch)
    mock_http({"results": [bad, SAMPLE_WORK], "meta": {"count": 2}})
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        result = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert result["retrieval_source"] == "openalex_fresh"
        assert len(result["works"]) == 1
        assert any("rejected 1" in warning for warning in result["warnings"])
        with app.state.session_factory() as session:
            assert session.scalar(select(SearchCache)) is None
            raw = session.scalars(select(RawPublicationRecord).order_by(RawPublicationRecord.id)).all()
            assert [row.payload for row in raw] == [bad, SAMPLE_WORK]
            assert all(row.fetched_at is not None for row in raw)
            run = session.scalar(select(IngestionRun))
            assert run.status == "partial"
            assert run.records_received == 2


@pytest.mark.parametrize(
    "records,count", [([None, "bad", []], 3), ([SAMPLE_WORK], 100), ([SAMPLE_WORK, SAMPLE_WORK], 2), ([], 1)]
)
def test_partial_preview_is_never_reused_as_complete_cache(tmp_path, mock_http, records, count):
    requests = mock_http({"results": records, "meta": {"count": count}})
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        for _ in range(2):
            result = client.post("/api/search", json={"message": "tactile sensors"}).json()
            assert result["retrieval_source"] == "openalex_fresh"
            assert any("incomplete" in warning for warning in result["warnings"])
        assert len(requests) == 2
        assert client.get("/api/stats").json()["raw_records"] == len(records) * 2
        with app.state.session_factory() as session:
            raw = session.scalars(select(RawPublicationRecord).order_by(RawPublicationRecord.id)).all()
            assert [row.payload for row in raw] == records * 2
        if not result["works"]:
            assert "no usable records" in result["answer"]


def test_complete_preview_smaller_than_total_is_cached(tmp_path, mock_http, monkeypatch):
    requests = mock_http({"results": [SAMPLE_WORK], "meta": {"count": 100}})
    app = create_app(settings(tmp_path))

    async def interpret(message):
        return SearchIntent(query="tactile sensors", max_results=1), "rules", []

    monkeypatch.setattr(app.state.search_service.interpreter, "interpret", interpret)
    with TestClient(app) as client:
        first = client.post("/api/search", json={"message": "tactile sensors"}).json()
        repeat = client.post("/api/search", json={"message": "tactile sensors"}).json()
        assert first["retrieval_source"] == "openalex_fresh"
        assert repeat["retrieval_source"] == "local_cache"
        assert repeat["total_matches"] == 100
        assert "preview" in repeat["answer"]
        assert len(requests) == 1


def test_partial_refresh_invalidates_previous_cache(tmp_path, mock_http):
    mock_http({"results": [SAMPLE_WORK], "meta": {"count": 1}})
    app = create_app(settings(tmp_path, cache_ttl_hours=0))
    with TestClient(app) as client:
        client.post("/api/search", json={"message": "tactile sensors"})
        mock_http({"results": [SAMPLE_WORK, None], "meta": {"count": 2}})
        client.post("/api/search", json={"message": "tactile sensors"})
        with app.state.session_factory() as session:
            assert session.scalar(select(SearchCache)) is None


def test_missing_optional_metadata_stays_missing_in_raw_record(tmp_path, mock_http):
    record = {
        "id": SAMPLE_WORK["id"],
        "title": None,
        "doi": None,
        "publication_year": None,
        "cited_by_count": None,
        "primary_location": None,
        "authorships": None,
    }
    mock_http({"results": [record], "meta": {"count": 1}})
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        result = client.post("/api/search", json={"message": "tactile sensors"}).json()
        work = result["works"][0]
        assert work["title"] is work["doi"] is work["publication_year"] is work["source"] is None
        assert work["landing_page_url"] is None
        assert work["authors"] == []
        assert work["cited_by_count"] is None  # Unknown counts remain distinct from known zero.
        with app.state.session_factory() as session:
            assert session.scalar(select(RawPublicationRecord)).payload == record


def test_provider_order_and_distinct_work_identity_are_preserved(tmp_path, mock_http):
    second = deepcopy(SAMPLE_WORK)
    second["id"] = "https://openalex.org/W222"
    # Identical titles and DOIs are insufficient grounds for merging provider IDs.
    mock_http({"results": [second, SAMPLE_WORK], "meta": {"count": 2}})
    app = create_app(settings(tmp_path))
    with TestClient(app) as client:
        for source in ("openalex_fresh", "local_cache"):
            result = client.post("/api/search", json={"message": "tactile sensors"}).json()
            assert result["retrieval_source"] == source
            assert [work["id"] for work in result["works"]] == [second["id"], SAMPLE_WORK["id"]]
        assert client.get("/api/stats").json()["publications"] == 2


@pytest.mark.parametrize(
    "value,expected",
    [
        (42, None),
        ("doi:10.1234/ABC", "10.1234/abc"),
        ("http://doi.org/10.1234/ABC", "10.1234/abc"),
        ("https://dx.doi.org/10.1234/ABC", "10.1234/abc"),
    ],
)
def test_doi_input_normalization(value, expected):
    assert normalize_doi(value) == expected


def test_abstract_ignores_malformed_positions_without_inventing_words():
    assert abstract_from_index({"known": [0], "bad": [True, -1, 2000, "1"], "wrong": 1}) == "known"
    assert abstract_from_index([]) is None
    assert abstract_from_index({}) is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"candidates": None},
        {"candidates": []},
        {"candidates": [{"content": {"parts": [{"text": None}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]},
        {"candidates": [{"content": {"parts": [{"text": '{"query":"robots","sql":"SELECT 1"}'}]}}]},
    ],
)
def test_malformed_gemini_uses_rules(tmp_path, mock_http, payload):
    mock_http(payload)
    config = Settings(
        database_url=settings(tmp_path).database_url, gemini_api_key="synthetic-test-key", openalex_api_key=""
    )
    intent, mode, warnings = asyncio.run(SearchInterpreter(config).interpret("Find tactile sensors"))
    assert mode == "rules"
    assert intent.query == "tactile sensors"
    assert any("unavailable" in warning for warning in warnings)


def test_valid_gemini_output_is_validated_and_uses_server_header(tmp_path, mock_http):
    requests = mock_http(
        {"candidates": [{"content": {"parts": [{"text": '{"query":"tactile sensors","max_results":1}'}]}}]}
    )
    config = Settings(
        database_url=settings(tmp_path).database_url, gemini_api_key="synthetic-test-key", openalex_api_key=""
    )
    intent, mode, warnings = asyncio.run(SearchInterpreter(config).interpret("Find tactile sensors"))
    assert (mode, warnings, intent.max_results) == ("gemini", [], 1)
    assert requests[0].headers["x-goog-api-key"] == "synthetic-test-key"
    assert "synthetic-test-key" not in str(requests[0].url)


@pytest.mark.parametrize("failure", ["rate_limit", "server", "timeout", "invalid_json"])
def test_gemini_retrieval_failure_uses_rules(mock_http, failure):
    if failure == "timeout":
        mock_http(error=httpx.ReadTimeout("Synthetic timeout"))
    elif failure == "invalid_json":
        mock_http(raw="not JSON")
    else:
        mock_http(status=429 if failure == "rate_limit" else 503)
    config = Settings(gemini_api_key="synthetic-test-key", openalex_api_key="")
    intent, mode, warnings = asyncio.run(SearchInterpreter(config).interpret("Find tactile sensors"))
    assert intent.query == "tactile sensors"
    assert mode == "rules" and warnings


def test_openalex_encodes_filters_sort_and_preview_limit(mock_http):
    requests = mock_http({"results": [SAMPLE_WORK], "meta": {"count": 100}})
    config = Settings(gemini_api_key="", openalex_api_key="synthetic-test-key")
    records, count = asyncio.run(
        OpenAlexClient(config).search(
            SearchIntent(query="robot & sensors", year_from=2020, year_to=2024, sort="most_cited", max_results=1)
        )
    )
    assert records == [SAMPLE_WORK] and count == 100
    params = requests[0].url.params
    assert params["search"] == "robot & sensors"
    assert params["filter"] == "from_publication_date:2020-01-01,to_publication_date:2024-12-31"
    assert params["sort"] == "cited_by_count:desc" and params["per_page"] == "1"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-test-key"


def test_models_compile_for_postgresql():
    for table in Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))


def gemini_response(plan):
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(plan)}]}}]}


def test_clarification_preserves_context_and_only_searches_when_ready(tmp_path, mock_http):
    requests = mock_http(
        gemini_response(
            {
                "query": "robotics",
                "clarification_question": "Do you want recent papers or highly cited foundational work?",
                "priorities": ["Clarify what best means"],
                "search_rationale": "Ranking depends on your research goal.",
            }
        )
    )
    config = Settings(
        database_url=settings(tmp_path).database_url, gemini_api_key="synthetic-test-key", openalex_api_key=""
    )
    fake = FakeOpenAlex()
    app = create_app(config, fake)
    with TestClient(app) as client:
        original = "Find the best robotics papers"
        first = client.post("/api/search", json={"message": original}).json()
        assert first["status"] == "clarification" and first["parser_mode"] == "gemini"
        assert fake.calls == 0
        assert client.get("/api/stats").json()["ingestion_runs"] == 0
        with app.state.session_factory() as session:
            assert session.scalar(select(SearchCache)) is None
        content = json.loads(requests[0].content)
        assert json.loads(content["contents"][0]["parts"][0]["text"])["message"] == original
        assert "systemInstruction" in content

        completed_plan = {
            "query": "robotic grasping",
            "sort": "newest",
            "year_from": 2022,
            "priorities": ["Recent grasping research"],
            "search_rationale": "Prioritize recent work on your stated application.",
        }
        followup_requests = mock_http(gemini_response(completed_plan))
        request = {
            "message": original,
            "clarifications": [
                {"question": first["question"], "answer": "Recent robotic grasping papers since 2022"},
            ],
        }
        result = client.post("/api/search", json=request).json()
        assert result["status"] == "results" and result["parser_mode"] == "gemini"
        assert result["query"]["query"] == "robotic grasping"
        assert result["query"]["year_from"] == 2022 and result["query"]["sort"] == "newest"
        assert result["priorities"] == completed_plan["priorities"]
        assert fake.calls == 1
        sent = json.loads(json.loads(followup_requests[0].content)["contents"][0]["parts"][0]["text"])
        assert sent == request
        with app.state.session_factory() as session:
            cached_intent = session.scalar(select(SearchCache)).intent
            assert "priorities" not in cached_intent and "clarification_question" not in cached_intent
        # Explanation wording must not cause an identical retrieval intent to miss the cache.
        completed_plan["priorities"] = ["Recency, with different explanation wording"]
        mock_http(gemini_response(completed_plan))
        repeat = client.post("/api/search", json=request).json()
        assert repeat["retrieval_source"] == "local_cache"
        assert repeat["priorities"] == completed_plan["priorities"]
        assert fake.calls == 1


@pytest.mark.parametrize(
    "clarifications",
    [
        [{"question": "What research topic?", "answer": "   "}],
        [{"question": "What research topic?", "answer": "x" * 501}],
        [{"question": "What research topic?", "answer": "robotics", "sql": "SELECT 1"}],
        [{"question": "What research topic?", "answer": "robotics"}] * 3,
    ],
)
def test_invalid_clarification_context_is_rejected(tmp_path, clarifications):
    fake = FakeOpenAlex()
    with TestClient(create_app(settings(tmp_path), fake)) as client:
        response = client.post("/api/search", json={"message": "Find papers", "clarifications": clarifications})
        assert response.status_code == 422
    assert fake.calls == 0


def test_clarification_loop_is_bounded_without_silent_retrieval(tmp_path, mock_http):
    mock_http(gemini_response({"query": "robotics", "clarification_question": "What is your application?"}))
    config = Settings(
        database_url=settings(tmp_path).database_url, gemini_api_key="synthetic-test-key", openalex_api_key=""
    )
    fake = FakeOpenAlex()
    with TestClient(create_app(config, fake)) as client:
        response = client.post(
            "/api/search",
            json={
                "message": "Find papers",
                "clarifications": [
                    {"question": "What research topic?", "answer": "robotics"},
                    {"question": "What matters most?", "answer": "recency"},
                ],
            },
        )
        assert response.status_code == 422
        assert "Start a new search" in response.json()["detail"]
    assert fake.calls == 0


@pytest.mark.parametrize(
    "patch",
    [
        {"clarification_question": "   "},
        {"clarification_question": "x" * 301},
        {"priorities": ["a", "b", "c", "d"]},
        {"priorities": "recency"},
        {"priorities": ["x" * 121]},
        {"search_rationale": {"sql": "SELECT 1"}},
    ],
)
def test_invalid_gemini_planning_fields_use_honest_fallback(mock_http, patch):
    mock_http(gemini_response({"query": "robotics", **patch}))
    planner = SearchInterpreter(Settings(gemini_api_key="synthetic-test-key", openalex_api_key=""))
    plan, mode, warnings = asyncio.run(planner.interpret("Find tactile sensors"))
    assert mode == "rules" and warnings
    assert plan.query == "tactile sensors" and plan.clarification_question is None


def test_rule_fallback_retains_answers_but_not_assistant_questions(mock_http):
    mock_http(status=429)
    planner = SearchInterpreter(Settings(gemini_api_key="synthetic-test-key", openalex_api_key=""))
    plan, mode, warnings = asyncio.run(
        planner.interpret(
            "Find tactile sensors",
            [
                ClarificationTurn(
                    question="Choose inventedquestiontoken?", answer="most cited robotic grasping since 2022"
                ),
            ],
        )
    )
    assert mode == "rules" and "HTTP 429" in warnings[0]
    assert "grasping" in plan.query and "inventedquestiontoken" not in plan.query
    assert plan.year_from == 2022 and plan.sort == "most_cited"


def test_new_search_has_no_server_side_conversation_memory(tmp_path, mock_http):
    requests = mock_http(gemini_response({"query": "robotics", "clarification_question": "What application?"}))
    config = Settings(
        database_url=settings(tmp_path).database_url, gemini_api_key="synthetic-test-key", openalex_api_key=""
    )
    with TestClient(create_app(config, FakeOpenAlex())) as client:
        client.post("/api/search", json={"message": "Find robotics papers"})
        client.post("/api/search", json={"message": "Find microscopy papers"})
    second = json.loads(json.loads(requests[1].content)["contents"][0]["parts"][0]["text"])
    assert second == {"message": "Find microscopy papers", "clarifications": []}


@pytest.mark.parametrize("message", ["Find papers", "Find the best robotics papers"])
def test_rules_can_ask_basic_clarifications_without_claiming_llm_use(message):
    planner = SearchInterpreter(Settings(gemini_api_key="", openalex_api_key=""))
    plan, mode, warnings = asyncio.run(planner.interpret(message))
    assert mode == "rules" and warnings and plan.clarification_question
