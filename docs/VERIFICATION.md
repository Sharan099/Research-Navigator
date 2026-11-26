# Current verification and remediation

Date: November 26, 2026. Scope: the current local prototype. Application code changed during this
remediation pass; the November 25 audit remains historical evidence. Existing research data was
evaluated read-only. All writes made by tests and benchmarks used separate databases.

## Subsequent publication evidence

The later provider diagnostic/release run recorded **299 Python passes and two PostgreSQL skips**
locally. The earlier 293-test coverage snapshot below remains a separate measurement.
The public [GitHub run at commit 959fcfa](https://github.com/Sharan099/Research-Navigator/actions/runs/45839781017)
then passed the Python 3.11/3.12/3.13 matrix, PostgreSQL job and dependency audit. Its browser dependency
installation failed before tests, leaving container/publish skipped. This verifies the remote
PostgreSQL test job; it does not establish a container release, deployed service or PostgreSQL backup drill.

Historical reports are archived under [docs/audits/2026-11-25](audits/2026-11-25/README.md).

## Executed checks

| Check | Target | Actual | Status / method |
|---|---|---|---|
| Python regression | No failures | 293 passed, 2 skipped | PASS for executed tests; PostgreSQL NOT VERIFIED |
| Combined line/branch coverage | At least 80% configured gate | 94.17%: (2,007 covered statements + 594 covered branches) / (2,094 statements + 668 branches) | PASS, pytest-cov XML |
| Frontend behavior | No failures | 34 passed | PASS, Node test runner |
| Browser behavior/accessibility | Local desktop/mobile and public isolation suites pass | 4 local + 2 public passed | PASS, Chromium; public suite uses a controlled HTTPS-origin proxy, not deployed hosting |
| Operations tooling | Backup/restore isolation contracts pass | 2 passed | PASS, separate unittest script |
| Static checks | No reported errors | Ruff and JavaScript syntax passed | PASS |
| Stored-data integrity | No duplicate provider IDs, FK errors, failed integrity check or broken tracked-run accounting | See aggregate report linked below | Read-only SQLite evaluation |
| Provider-outage/invalid-input scenarios | Honest local fallback; HTTP 422 for invalid request | Synthetic benchmark assertions | See aggregate report |
| PostgreSQL runtime | Isolated real PostgreSQL execution | No TEST_DATABASE_URL configured; 2 tests skipped | NOT VERIFIED |
| Docker runtime | Build and ready healthy nonroot container | Docker config/daemon access denied | NOT VERIFIED; `docker info` failed |
| Remote CI and deployment | Successful remote run and actual deployment | Not executed in this pass | NOT VERIFIED |
| Live Gemini/Groq generation | Successful validated planning and answers | No generation calls made | NOT VERIFIED; mocked provider contracts are separate |
| Scientific answer/retrieval quality | Judged precision, recall, faithfulness and hallucination measures | No judged dataset | NOT VERIFIED |

The final full Python run took 68.27 seconds on Windows, Python 3.13.7. A Starlette/httpx TestClient
deprecation warning remains. It is a compatibility warning, not a failed test; dependency migration
should be validated separately. Earlier in this pass, two inconsistent legacy fixtures failed the new
year/date rule. Their dates and years were made consistent, then the entire suite was rerun.

## Reproduce

The retained final aggregate evaluation observed 143 publications and 156 raw observations.
Provenance coverage was 143/143; duplicate provider IDs, foreign-key violations, year/date conflicts
and tracked-run reconciliation failures were zero. Titles were present in 141/143 records, abstracts
in 121/143 and DOI in 139/143. All 143 had publication dates. Raw run lineage covered 60/156
observations; only 6/18 historical runs had quality counters. Source missingness and historical
lineage gaps remain visible rather than being rewritten as complete data.

The final synthetic run completed 20/20 cached requests at five clients with P50/P95/P99
15.39/26.06/27.61 ms and 210.27 requests/s. Both explicit fault assertions passed. These are the
final retained run's values, not an average across trials or a before/after speed comparison.

Install locked development dependencies and Chromium as described in README. From the project root:

```text
python -m ruff check backend tests scripts migrations
python -m pytest -q --cov --cov-report=term-missing --cov-report=xml
python scripts/test_operations.py
node --check frontend/app.js
npm test
npm run test:browser
npm run test:browser:public
python scripts/evaluate.py --database research_navigator.db --benchmark --output evaluation/reports/current.json
```

`python scripts/release_check.py --browser --database research_navigator.db` runs these local gates.
Use a virtual environment. This workspace used its existing `.tools` dependencies and disabled
unrelated globally installed pytest plugins, loading `pytest_cov` explicitly. Workstation paths,
browser binaries and the choice of database are local setup, not application requirements.

Raw evidence remains under ignored `.runtime/portfolio-*` and `evaluation/reports/`. The retained
[aggregate JSON](../evaluation/published/2026-11-26.json) and
[generated report](../evaluation/published/2026-11-26.md) contain counts and synthetic timings,
without publication text, user questions, credentials or private database contents. JSON records
measurement time, environment and a code/config SHA-256 fingerprint; it does not claim a Git commit.

The benchmark uses 20 cached requests at five clients, one synthetic seed, one forced provider-outage
refresh and one invalid request. It uses in-process ASGI transport and disposable SQLite. Its P99 is
based on only 20 observations; timings are not production SLOs or evidence of real provider capacity.
This pass did not remeasure peak memory, sustained load, operational RTO/RPO or dependency advisories.
Time-dependent freshness, source data and machine load make exact numerical reproduction unlikely.

## Remediation against the historical gap register

| Gap | Implemented change | Regression evidence / remaining limit |
|---|---|---|
| G01 backup acceptance | Temporary online snapshot; table-definition, integrity, FK and snapshot-count validation; atomic publication; invalid same-day file replacement | Empty/corrupt files, altered columns and validation/count failures tested; retention stops after failure. Valid same-day files are reused. Independent pre-upgrade backup and off-device recovery still required |
| G02 date constraints | Enforce year/date constraints before serving live, cached and local results | Inclusive year boundaries, unknown dates, upstream noncompliance and legacy bad cache tested |
| G03 ranking cutoff | Rank matching rows in SQL before limiting; deterministic ID ties | Best match after 300 weaker candidates tested for all three sort modes. Keyword scans still limit scale |
| G04 completeness | Shared whitespace and missing-value definition in SQL/API and evaluator | Blank/Unicode whitespace, null and known zero tested; API and evaluator agree |
| G05 consistency | Reject conflicting year/date pairs from cleaned ingestion while retaining raw JSON | Conflicting record retained raw and excluded from canonical storage; no historical data rewritten |
| G06 request metrics | Telemetry wraps policy rejection and application execution | Early admission rejection counted once, correlation returned, active counter preserved |
| G07 logging | Explicit INFO logger with content-free request JSON | Enabled logging and omission of raw URL/query text tested; no persistent log storage added |
| G08 cache metrics | One runtime cache counter at the shared search decision | Standalone miss/hit and bad-cache paths tested; saved chat uses the same path without a second counter |

These are scoped remediations, not closure of every acceptance criterion in the historical audit.
The source registry, classification, scheduled ingestion, model lineage, persistent monitoring,
employee identity, recovery objectives and deployment gates remain incomplete.

## Changed files and concepts

| File or group | Function and concept demonstrated |
|---|---|
| `backend/app/repository.py` | Source consistency, inclusive filtering, identity preservation and SQL ordering before LIMIT |
| `backend/app/quality.py`, `main.py` | One completeness definition shared by Python and database reporting |
| `backend/app/maintenance.py` | Consistent read snapshots, online backup, validation, atomic file replacement and failure-safe retention |
| `backend/app/http_policy.py`, `telemetry.py` | Observe all HTTP outcomes, bounded metric labels and safe structured logging |
| `backend/app/providers.py`, `service.py`, `chats.py` | Context-local runtime ownership and counting a shared cache decision once |
| `scripts/synthetic_provider.py`, `browser_server.py`, `evaluate.py` | Separate reusable synthetic acquisition from reporting; read-only snapshots, reconciliation and reproducible measurement |
| `scripts/release_check.py`, `package.json`, `.github/workflows/ci.yml` | Consistent local/CI browser gates; existing command compatibility |
| `scripts/prepare_release.py`, `tests/test_prepare_release.py` | Explicitly retain reviewed aggregates while excluding private reports/data |
| `tests/test_portfolio_hardening.py`, `tests/test_pipeline_hardening.py`, `tests/test_chats.py` | Regression cases, consistent source fixtures and one cache counter per shared decision |
| README, code map, learning/evaluation/operations guides and dated report addenda | Explain the actual system and distinguish historical observations from current behavior |

## Deployment decision

| Use | Decision | Reason |
|---|---|---|
| Supervised local learning prototype | GO | Passing executed local checks; single worker and verified backups remain required; scientific accuracy is not established |
| Internal multiuser employee service | NO-GO | Anonymous browser isolation is not employee authentication/roles; deployment, PostgreSQL and operations evidence incomplete |
| Enterprise production | NO-GO | Monitoring, recovery objectives, governance, access controls, capacity and AI evaluation remain incomplete |
