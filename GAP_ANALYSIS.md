# Research Navigator — prioritized gap analysis

**Later remediation, November 26:** This register preserves the November 25 findings. See
[current verification](docs/VERIFICATION.md#remediation-against-the-historical-gap-register) for
scoped fixes to G01–G08, regression evidence and remaining limits. Public-demo browser ownership
was also added later; it does not provide employee authentication or roles. Historical FAIL rows
below describe the audit snapshot, not an assertion that every defect remains present.

**Audit:** 2026-11-25. No application fixes were made. Priorities reflect impact and deployment
scope, not a cosmetic code-quality score. **P0** blocks the named deployment; **P1** can undermine
recovery or requested results; **P2** weakens correctness, operability or scale; **P3** is a deferred
capability or hardening improvement. Evidence IDs resolve through the
[audit index](evaluation/audits/2026-11-25/README.md).

## Priority register

| ID | Priority / scope | Status | Finding | Evidence |
|---|---|---|---|---|
| G00 | P0, shared deployment | FAIL | Authentication, authorization and user ownership absent | Models/routes/config inspection |
| G01 | P1, local/shared recovery | FAIL | Empty existing SQLite file accepted as daily backup | E06 |
| G02 | P1, result correctness | FAIL | Year-only source/date constraints not revalidated | E06 |
| G03 | P2, retrieval correctness | FAIL | Relevance ranking follows unordered 250-candidate cutoff | E06 |
| G04 | P2, KPI correctness | FAIL | Quality API counts blank strings as complete | E06 |
| G05 | P2, data consistency | FAIL | Year/date disagreement accepted by validator | E06; none observed in real cohort E05 |
| G06 | P2, observability | FAIL | Policy/admission rejections missing from metrics | E07 |
| G07 | P2, observability | FAIL | Structured INFO logs disabled by default logger configuration | E07 |
| G08 | P2, observability | PARTIAL | Cache metrics differ between saved chat and standalone search | E07 |
| G09 | P1 before scientific-quality claims | NOT VERIFIED | No labeled retrieval/faithfulness/hallucination evaluation | Unfilled rubric; E10 |
| G10 | P1 before shared release | NOT VERIFIED | PostgreSQL, Docker and remote CI not executed | E01/E12 |
| G11 | P1 before recovery guarantees | NOT VERIFIED | No approved/measured RPO, operational RTO or off-device drill | E09; backup code |
| G12 | P2, governance | PARTIAL | Incomplete historical run lineage and model/transform traceability | E04; schemas/models |
| G13 | P2, growth/scale | PARTIAL | Scan-based retrieval and no sustained capacity evidence | E05/E08 |
| G14 | P2, data lifecycle | PARTIAL | Unreferenced authors and indefinite raw-record growth | E05; retention code |
| G15 | P2 before operated shared service | FAIL | No persistent monitoring, distributed tracing or alert delivery | Metrics/runtime inspection |
| G16 | P2, governance | PARTIAL | Licensing inventory and source-policy review incomplete | Source registry/raw schema |
| G17 | P3, optional ingestion expansion | PARTIAL | No cursor paging, checkpointed sync or incremental scheduler | OpenAlex adapter/service |
| G18 | P3, release hardening | PARTIAL | Pinning is not full artifact provenance or cross-platform validation | Locks/CI/Docker inspection |
| G19 | P3, migration maintainability | PARTIAL | Historical revisions depend on mutable application helpers | Migration inspection |

No P0 exploit was demonstrated for the supported loopback-only personal deployment. G00 is an explicit
release blocker for changing that scope, not a claim that missing enterprise authentication is a hidden
bug in a deliberately personal app.

## Confirmed defects and acceptance criteria

### G01 — Managed daily backup can be empty

**Root cause:** [daily_backup](backend/app/maintenance.py) skips copying if today's filename exists,
then checks only `PRAGMA quick_check`. An empty file is a structurally valid zero-table SQLite database.
The probe had 301 source publications and zero backup tables, yet backup acceptance returned normally.
Potential retention of this false recovery point could remove older useful copies; actual research
loss was not observed.

**Recommendation:** Copy into a unique temporary destination, verify application schema/version and
content against a consistent snapshot, then publish atomically. Do not treat filename existence as
completion. Keep retention dependent on a verified replacement. Ensure pre-upgrade backups are fresh
even if an earlier same-day backup exists. Keep an independently restored off-device copy when needed.

**Acceptance:** Empty, truncated, unrelated and interrupted backup files are rejected; no retention
runs after failure; a same-day upgrade has an explicit fresh recovery point; a valid online backup
restores with schema, count, FK and readiness checks. All tests use separate files.

### G02 — Year-only filters trust upstream compliance

**Root cause:** [matches_dates](backend/app/repository.py) returns true when explicit date fields are
absent, ignoring `year_from/year_to`. The adapter sends the correct provider filter, but the service
does not independently enforce it during ingestion/cache coverage checks. An out-of-contract provider
returned a 1900 work for a since-2022 request and the app returned it.

**Recommendation:** Apply one defined effective-bound rule to fresh, cached and local results, using
the existing validated year/date intersection. Preserve missing dates rather than inventing a day.
Keep rejected/excluded raw evidence and accurate accounting.

**Acceptance:** Lower/upper/year-only/mixed bounds, missing fields, leap dates, equality boundaries,
refresh and cache reuse are checked; the 1900 fixture cannot satisfy since-2022. Counters reconcile.

### G03 — Relevance cutoff hides the strongest match

**Root cause:** [local_search](backend/app/repository.py) takes 250 SQL candidates without relevance
ordering, then ranks in Python. In the 301-row fixture, a row matching all query terms and with a
higher citation count was never eligible.

**Recommendation:** Define and test deterministic relevance at the database candidate-selection
stage, using a ranked query/full-text strategy appropriate to the supported database. Document
fallback ranking and ties. Avoid claiming semantic retrieval without implementing/evaluating it.

**Acceptance:** Strong matches beyond the original cutoff remain eligible; equal-score ordering is
deterministic; relevance/date/citation ordering agrees across SQLite and PostgreSQL fixtures. Measure
large-corpus query plans and latency before selecting a production index design.

### G04/G05 — Completeness and consistency have different definitions

**Root cause:** [quality](backend/app/main.py) counts non-null strings as present, while the evaluator
recognizes empty values as missing. The raw validator checks individual year/date validity without
checking their agreement. These are two different defects, even though no current real record had
a date/year conflict.

**Recommendation:** Define missingness per field, including whitespace-only strings. Make API and
audit share that definition. Define an explicit treatment for conflicting source fields, retain raw
values, and surface uncertainty rather than silently selecting a year.

**Acceptance:** Empty/null/whitespace/known-zero cases produce consistent metrics; optional missing
values remain unknown; a conflicting year/date fixture is flagged with documented handling. Do not
force optional source completeness to 100% by inventing values.

### G06/G07/G08 — Operational evidence omits important traffic

**Root cause:** [LocalPolicy](backend/app/http_policy.py) returns policy/admission rejections before
the metric finalizer. Default Uvicorn logging config does not enable this application's INFO logger.
Cache counters are recorded in the chat emitter, while standalone search does not pass that emitter.
The probe produced 5 HTTP 200 and 15 HTTP 429 responses, no exported 429 lines, no standalone cache
lines, and INFO logging disabled.

**Recommendation:** Observe the full request lifecycle, including early rejection, without reading
or buffering whole SSE responses. Configure a content-safe application logger explicitly. Emit cache
decisions at a shared service boundary, and distinguish HTTP response status from terminal SSE outcome.

**Acceptance:** Every rejection has appropriate bounded-label metrics and correlation; both search
interfaces report equivalent decisions; default launch emits a safe structured request record; keys,
questions and abstracts stay absent; SSE streams increment terminal outcomes without blocking delivery.

## Missing capabilities and release gates

**G00 — Identity and ownership.** There are no principals or per-user access checks. Before sharing,
design authentication, deny-by-default authorization, conversation ownership, principal-scoped quotas,
privacy/deletion rules and audit events. Acceptance includes cross-user ID guessing, read/write/delete,
stream replay and pagination isolation tests. Local Host/Origin validation is not an ownership model.

**G09 — Scientific evaluation.** Mechanical quote membership is useful but not entailment. Define K,
real tasks, relevance judgments, a reference universe if making recall claims, adjudicated source
support labels and thresholds. Record unsupported claims/reviewed claims. A quote-match test, citation
count, OpenAlex total or LLM judge cannot replace these denominators. Leave scores unreported until
the evaluation actually runs. Start from [the existing rubric](evaluation/HUMAN_REVIEW.md).

**G10 — Executable release evidence.** Docker daemon access was denied; PostgreSQL checks skipped;
there is no remote CI run. Run isolated PostgreSQL migration/concurrent-upsert and restore checks,
container nonroot/readiness/persistence checks, then the configured interpreter/browser/security
matrix. Preserve logs, tool/image versions and artifact identities. No configuration-only PASS.

**G11 — Recovery objectives.** Daily local copies are not a verified 24-hour RPO, and a 199.42 ms
workstation drill is not operational RTO. Choose objectives appropriate to the intended users, then
test media availability, restoration, service switching, detection and acceptable data loss. Keep a
protected off-device copy if workstation loss is in scope. Resolve G01 before trusting rotation.

**G12 — Lineage.** Only 30/126 raw records have a run link; 12/15 ingestion rows have no new counters.
Preserve that uncertainty. For future events, persist effective request/filter scope, source response
metadata and transformation/model/prompt versions with outcomes. Test trace reconstruction without
logging secrets or pretending older lineage is known.

**G13 — Scale.** EXPLAIN shows scans and a temporary sort. The largest measured load here was 100
attempts at ten clients; 95 were rejected. Define intended corpus size/concurrency/SLO, then test
socket-level traffic, cold/warm cache, provider delays, write contention, long history and a memory
soak. Report successful throughput separately from fast rejections. Query optimization follows the
measured workload, not a speculative migration to a distributed stack.

**G14 — Entity lifecycle.** Ordered link replacement can leave authors unreferenced; unknown IDs can
create new author entities. Define retention/archive/garbage-collection rules and prove they retain
necessary attribution/raw evidence. Do not deduplicate authors only by names. Measure long-run raw
growth before adding limits that would silently remove provenance.

**G15 — Operability.** Metrics are in-memory and restart-local; no scrape store, alerts or delivery tests
exist. Add durable operational measurements, agreed thresholds/runbooks and tested alert delivery for
availability, provider failures, stale ingestion and failed backups when running an operated service.
Correlation IDs and saved SSE steps are not distributed tracing or paging infrastructure.

**G16 — Source policy.** Document the integrated endpoint contract, change review date, per-second
and daily limits, metadata license and rights of linked publication content. Preserve supplied location
license information and attribution requirements where applicable. No PDF ingest is implemented or
authorized by this audit; adding sources requires a separate review.

**G17 — Optional full ingestion.** One-page previews and query refresh are legitimate for this
prototype. If full synchronization is needed, implement cursor traversal, durable checkpoints,
watermarks/deletion/change semantics and restart tests before claiming full corpus coverage.

**G18 — Supply chain.** Exact version pins and zero reported CVEs do not give immutable base images,
hashed Python wheels, signed releases or SBOM/attestation. Add controls appropriate to the release
environment, validate Python 3.11–3.13/OS combinations, and retain artifact provenance. No deployment
or public release is requested by this audit.

**G19 — Migration independence.** Historical migrations import current application validation helpers.
A future validator change could alter old backfill behavior. Freeze the necessary migration logic
or version its dependencies; prove fresh/repeated/legacy/failed upgrades are stable. This is a code-
inspection maintainability risk, not an observed failure of the current migration tests.

## Work order and closure

For the supported local app, address G01 and G02 first, then G03–G08 with meaningful regression tests.
Use a tested manual backup in the meantime. Scientific-quality claims require G09 regardless of UI
quality. Shared-service work begins with G00 and deployment/recovery gates, not by increasing worker
count or removing the local Host policy. Re-run affected probes and preserve before/after evidence;
passing the original suite alone does not close a gap.
