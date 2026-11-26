"""Measure stored-data integrity read-only and optionally benchmark a disposable offline app."""

import argparse
import asyncio
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.quality import is_present
from scripts.operations import readonly
from scripts.synthetic_provider import SyntheticProvider


def rate(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def source_fingerprint():
    """Bind measurements to code/config bytes without including data, credentials or reports."""
    root = Path(__file__).resolve().parents[1]
    paths = set()
    for directory in ("backend", "scripts", "tests", "migrations", "frontend", ".github"):
        paths.update(
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".js", ".cjs", ".html", ".css", ".yml"}
            and "__pycache__" not in path.parts
        )
    paths.update(
        root / name
        for name in (
            "pyproject.toml",
            "requirements.lock",
            "requirements-dev.lock",
            "package.json",
            "package-lock.json",
            "Dockerfile",
        )
    )
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def database_metrics(path):
    with readonly(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        works = connection.execute("SELECT * FROM publications").fetchall()
        raw = connection.execute(
            "SELECT provider_work_id FROM raw_publication_records WHERE provider='openalex'"
        ).fetchall()
        raw_ids = {row[0] for row in raw}
        cache = connection.execute("SELECT * FROM search_cache").fetchall()
        ids = {row["openalex_id"] for row in works}
        duplicates = len(works) - len(ids)
        covered = sum(bool(row["openalex_id"] in raw_ids and row["last_fetched_at"]) for row in works)
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        complete = 0
        fresh = 0
        now = datetime.now(timezone.utc)
        ages = []
        for row in works:
            if row["last_fetched_at"]:
                parsed = datetime.fromisoformat(row["last_fetched_at"])
                ages.append((now - parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)).total_seconds() / 3600)
        for row in cache:
            work_ids, intent = json.loads(row["work_ids"]), json.loads(row["intent"])
            expected = min(row["total_matches"], intent.get("max_results", 10))
            complete += len(set(work_ids)) == len(work_ids) == expected and set(work_ids) <= ids
            parsed = datetime.fromisoformat(row["fetched_at"])
            fresh += 0 <= (now - parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)).total_seconds() < 86400
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        ingestion = {}
        for row in connection.execute("SELECT status, COUNT(*) FROM ingestion_runs GROUP BY status"):
            ingestion[row[0]] = row[1]
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(ingestion_runs)")}
        measured_runs = received = valid = rejected = repeated = usable = linked_raw = None
        if {"records_valid", "records_rejected", "records_duplicates", "records_usable"} <= run_columns:
            counters = connection.execute(
                "SELECT COUNT(*), SUM(records_received), SUM(records_valid), SUM(records_rejected), "
                "SUM(records_duplicates), SUM(records_usable) FROM ingestion_runs WHERE records_valid IS NOT NULL"
            ).fetchone()
            measured_runs, received, valid, rejected, repeated, usable = counters
        raw_columns = {row[1] for row in connection.execute("PRAGMA table_info(raw_publication_records)")}
        if "ingestion_run_id" in raw_columns:
            linked_raw = connection.execute(
                "SELECT COUNT(*) FROM raw_publication_records WHERE ingestion_run_id IS NOT NULL"
            ).fetchone()[0]
        reconciliation = None
        if "records_excluded_dates" in run_columns:
            reconciliation = connection.execute(
                "SELECT COUNT(*) FROM ingestion_runs WHERE records_valid IS NOT NULL AND "
                "(records_received != records_valid + records_rejected OR "
                "records_valid != records_duplicates + records_excluded_dates + records_usable)"
            ).fetchone()[0]
        claims = citations = invalid_citations = 0
        runs = orphaned_busy = 0
        if "chat_messages" in tables:
            for row in connection.execute(
                "SELECT result FROM chat_messages WHERE role='assistant' AND result IS NOT NULL"
            ):
                result = json.loads(row[0])
                excerpts = {
                    source["id"]: " ".join(source.get("excerpt", "").split()) for source in result.get("sources", [])
                }
                for claim in result.get("claims", []):
                    claims += 1
                    invalid_citations += not bool(claim.get("citations"))
                    for citation in claim.get("citations", []):
                        citations += 1
                        quote = " ".join(citation.get("quote", "").split())
                        invalid_citations += not (quote and quote in excerpts.get(citation.get("work_id"), ""))
            runs = connection.execute("SELECT COUNT(*) FROM chat_runs").fetchone()[0]
            orphaned_busy = connection.execute(
                "SELECT COUNT(*) FROM conversations c LEFT JOIN chat_runs r ON r.id=c.active_request "
                "WHERE c.active_request IS NOT NULL AND (r.id IS NULL OR r.status!='running')"
            ).fetchone()[0]
        return {
            "publications": len(works),
            "raw_records": len(raw),
            "duplicate_provider_ids": duplicates,
            "provenance_completeness": rate(covered, len(works)),
            "foreign_key_violations": len(foreign_keys),
            "stored_year_date_conflicts": sum(
                row["publication_year"] is not None
                and row["publication_date"] is not None
                and row["publication_year"] != int(row["publication_date"][:4])
                for row in works
            ),
            "sqlite_integrity": integrity,
            "missing_title_rate": rate(sum(not is_present(row["title"]) for row in works), len(works)),
            "missing_abstract_rate": rate(sum(not is_present(row["abstract"]) for row in works), len(works)),
            "missing_doi_rate": rate(sum(not is_present(row["doi"]) for row in works), len(works)),
            "missing_publication_date_rate": rate(
                sum(not is_present(row["publication_date"]) for row in works), len(works)
            ),
            "retrieval_age_hours_max": round(max(ages), 2) if ages else None,
            "cache_entries": len(cache),
            "cache_identity_coverage_rate": rate(complete, len(cache)),
            "cache_within_reference_24h_rate": rate(fresh, len(cache)),
            "stored_claims": claims,
            "stored_citations": citations,
            "invalid_stored_citation_or_uncited_claims": invalid_citations,
            "chat_runs": runs,
            "orphaned_busy_conversations": orphaned_busy,
            "ingestion_run_status_counts": ingestion,
            "ingestion_runs_with_quality_counters": measured_runs,
            "measured_records_received": received,
            "measured_records_valid": valid,
            "measured_record_validation_rate": rate(valid, received) if received is not None else None,
            "measured_records_rejected": rejected,
            "measured_records_duplicates": repeated,
            "measured_records_usable": usable,
            "raw_records_with_run_lineage": linked_raw,
            "unreconciled_tracked_runs": reconciliation,
        }


