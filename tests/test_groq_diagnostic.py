"""Live-verification safeguards exercised offline with synthetic evidence and HTTP envelopes."""

import json
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import create_database_engine
from backend.app.migrations import migrate_database
from backend.app.models import DailyUsage, Publication
from scripts import diagnose_groq as diagnostic
from scripts.diagnose_groq import list_models


@pytest.fixture
def config(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'source.db'}",
        gemini_api_key="synthetic-gemini",
        groq_api_key="synthetic-groq",
        openalex_api_key="",
    )
    engine = create_database_engine(settings.database_url)
    migrate_database(engine)
    with sessionmaker(engine)() as session:
        session.add(
            Publication(
                openalex_id="https://openalex.org/W1",
                title="Synthetic fixture",
                abstract="Synthetic robots sense touch.",
            )
        )
        session.commit()
    engine.dispose()
    monkeypatch.setattr(diagnostic, "list_models", lambda _: set(settings.groq_models))
    return settings


def test_preflight_never_generates(config, monkeypatch):
    monkeypatch.setattr(diagnostic, "verify_model", lambda *a, **kw: pytest.fail("Preflight generation"))
    assert diagnostic.diagnose(config)["status"] == "preflight_only"


def test_shared_budget_is_never_reset_or_bypassed(config, monkeypatch):
    engine = create_database_engine(config.database_url)
    with sessionmaker(engine)() as session:
        session.add(DailyUsage(day=datetime.now(timezone.utc).date(), resource="ai_turns", used=3))
        session.commit()
    monkeypatch.setattr(diagnostic, "verify_model", lambda *a, **kw: pytest.fail("Quota bypass"))
    result = diagnostic.diagnose(config, live=True)
    assert result["status"] == "local_budget_exhausted" and result["generation_attempts"] == 0
    with sessionmaker(engine)() as session:
        assert session.scalar(select(DailyUsage.used)) == 3
    engine.dispose()


@pytest.mark.parametrize("maximum,verified", [(1, False), (2, True)])
def test_groq_route_verification_is_bounded_and_never_uses_gemini(config, monkeypatch, maximum, verified):
    original = httpx.AsyncClient
    hosts = []

    def dispatch(request):
        hosts.append(request.url.host)
        body = json.loads(request.content)
        if "Plan scholarly" in body["messages"][0]["content"]:
            generated = {"query": "robotics", "clarification_question": "Which research topic matters?"}
        else:
            generated = {
                "claims": [
                    {
                        "text": "The fixture mentions touch.",
                        "citations": [{"work_id": "https://openalex.org/W1", "quote": "Synthetic robots sense touch."}],
                    }
                ]
            }
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(generated)}}]}
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(dispatch)})
    )
    report = diagnostic.diagnose(config, live=True, max_attempts=maximum)
    assert report["generation_verified"] is verified
    assert len(hosts) == maximum and set(hosts) == {"api.groq.com"}
    assert report["usage_after"]["used"]["groq"] == maximum
    assert report["usage_after"]["used"]["ai_turns"] == maximum
    assert report["usage_after"]["used"]["gemini"] == 0
    assert "Synthetic robots" not in json.dumps(report)


def test_discovery_failure_redacts_credentials(config, monkeypatch):
    def fail(_):
        request = httpx.Request("GET", "https://example.test", headers={"Authorization": "Bearer synthetic-groq"})
        raise httpx.HTTPStatusError("synthetic-groq", request=request, response=httpx.Response(401, request=request))

    monkeypatch.setattr(diagnostic, "list_models", fail)
    result = diagnostic.diagnose(config, live=True)
    assert result["status"] == "model_discovery_failed"
    assert "synthetic-groq" not in json.dumps(result)


def test_model_listing_uses_header_not_query(config, monkeypatch):
    original = httpx.Client
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": config.groq_model}]})

    monkeypatch.setattr(httpx, "Client", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(respond)}))
    assert list_models(config) == {config.groq_model}
    assert seen[0].headers["authorization"] == "Bearer synthetic-groq"
    assert not seen[0].url.query
