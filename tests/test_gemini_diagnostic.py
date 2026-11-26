"""Diagnostic quota/account checks use synthetic envelopes and disposable databases."""

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
from scripts import diagnose_gemini as diagnostic
from scripts.diagnose_gemini import list_models


@pytest.fixture
def config(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'source.db'}", gemini_api_key="synthetic-secret", openalex_api_key=""
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
    monkeypatch.setattr(
        diagnostic,
        "list_models",
        lambda _: [{"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]}],
    )
    return settings


def test_candidate_filter_prefers_stable_text_lite():
    names = [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash-lite-preview",
        "gemini-image-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-omni-flash-lite",
    ]
    models = [{"name": "models/" + name, "supportedGenerationMethods": ["generateContent"]} for name in names]
    assert diagnostic.candidate_models(models, "gemini-3.5-flash") == ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


def test_preflight_never_generates(config, monkeypatch):
    monkeypatch.setattr(diagnostic, "verify_model", lambda *args: pytest.fail("Generation in preflight"))
    result = diagnostic.diagnose(config)
    assert result["status"] == "preflight_only" and not result["generation_verified"]


def test_exhausted_turns_never_generate_or_reset(config, monkeypatch):
    engine = create_database_engine(config.database_url)
    sessions = sessionmaker(engine)
    with sessions() as session:
        session.add(DailyUsage(day=datetime.now(timezone.utc).date(), resource="ai_turns", used=3))
        session.commit()
    monkeypatch.setattr(diagnostic, "verify_model", lambda *args: pytest.fail("Quota bypass"))
    result = diagnostic.diagnose(config, live=True)
    assert result["status"] == "local_budget_exhausted"
    with sessions() as session:
        assert session.scalar(select(DailyUsage.used)) == 3
    engine.dispose()


def test_success_uses_real_routes_and_persistent_ledger(config):
    # The same mock transport returns different schemas for the actual planner/answer prompts.
    original = httpx.AsyncClient

    def dispatch(request):
        body = json.loads(request.content)
        if "Plan scholarly" in body["systemInstruction"]["parts"][0]["text"]:
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
            200,
            json={"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(generated)}]}}]},
        )

    from unittest.mock import patch

    with patch.object(
        httpx, "AsyncClient", lambda **kw: original(**{**kw, "transport": httpx.MockTransport(dispatch)})
    ):
        result = diagnostic.diagnose(config, live=True)
    assert result["generation_verified"] and result["status"] == "verified"
    assert result["usage_after"]["used"] == {"ai_turns": 2, "gemini": 2, "groq": 0, "openalex": 0}
    assert len(result["checks"][0]["attempts"]) == 2
    assert "Synthetic robots" not in json.dumps(result)


@pytest.mark.parametrize("status,code", [(401, "provider_auth_error"), (429, "provider_rate_limit")])
def test_account_failure_stops_without_switching(config, mock_http, status, code):
    requests = mock_http(status=status, payload={})
    result = diagnostic.diagnose(config, live=True)
    assert not result["generation_verified"] and len(requests) == 1
    assert result["checks"][0]["warnings"] == [code]


def test_attempt_limit_is_enforced(config, mock_http):
    requests = mock_http(error=httpx.ReadTimeout("synthetic timeout"))
    result = diagnostic.diagnose(config, live=True, max_attempts=1)
    assert len(requests) == 1 and not result["generation_verified"]
    assert result["usage_after"]["used"]["gemini"] == 1


def test_discovery_auth_error_is_redacted(config, monkeypatch):
    def fail(_):
        request = httpx.Request("GET", "https://example.test/?key=synthetic-secret")
        raise httpx.HTTPStatusError("synthetic-secret", request=request, response=httpx.Response(403, request=request))

    monkeypatch.setattr(diagnostic, "list_models", fail)
    result = diagnostic.diagnose(config, live=True)
    assert result["failure"]["code"] == "provider_auth_error"
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.parametrize("payload", [None, {"models": None}, {"models": [None]}])
def test_bad_model_metadata_is_rejected(config, monkeypatch, payload):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: original(
            **kw, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(payload)))
        ),
    )
    with pytest.raises(ValueError, match="Invalid model"):
        list_models(config)


def test_missing_key_does_not_contact_provider(config, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(diagnostic, "list_models", lambda _: pytest.fail("Missing key sent upstream"))
    assert diagnostic.diagnose(replace(config, gemini_api_key=""), live=True)["status"] == "missing_key"
