"""Explicit Gemini preflight and bounded app-route verification. Never resets usage."""

import argparse
import json
import tempfile
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import create_database_engine
from backend.app.gemini import failure_details
from backend.app.main import create_app
from backend.app.models import Publication
from backend.app.providers import ProviderRuntime
from scripts.operations import backup

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
STOP_CODES = {"provider_auth_error", "provider_rate_limit", "local_budget_exhausted"}


class StoredEvidenceOnly:
    async def search(self, intent):
        raise httpx.RequestError("Source discovery is disabled during Gemini diagnosis")


def candidate_models(models, current):
    """Current model first, then one stable numbered text Flash-Lite alternative."""
    names = {
        model.get("name", "").removeprefix("models/")
        for model in models
        if "generateContent" in model.get("supportedGenerationMethods", [])
    }
    alternatives = sorted(
        (
            name
            for name in names
            if name.startswith("gemini-")
            and "flash-lite" in name
            and not any(word in name for word in ("preview", "latest", "image", "audio", "live", "tts", "omni"))
            and name != current
        ),
        reverse=True,
    )
    return [current] + alternatives[:1]


def budget_available(usage):
    return all(usage["used"][name] < usage["limits"][name] for name in ("gemini", "ai_turns"))


def list_models(settings):
    models, page = [], None
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        for _ in range(5):
            response = client.get(
                BASE_URL,
                headers={"x-goog-api-key": settings.gemini_api_key},
                params={"pageSize": 100, **({"pageToken": page} if page else {})},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                raise ValueError("Invalid model listing")
            if any(
                not isinstance(model, dict)
                or not isinstance(model.get("name"), str)
                or not isinstance(model.get("supportedGenerationMethods", []), list)
                for model in payload["models"]
            ):
                raise ValueError("Invalid model metadata")
            models.extend(payload.get("models", []))
            page = payload.get("nextPageToken")
            if not page:
                return models
    raise ValueError("Model listing exceeded five pages")


class DiagnosticRuntime(ProviderRuntime):
    """Routes charge the real durable ledger; research writes stay in the copy."""

    def __init__(self, settings, sessions, remaining, samples, provider="gemini"):
        super().__init__(settings, sessions)
        self.remaining, self.samples = remaining, samples
        self.provider = provider

    async def _request_json(self, provider, method, url, **kwargs):
        if provider != self.provider or self.remaining[0] <= 0:
            raise ValueError("Diagnostic attempt limit reached")
        self.remaining[0] -= 1
        stages = {}
        started = perf_counter()

        async def trace(name, info):
            # Keep timings only; trace info can contain credentials and request bodies.
            if name.endswith((".started", ".complete", ".failed")):
                stages[name] = round(perf_counter() - started, 3)

        kwargs["extensions"] = {"trace": trace}
        try:
            return await super()._request_json(provider, method, url, **kwargs)
        finally:
            self.samples.append({"seconds": round(perf_counter() - started, 3), "transport_stages": stages})


def verify_model(settings, source, ledger_sessions, remaining, provider="gemini"):
    """Exercise real FastAPI routes using in-process HTTP and the selected provider."""
    samples = []
    with tempfile.TemporaryDirectory(prefix="gemini-", dir=".runtime") as directory, ExitStack() as resources:
        target = Path(directory) / "research.sqlite3"
        backup(source, target)
        config = replace(
            settings,
            database_url=f"sqlite:///{target.resolve().as_posix()}",
            provider_attempts=1,
            groq_api_key="" if provider == "gemini" else settings.groq_api_key,
            gemini_api_key=settings.gemini_api_key if provider == "gemini" else "",
            app_mode="local",
            provider_timeout_seconds=45,
            request_timeout_seconds=50,
        )
        application = create_app(config, StoredEvidenceOnly())
        resources.callback(application.state.engine.dispose)
        application.state.providers = DiagnosticRuntime(config, ledger_sessions, remaining, samples, provider=provider)
        application.state.chats.runtime = application.state.providers
        with TestClient(application) as client:
            with application.state.session_factory() as session:
                work = session.scalar(
                    select(Publication).where(Publication.abstract.is_not(None), Publication.abstract != "").limit(1)
                )
                work_id = work.openalex_id if work else None
            # A synthetic ambiguous question exercises the actual planning payload without ingestion.
            plan = client.post("/api/search", json={"message": "Find the best research papers."}).json()
            planning_ok = plan.get("parser_mode") == provider
            result = {
                "model": settings.gemini_model if provider == "gemini" else settings.groq_models[0],
                "planning_passed": planning_ok,
                "planning_status": plan.get("status"),
                "answer_passed": False,
                "warnings": safe_codes(plan),
                "attempts": samples,
            }
            if not planning_ok or not work_id or remaining[0] <= 0:
                result["evidence_available"] = bool(work_id)
                return result
            answer = client.post(
                "/api/search",
                json={
                    "message": "Describe this abstract in one short cited claim.",
                    "action": "answer",
                    "work_ids": [work_id],
                },
            ).json()
            result["answer_passed"] = answer.get("answer_mode") == provider and bool(answer.get("claims"))
            result["claim_count"] = len(answer.get("claims", []))
            result["warnings"].extend(safe_codes(answer))
            return result


def safe_codes(result):
    # Warnings may include research text. Retain only the known diagnostic categories.
    codes = (
        "provider_auth_error",
        "provider_rate_limit",
        "provider_timeout",
        "provider_transport_error",
        "provider_model_unavailable",
        "provider_request_rejected",
        "provider_unavailable",
        "invalid_response",
        "invalid_json",
        "invalid_schema",
        "citation_mismatch",
        "generation_incomplete",
        "provider_blocked",
    )
    found = [code for code in codes if any(f"[{code}]" in text for text in result.get("warnings", []))]
    if any("local daily Gemini budget is exhausted" in text for text in result.get("warnings", [])):
        found.append("local_budget_exhausted")
    return found


def diagnose(settings, live=False, max_attempts=4):
    if not 1 <= max_attempts <= 4:
        raise ValueError("max_attempts must be between 1 and 4")
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "configured_model": settings.gemini_model,
        "generation_verified": False,
        "live_requested": live,
        "checks": [],
    }
    if not settings.gemini_api_key:
        return {**report, "status": "missing_key"}
    engine = create_database_engine(settings.database_url)
    try:
        sessions = sessionmaker(bind=engine)
        usage = ProviderRuntime(settings, sessions).usage()
        report["usage_before"] = usage
        try:
            models = list_models(settings)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {**report, "status": "model_discovery_failed", "failure": failure_details(exc, "discovery")}
        candidates = candidate_models(models, settings.gemini_model)
        report["candidates"] = candidates
        report["model_discovery_passed"] = True
        if not live:
            return {**report, "status": "preflight_only"}
        if not budget_available(usage):
            return {**report, "status": "local_budget_exhausted"}
        if engine.dialect.name != "sqlite" or not engine.url.database or not Path(engine.url.database).is_file():
            return {**report, "status": "sqlite_source_required"}
        remaining = [max_attempts]
        Path(".runtime").mkdir(exist_ok=True)
        for model in candidates:
            if remaining[0] <= 0:
                break
            check = verify_model(replace(settings, gemini_model=model), engine.url.database, sessions, remaining)
            report["checks"].append(check)
            if check["planning_passed"] and check["answer_passed"]:
                report.update(status="verified", generation_verified=True, verified_model=model)
                break
            if STOP_CODES.intersection(check["warnings"]):
                break
        else:
            report["status"] = "generation_unresolved"
        report.setdefault("status", "generation_unresolved")
        report["usage_after"] = ProviderRuntime(settings, sessions).usage()
        return report
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly authorize up to four charged generation attempts"
    )
    parser.add_argument("--max-attempts", type=int, choices=range(1, 5), default=4)
    args = parser.parse_args()
    try:
        report = diagnose(Settings(), args.live, args.max_attempts)
    except (SQLAlchemyError, OSError, ValueError, TypeError) as exc:
        report = {"status": "diagnostic_setup_failed", "generation_verified": False, "error_type": type(exc).__name__}
    Path(".runtime").mkdir(exist_ok=True)
    output = Path(".runtime/gemini-diagnostic.json")
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("verified", "preflight_only") else 1


if __name__ == "__main__":
    raise SystemExit(main())
