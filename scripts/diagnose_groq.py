"""Groq metadata preflight; --live explicitly permits at most two charged app-route requests."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import create_database_engine
from backend.app.gemini import failure_details
from backend.app.providers import ProviderRuntime
from scripts.diagnose_gemini import verify_model


def list_models(settings):
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        response = client.get(
            "https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {settings.groq_api_key}"}
        )
        response.raise_for_status()
        models = response.json()["data"]
        if not isinstance(models, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in models
        ):
            raise ValueError("Invalid model listing")
        return {row["id"] for row in models}


def diagnose(settings, live=False, max_attempts=2):
    if not 1 <= max_attempts <= 2:
        raise ValueError("max_attempts must be between 1 and 2")
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "configured_model": settings.groq_model,
        "generation_verified": False,
        "live_requested": live,
        "checks": [],
    }
    if not settings.groq_api_key:
        return {**report, "status": "missing_key"}
    engine = create_database_engine(settings.database_url)
    try:
        sessions = sessionmaker(engine)
        usage = ProviderRuntime(settings, sessions).usage()
        report["usage_before"] = usage
        try:
            report["configured_model_listed"] = settings.groq_model in list_models(settings)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return {
                **report,
                "status": "model_discovery_failed",
                "failure": failure_details(exc, "discovery", provider="Groq"),
            }
        if not report["configured_model_listed"]:
            return {**report, "status": "configured_model_not_listed"}
        if not live:
            return {**report, "status": "preflight_only"}
        if any(usage["used"][name] >= usage["limits"][name] for name in ("groq", "ai_turns")):
            return {**report, "status": "local_budget_exhausted", "generation_attempts": 0}
        if engine.dialect.name != "sqlite" or not engine.url.database or not Path(engine.url.database).is_file():
            return {**report, "status": "sqlite_source_required"}
        Path(".runtime").mkdir(exist_ok=True)
        check = verify_model(settings, engine.url.database, sessions, [max_attempts], provider="groq")
        report["checks"].append(check)
        report["generation_attempts"] = len(check["attempts"])
        report["usage_after"] = ProviderRuntime(settings, sessions).usage()
        passed = check["planning_passed"] and check["answer_passed"]
        return {
            **report,
            "generation_verified": bool(passed),
            "status": "verified" if passed else "generation_unresolved",
        }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Spend at most two attempts within existing daily limits")
    parser.add_argument("--max-attempts", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    try:
        report = diagnose(Settings(), args.live, args.max_attempts)
    except (SQLAlchemyError, OSError, ValueError, TypeError) as exc:
        report = {"status": "diagnostic_setup_failed", "generation_verified": False, "error_type": type(exc).__name__}
    Path(".runtime").mkdir(exist_ok=True)
    Path(".runtime/groq-diagnostic.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("verified", "preflight_only") else 1


if __name__ == "__main__":
    raise SystemExit(main())
