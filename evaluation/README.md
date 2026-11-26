# Pipeline evaluation

Evaluate the current SQLite database without modifying it:

```text
python scripts/evaluate.py --output evaluation/reports/current.json
python scripts/evaluate.py --benchmark --compare evaluation/reports/baseline.json --output evaluation/reports/after.json
```

The second command adds a disposable offline workload: five clients, 20 cached research
requests, one synthetic initial ingestion, an explicit refresh during a synthetic provider
outage, and invalid-input rejection. It never connects to OpenAlex, Gemini or Groq. Synthetic records
are clearly labeled and exist only in a temporary database. This is a small reproducible
engineering benchmark, not a capacity claim about real providers or a multiuser deployment.

Each run produces machine-readable JSON and a Markdown report. Stored-data quality checks
use read-only SQLite connections; benchmark writes are isolated to a temporary directory.
Reports contain aggregate values, not saved questions, source abstracts or credentials.

The read-only audit holds one SQLite read transaction, so its queries share a consistent snapshot.
Quality completeness uses the same whitespace definition as `/api/quality`. Tracked-run reconciliation
is an integrity gate; older rows without counters remain unknown. Year/date conflicts are reported.
Each JSON report includes Python/OS/architecture and a SHA-256 fingerprint of code, tests and selected
runtime configuration files. The fingerprint excludes secrets, databases and reports; it is not a signature.

Keep private run artifacts in ignored `evaluation/reports/` and `evaluation/audits/`. Reviewed aggregate
JSON/Markdown can be retained in `evaluation/published/`, which the clean exporter includes. Reproduce
the current report using the same code snapshot:

```text
python scripts/evaluate.py --database research_navigator.db --benchmark --output evaluation/reports/reproduced.json
```

Input data, workstation load and time-dependent freshness change results. See
[current verification](../docs/VERIFICATION.md) for methods, scope and historical comparison limits.

| Dimension | Measurement | Interpretation / gate |
|---|---|---|
| Identity | Duplicate provider IDs | Zero; titles are not identity keys |
| Provenance | Works linked to raw provider records with retrieval time / works | 100% for nonempty corpus |
| Integrity | SQLite integrity and FK violations | `ok`, zero violations |
| Completeness | Missing DOI, abstract, title and publication date ratios | Report upstream missingness; do not invent data |
| Freshness | Max retrieval age, caches within reference 24h | A snapshot measure; expired cache is valid if never reused |
| Cache coverage | Exact unique expected IDs present / cache entries | Identity coverage only; runtime also validates date bounds and TTL |
| Evidence | Invalid stored citation quotes, IDs or uncited claims | Zero; exact quotes do not prove semantic entailment |
| Run ownership | Busy topics lacking a running request | Zero |
| Availability | Cached workload errors, explicit outage fallback, invalid input rejection | Zero cached errors; fault scenarios pass |
| Performance | p50/p95/p99/mean ms, requests/s, client count | Compare equivalent environments; no production SLO established |
| Recoverability | Backup/restore duration and table count/integrity checks | Test separate destination; never overwrite source |

An empty denominator is reported as `null`, not a misleading perfect score. Historical runs
without new counters must remain unknown. The local catalog is a partial preview and does not
measure completeness or scientific recall across OpenAlex. True retrieval relevance needs a
curated, labeled query set and human judgments; abstract-answer entailment needs an explicitly
reviewed evaluation set. These cannot honestly be replaced with fabricated scores.

`reports/baseline.json` captures the existing stored-data state before hardening was completed.
`reports/after.json` is the latest measured state and disposable benchmark. Data preservation
may correctly leave quality ratios unchanged. Latency comparisons require matching workload
measurements on both versions; a baseline without benchmark values cannot establish speedup.

The report's integrity exit code fails on duplicate identity, missing provenance, FK problems,
invalid stored citations, orphaned busy topics, benchmark errors or failed fault scenarios.
It does not silently fail merely because a provider omitted an abstract or the cache aged.
