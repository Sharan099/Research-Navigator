# Research Navigator — engineering scorecard

**Current follow-up, November 26:** [Verification](docs/VERIFICATION.md) contains newer executed
checks and [role alignment](docs/ROLE_ALIGNMENT.md) compares the implementation with the working-student
JD. This scorecard retains the original November 25 measurements and their limitations.

**Evidence date:** 2026-11-25. Personal single-process prototype, version 0.2.0. No application
fixes were made during this audit. This scorecard avoids an invented aggregate readiness percentage.

| Area | Verified achievement | Result / limit |
|---|---|---|
| Data acquisition | Backend OpenAlex adapter with validated intent, bounded preview and safe fallback | One anonymous live OpenAlex request succeeded in 605.5 ms; no fresh Gemini test |
| Identity and provenance | Provider-ID upserts, raw source retention and retrieval timestamps | 116 publications, 126 raw observations; no duplicate canonical IDs; work-level provenance 100% |
| Data quality | Missing optional source values are measured and retained | 21 missing abstracts, 3 missing DOIs; no observed date/year conflicts or negative counts |
| Reconciliation | Raw-entry and unique-usable accounting checked independently | Three tracked runs, 30 records, zero reconciliation violations; older run counters unknown |
| Database safety | SQLite FK enforcement, transaction/migration rollback and idempotent chat commands tested | Integrity/FK checks passed; PostgreSQL runtime remains unverified |
| Recovery | Separate manual online backup/restore and readiness check | All table counts matched; restore 53.71 ms; restore-through-ready 199.42 ms on one workstation |
| Regression | Offline success, edge, failure and concurrency tests executed | 224 Python tests passed; two PostgreSQL skips; 93.18% combined line/branch coverage |
| UI behavior | Saved topics, streaming, filters, citation disclosure and recovery tested | 33 Node tests and four Chromium desktop/mobile tests passed, including axe assertions |
| Dependency hygiene | Locked runtime/development dependencies and registry audits | 27 Python packages audited; zero known Python/npm vulnerabilities at audit time |
| Architecture navigation | Code map plus local Graphify AST analysis | 52 code files indexed; 614 stored graph nodes and 2,183 edges; not a correctness score |

## Performance, with the workload attached

The existing synthetic in-process cache benchmark completed **20/20 requests at five clients** with
P50/P95/P99 **35.46/41.10/41.10 ms** and **113.85 successful requests/s**. It does not measure network
latency, real provider capacity or production throughput.

A separate tracemalloc-instrumented ASGI workload produced:

| Clients | Successful/attempted | Rejected 429 | P50/P95/P99 ms for successful requests | Successful requests/s |
|---|---:|---:|---|---:|
| 1 | 100/100 | 0 | 21.74 / 26.66 / 27.44 | 44.39 |
| 5 | 100/100 | 0 | 92.38 / 162.58 / 188.31 | 47.03 |
| 10 | 5/100 | 95 | 213.18 / 218.50 / 218.50 | 22.11 |

The ten-client result demonstrates bounded admission, not support for ten concurrent successful
research requests. The cache hit rate is 100% **among successful warmed synthetic requests**, not
an observed production cache ratio. RSS samples were approximately 83.0–88.8 MiB; this is not a peak
or a memory-leak evaluation. No latency/memory SLO or before/after speedup is established.

## Defects the audit exposed

- Managed daily backup accepted an empty zero-table SQLite file as a recovery copy.
- A year-only query returned a provider record outside its requested range.
- Local relevance ranking discarded a strongest-match fixture before ranking the candidates.
- The quality API counted blank strings as present; year/date conflict validation was incomplete.
- Early rejected requests were missing from metrics; default application INFO logging was disabled.
- Standalone cache metrics and author lifecycle need work; 39 unreferenced authors were observed.

These are reproduced findings, not fixes. The original regression suite passing did not establish
that the implementation was complete. See [root causes and closure criteria](GAP_ANALYSIS.md).

## Claims suitable for an interview

> I built a local research navigator with a relational raw/cleaned data model, provider-identity
> upserts, bounded query planning, durable chat activity and tested failure fallbacks. I measured
> data quality, cache behavior, concurrent admission and manual recovery using isolated fixtures.
> The audit found correctness and backup defects despite 93.18% coverage, which showed why coverage
> and a working demo are not enough to establish production readiness.

Use the actual denominators and limitations when discussing these results. Do not claim full OpenAlex
ingestion, PostgreSQL production validation, zero hallucinations, measured Recall@K, enterprise
authentication, guaranteed RTO/RPO, successful remote CI, or unlimited concurrent users.

## Deployment decision

| Intended use | Decision |
|---|---|
| Supervised personal learning, loopback-only, one worker, independently verified manual backups | **GO for that limited purpose** |
| Internal multiuser service | **NO-GO** |
| Enterprise production | **NO-GO** |

Evidence and methods: [readiness report](PRODUCTION_READINESS_REPORT.md),
[data quality report](DATA_QUALITY_REPORT.md), [audit bundle](evaluation/audits/2026-11-25/README.md),
and [beginner learning guide](LEARNING_GUIDE.md).