def percentile(values, quantile):
    return round(sorted(values)[max(0, math.ceil(len(values) * quantile) - 1)], 2) if values else None


async def benchmark():
    import httpx
    from fastapi.testclient import TestClient

    from backend.app.config import Settings
    from backend.app.main import create_app

    provider = SyntheticProvider()
    with tempfile.TemporaryDirectory(prefix="research-evaluation-") as temporary:
        app = create_app(
            Settings(
                database_url=f"sqlite:///{Path(temporary) / 'disposable.db'}",
                gemini_api_key="",
                groq_api_key="",
                openalex_api_key="",
            ),
            openalex_client=provider,
        )
        durations = []
        errors = 0
        sources = {}
        payload = {
            "message": "robot learning",
            "action": "search",
            "reuse_query": True,
            "previous_query": {"query": "robot learning", "max_results": 1},
        }
        with TestClient(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                seed = await client.post("/api/search", json=payload)
                assert seed.status_code == 200, "Synthetic initial ingestion failed"

                async def request():
                    nonlocal errors
                    started = perf_counter()
                    response = await client.post("/api/search", json=payload)
                    durations.append((perf_counter() - started) * 1000)
                    errors += response.status_code != 200
                    source = response.json().get("retrieval_source", "error")
                    sources[source] = sources.get(source, 0) + 1

                start = perf_counter()
                for _ in range(4):
                    await asyncio.gather(*(request() for _ in range(5)))
                elapsed = perf_counter() - start
                provider.fail = True
                # Refresh proves that cached rows are genuinely bypassed during the fault scenario.
                outage = await client.post("/api/search", json={**payload, "refresh": True})
                fault_pass = outage.status_code == 200 and outage.json().get("retrieval_source") == "local_fallback"
                invalid = await client.post("/api/search", json={"message": "x"})
                invalid_pass = invalid.status_code == 422
        return {
            "fixture": "synthetic; disposable SQLite; no external API calls",
            "clients": 5,
            "requests": len(durations),
            "errors": errors,
            "latency_ms": {
                "p50": percentile(durations, 0.5),
                "p95": percentile(durations, 0.95),
                "p99": percentile(durations, 0.99),
                "mean": round(statistics.mean(durations), 2),
            },
            "requests_per_second": round(len(durations) / elapsed, 2),
            "retrieval_sources": sources,
            "provider_outage_fallback_pass": fault_pass,
            "invalid_input_rejected_pass": invalid_pass,
            "synthetic_provider_calls": provider.calls,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("research_navigator.db"))
    parser.add_argument("--output", type=Path, default=Path("evaluation/reports/current.json"))
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    result = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": source_fingerprint(),
        "runtime": {"python": platform.python_version(), "os": platform.system(), "machine": platform.machine()},
        "mode": "read-only stored-data audit",
        "data": database_metrics(args.database),
        "limitations": [
            "Local subset only; completeness of OpenAlex cannot be established.",
            "Missing source fields are reported, not fabricated or treated as pipeline failure.",
            "Exact quote checks do not measure semantic entailment or scientific accuracy.",
            "Reference freshness uses 24h, independent of runtime cache configuration.",
            "No PostgreSQL, container, remote CI or paid-provider validation implied.",
        ],
    }
    if args.benchmark:
        result["benchmark"] = asyncio.run(benchmark())
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        result["comparison"] = {
            key: {"before": baseline.get("data", {}).get(key), "after": value} for key, value in result["data"].items()
        }
    data = result["data"]
    passed = (
        data["sqlite_integrity"] == "ok"
        and data["duplicate_provider_ids"] == 0
        and data["foreign_key_violations"] == 0
        and data["stored_year_date_conflicts"] == 0
        and data["invalid_stored_citation_or_uncited_claims"] == 0
        and data["orphaned_busy_conversations"] == 0
        and data["provenance_completeness"] in (None, 1)
        and data["unreconciled_tracked_runs"] in (None, 0)
    )
    if "benchmark" in result:
        measured = result["benchmark"]
        passed = (
            passed
            and measured["errors"] == 0
            and measured["provider_outage_fallback_pass"]
            and measured["invalid_input_rejected_pass"]
        )
    result["integrity_gates_pass"] = bool(passed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = [
        "# Pipeline evaluation",
        "",
        f"Evaluated: {result['evaluated_at']}",
        "",
        f"Integrity gates: {'PASS' if passed else 'FAIL'}",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    summary.extend(f"| {key} | {value} |" for key, value in data.items())
    if "benchmark" in result:
        summary.extend(
            ["", "## Disposable offline benchmark", "", "```json", json.dumps(result["benchmark"], indent=2), "```"]
        )
    summary.extend(["", "## Interpretation", ""] + [f"- {line}" for line in result["limitations"]])
    args.output.with_suffix(".md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": args.output.as_posix(),
                "integrity_gates_pass": bool(passed),
                "data": data,
                "benchmark": result.get("benchmark"),
            },
            indent=2,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
