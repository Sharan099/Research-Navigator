"""Quota-only switching, shared budgets and citation checks use synthetic providers."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import create_database_engine
from backend.app.llm import generate_json
from backend.app.main import create_app
from backend.app.models import Base, DailyUsage, Publication
from backend.app.providers import ProviderRuntime


def envelope(value):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


def prompt():
    return {
        "systemInstruction": {"parts": [{"text": "Plan JSON"}]},
        "contents": [{"parts": [{"text": "robotics"}]}],
        "generationConfig": {"responseJsonSchema": {"type": "object"}},
    }


def test_successful_gemini_never_calls_groq(mock_http):
    requests = mock_http({"candidates": [{"content": {"parts": [{"text": '{"query":"robotics"}'}]}}]})
    _, provider, warnings = asyncio.run(
        generate_json(Settings(gemini_api_key="synthetic-gemini", groq_api_key="synthetic-groq"), prompt())
    )
    assert provider == "gemini" and warnings == []
    assert len(requests) == 1 and requests[0].url.host == "generativelanguage.googleapis.com"


def test_gemini_timeout_never_calls_groq(mock_http):
    requests = mock_http(error=httpx.ReadTimeout("synthetic timeout"))
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(generate_json(Settings(gemini_api_key="synthetic-gemini", groq_api_key="synthetic-groq"), prompt()))
    assert len(requests) == 1 and requests[0].url.host == "generativelanguage.googleapis.com"


def test_groq_primary_when_gemini_not_configured(mock_http):
    requests = mock_http(envelope({"query": "robotics"}))
    _, provider, _ = asyncio.run(generate_json(Settings(gemini_api_key="", groq_api_key="synthetic-groq"), prompt()))
    assert provider == "groq" and len(requests) == 1 and requests[0].url.host == "api.groq.com"


@pytest.mark.parametrize("gemini_cap,gemini_status", [(0, 200), (6, 429)])
def test_quota_switch_preserves_one_shared_turn_and_provider_counts(tmp_path, gemini_cap, gemini_status):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'quota.db'}",
        gemini_api_key="synthetic-gemini",
        groq_api_key="synthetic-groq",
        gemini_requests_per_day=gemini_cap,
    )
    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    attempts = []

    def respond(request):
        attempts.append(request.url.host)
        return (
            httpx.Response(gemini_status, json={})
            if "googleapis" in request.url.host
            else httpx.Response(200, json=envelope({"query": "robotics"}))
        )

    async def run():
        runtime = ProviderRuntime(settings, sessions)
        runtime.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with runtime.turn():
                text, provider, warnings = await generate_json(
                    settings,
                    {
                        "systemInstruction": {"parts": [{"text": "Plan in JSON"}]},
                        "contents": [{"parts": [{"text": "robotics"}]}],
                        "generationConfig": {"responseJsonSchema": {"type": "object"}},
                    },
                )
                assert json.loads(text)["query"] == "robotics" and provider == "groq"
                assert "quota" in warnings[0]
        finally:
            await runtime.close()

    asyncio.run(run())
    with sessions() as session:
        counts = {row.resource: row.used for row in session.scalars(select(DailyUsage))}
    assert counts["ai_turns"] == counts["groq"] == 1
    assert counts.get("gemini", 0) == (0 if gemini_cap == 0 else 1)
    assert attempts.count("api.groq.com") == 1
    engine.dispose()


@pytest.fixture
def fallback_app(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'app.db'}",
            gemini_api_key="synthetic-gemini",
            groq_api_key="synthetic-groq",
            gemini_requests_per_day=0,
            openalex_api_key="",
        )
    )
    with TestClient(app) as client:
        with app.state.session_factory() as session:
            session.add(
                Publication(
                    openalex_id="https://openalex.org/W1",
                    title="Synthetic fixture",
                    abstract="Synthetic robots sense touch.",
                )
            )
            session.commit()
        yield app, client


@pytest.mark.parametrize(
    "quote,expected", [("Synthetic robots sense touch.", "groq"), ("Invented quote", "evidence_only")]
)
def test_groq_answers_keep_citation_validation(fallback_app, mock_http, quote, expected):
    app, client = fallback_app
    requests = mock_http(
        envelope(
            {
                "claims": [
                    {
                        "text": "The fixture mentions touch.",
                        "citations": [{"work_id": "https://openalex.org/W1", "quote": quote}],
                    }
                ]
            }
        )
    )
    result = client.post(
        "/api/search",
        json={"message": "Explain this abstract", "action": "answer", "work_ids": ["https://openalex.org/W1"]},
    ).json()
    assert result["answer_mode"] == expected
    assert requests[0].url.host == "api.groq.com"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-groq"
    assert "x-goog-api-key" not in requests[0].headers
    assert result["claims"] if expected == "groq" else not result["claims"]


def test_groq_planning_and_clarification_mode(fallback_app, mock_http):
    _, client = fallback_app
    mock_http(envelope({"query": "robotics", "clarification_question": "Which robotics application matters?"}))
    result = client.post("/api/search", json={"message": "Find the best robotics papers"}).json()
    assert result["status"] == "clarification" and result["parser_mode"] == "groq"


@pytest.mark.parametrize("status", [401, 403, 500])
def test_nonquota_failure_does_not_switch(tmp_path, mock_http, status):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'nofallback.db'}",
            gemini_api_key="synthetic-gemini",
            groq_api_key="synthetic-groq",
        )
    )
    requests = mock_http(status=status, payload={})
    with TestClient(app) as client:
        result = client.post("/api/search", json={"message": "robotics"}).json()
        assert result["parser_mode"] == "rules"
    assert all(request.url.host != "api.groq.com" for request in requests)


def test_shared_ai_cap_blocks_both_providers(fallback_app, mock_http):
    app, client = fallback_app
    from dataclasses import replace

    app.state.providers.settings = replace(app.state.providers.settings, ai_turns_per_day=0)
    requests = mock_http(envelope({"query": "robotics"}))
    result = client.post("/api/search", json={"message": "robotics"}).json()
    assert result["parser_mode"] == "rules"
    assert all(request.url.host == "api.openalex.org" for request in requests)
    assert any("shared daily AI-turn budget" in warning for warning in result["warnings"])


@pytest.mark.parametrize(
    "payload,status", [(envelope({"claims": []}), 200), ({"choices": None}, 200), ({"choices": [None]}, 200), ({}, 429)]
)
def test_failed_groq_never_fabricates_answers(fallback_app, mock_http, payload, status):
    _, client = fallback_app
    mock_http(payload, status=status)
    result = client.post(
        "/api/search",
        json={"message": "Explain this abstract", "action": "answer", "work_ids": ["https://openalex.org/W1"]},
    ).json()
    assert result["answer_mode"] == "evidence_only" and result["claims"] == []
