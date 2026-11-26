# Learning guide: building a reliable research pipeline

## Publishing source and a verified container

Git stores source text and history; GHCR stores the Docker image. `.gitignore` excludes private data
and generated artifacts from Git. `.dockerignore` controls what the image builder can receive. Its
secret/database exclusions must come after directory inclusion rules, so nested files stay excluded.
Neither ignore file removes credentials already published in Git history.

The CI container job builds an image, checks readiness and its nonroot user, then saves that image.
The publication job loads the saved artifact, tags it with the source commit and `latest`, and pushes
it to GHCR. Publishing the tested artifact avoids a second build selecting different base-image bytes.
These configured jobs still require an actual successful remote run to establish image availability.

`scripts/diagnose_groq.py` separates metadata preflight from live generation. `--live` permits at most
two attempts through real app routes, using the shared verifier and an isolated research copy.
It charges the durable usage ledger and never resets limits. A listed model proves metadata access,
not successful planning or citation validity. A shared 3/3 AI-turn allowance stops both Gemini and
Groq even when Groq has requests left. Mocked tests prove those contracts separately from live access.
See [the GitHub guide](docs/GITHUB.md) for source selection, commands and current external blockers.

## November 26: correctness and evidence

The current [verification record](docs/VERIFICATION.md) supersedes historical defect descriptions
only where remediation is explicitly documented. The older audit examples remain dated lessons.

**One rule, two execution environments.** `quality.py:is_present` checks a Python value;
`present_condition` builds a SQL expression for the same definition. Null and whitespace-only text
are missing; a citation count of zero is known. Sharing the definition stops the API and report from
disagreeing. `test_completeness_sql_and_python_agree` checks null, blank, Unicode whitespace and text.

**Filter before serving.** A provider can return a record outside the requested years. In
`repository.py:matches_dates`, the publication date supplies its year when known; otherwise a known
publication year can satisfy year-only constraints. Explicit day ranges require an actual date.
`local_search` applies equivalent SQL predicates. Tests cover fresh, cached and local paths. No day
is invented from a year. A conflicting source year/date is retained raw but rejected from cleaned storage.

**Order before limit.** `local_search` builds a SQL CASE score from title token matches. The database
orders all matching rows, then applies LIMIT. Cutting to 250 rows first could hide the best match.
An ID tie-breaker makes repeated results stable. The regression inserts 300 weaker synthetic records
before the best one and checks relevance, newest and most-cited sorting. This remains keyword search.

**Publish a backup only after validation.** `maintenance.py:daily_backup` holds a read snapshot,
copies through SQLite's online backup API into a temporary file, and checks schema, integrity,
foreign keys and row counts. `Path.replace` publishes it atomically on the same filesystem. Failure
cleans the temporary file and stops retention. An existing valid same-day backup is reused. These
properties do not establish off-device recovery or a guaranteed recovery time.

**Observe rejected work too.** `http_policy.py:LocalPolicy.__call__` surrounds both policy decisions
and application execution with `try/finally`. It records status, duration and a safe request ID even
when admission returns 429. Metrics use route templates rather than user text. `providers.py` records
each cache decision in the request's ContextVar runtime; saved chats no longer count it a second time.

**Fixtures are adapters, not facts.** `scripts/synthetic_provider.py` supplies clearly labeled offline
records to both browser tests and benchmarks. `evaluate.py` reads real local data through a read-only
transaction and measures synthetic traffic separately. Reports store aggregates, environment, time
and a source fingerprint. A small cached benchmark cannot prove real provider latency or scientific
quality. Use [the file-by-file explanation](docs/VERIFICATION.md#changed-files-and-concepts) to find
the code and corresponding regression tests.

### Gemini-to-Groq quota fallback

`llm.py` adapts the same planning/evidence prompt to Gemini or Groq. Gemini is attempted first.
A Gemini HTTP 429 or provider-specific local quota failure permits switching to Groq. Missing
Gemini configuration also permits Groq as primary. Shared AI-turn exhaustion stops both providers.
Since November 26 any Gemini failure (quota, timeout, 5xx, auth, bad output) hands the turn to Groq,
and the warning records the Gemini failure code. Groq-side citation failures still fall back to evidence only.
`providers.py` charges each actual provider attempt and charges the shared AI turn only once when
a request crosses providers. Its SQL transaction rolls back an attempted charge if a limit fails.
No database migration is needed: the existing usage table stores a new `groq` resource row.

Groq's JSON object mode gives a JSON response, not a guarantee of schema correctness or truth.
Pydantic still validates planning and claim structures. Answers still check each cited work and
literal quote against backend-loaded abstracts. Modes include `groq` so the UI does not label it
as Gemini. `test_llm_fallback.py` uses synthetic HTTP responses to verify switching, shared quotas,
headers, model output, failure handling and citations. Offline/browser helpers explicitly disable
Groq too; historical frozen audit helpers require `GROQ_API_KEY` to be explicitly empty when rerun.

`.gitignore` protects ordinary staging, while `scripts/prepare_release.py` additionally copies only
allowed source files and scans actual configured secret values. Neither removes old Git history.
See [GitHub steps](docs/GITHUB.md). Provider contracts follow
[Groq OpenAI compatibility](https://console.groq.com/docs/openai) and
[JSON output documentation](https://console.groq.com/docs/structured-outputs).

| Changed file | Responsibility and concept |
|---|---|
| `backend/app/config.py` | Read and validate Groq settings; secrets stay out of object representations |
| `backend/app/llm.py` | Adapt provider contracts, switch on any Gemini failure, walk the Groq model list |
| `backend/app/providers.py` | Charge provider attempts and one shared AI turn transactionally |
| `backend/app/gemini.py` | Produce safe provider-specific failures without echoing raw exceptions |
| `backend/app/interpreter.py` | Validate planning JSON from either provider |
| `backend/app/answers.py` | Validate generated claims and source quotes from either provider |
| `backend/app/schemas.py` | Extend response enums with the truthful provider name `groq` |
| `backend/app/main.py` | Report whether either provider is configured; configuration is not availability |
| `frontend/app.js` | Display Groq planning and its separate usage budget |
| `.env.example`, `compose.yaml` | Document and pass backend-only Groq configuration |
| `.gitignore`, `scripts/prepare_release.py` | Exclude private data and scan environment/file credential values |
| Offline scripts and `tests/conftest.py` | Explicitly disable real Groq access in fixtures and Gemini-only diagnostics |
| `tests/test_llm_fallback.py`, frontend tests | Verify routing, shared caps, failures, citations and provider labels |

### Public demo and container release

`public_access.py` signs a random browser cookie with HMAC. The browser keeps the token in an
HttpOnly secure cookie; the database stores only an identity hash on each conversation.
The middleware checks that hash before reads, changes, pagination, replay or SSE admission.
Migration `0003` adds an indexed nullable owner field. Historical local conversations stay unowned
and cannot be accessed publicly. This is anonymous ownership, not human identity verification.
`POST /api/session` bootstraps the cookie before frontend topic loading. A blocked-cookie browser
gets a direct-link recovery path rather than a shared workspace. Hosts and origins remain explicit.

The Dockerfile builds dependencies in one stage and copies the environment into a nonroot runtime.
The build context allows only application inputs, so local databases and keys cannot enter layers.
Local named volumes retain SQLite; temporary hosted storage does not. Tests use separate databases.
`test_public_access.py` checks ownership and cookie failures; browser tests exercise two visitors.
See [release status](docs/DEPLOYMENT.md) for the difference between a passing local check,
a built container, a published image, and a live deployment. No optimization speedup is claimed.

### Gemini failure diagnosis

`python -m scripts.diagnose_gemini` checks model discovery and durable usage. Add `--live`
only when generation is intended. The command caps generation at four attempts, disables retries,
and charges the real usage ledger while isolating research operations in a temporary database copy.
Synthetic tests verify both HTTP routes, limits, authentication/rate-limit stop conditions,
and secret redaction. The live report uses `generation_verified: false` until both planning and a
cited answer succeed. Provider listing access is different from successful generation access.
Transport traces keep only event names and elapsed seconds, never sensitive trace information.
On 2026-11-26 the live command correctly stopped before generation at the 3/3 AI-turn limit;
the record is `.runtime/gemini-diagnostic.json`. Daily budgets reset by UTC date. No account
model switch or application timeout change is justified yet by successful generation measurements.

`backend/app/gemini.py` reads Gemini public response parts and classifies failures using fixed,
safe messages. `answers.py` sends the failure code and optional HTTP status to the activity timeline;
`interpreter.py` includes the same category when planning falls back to rules. Warning logs contain
only the operation, category and status, never raw provider errors, URLs, keys or source text.
Timeout, account rate limits, local daily budgets, invalid JSON/schema, blocked/incomplete generation,
and citation mismatch are different failures. A configured key or successful health route proves
configuration, not successful generation. Public answer parts are joined; thought parts are excluded.
Exact citation checks remain mandatory. `tests/test_gemini.py` uses synthetic envelopes and sensitive
sentinels; topic integration tests verify fallback reasons and rejection of unsupported citations.

On 2026-11-26 a live, single-attempt answer diagnostic with stored abstracts returned
`provider_timeout` after 45.52 seconds. DNS and an unauthenticated Google endpoint request worked.
This verifies the observed timeout and honest fallback, not its upstream cause or successful Gemini
generation. Increasing a timeout does not repair an unavailable provider. Personal database usage
and citation rules were not reset or relaxed by this code change.

This project is an example of data engineering with bounded AI assistance. Start with the
[architecture](README.md#current-architecture), then follow one question through the modules.

The **2026-11-25 audit** found defects as well as working controls. Read the
[scorecard](docs/audits/2026-11-25/ENGINEERING_SCORECARD.md), [full audit](docs/audits/2026-11-25/PRODUCTION_READINESS_REPORT.md), and
[gap analysis](docs/audits/2026-11-25/GAP_ANALYSIS.md). The explanations below describe responsibilities; they do not
mean every implementation or failure case has passed. In particular, the managed daily backup,
year-only filtering, relevance cutoff, completeness KPI and operational telemetry have known gaps.

## 1. Trace a request

The browser sends a small command: a question, request UUID, selected paper IDs, and explicit
controls. FastAPI validates the request. For a saved chat, the server loads recent messages and
structured topic state from SQL. It resolves paper numbers against the displayed result order,
then chooses a specific research operation.

A search follows this outline:

```python
# Conceptual outline, not a second implementation.
intent = validate(planner_output)
preview = read_complete_fresh_cache(intent)
if preview is None:
    raw_records = request_source_with_limits(intent)
    preview = validate_and_store_transaction(raw_records, intent)
return metadata_with_provenance(preview)
```

Each function has a distinct responsibility. The planner proposes parameters; it never supplies
SQL or executable Python. The repository owns data correctness. The service owns cache/retrieval
decisions. The HTTP runtime owns external budgets and failures.

**Exercise:** Find `search_owned`, `fingerprint`, and `save_openalex_work` with `rg`. Explain why
question wording and a model's rationale should not identify a cache entry.

## 2. Preserve evidence before interpreting it

OpenAlex returns source JSON. Retaining it lets you investigate a later cleaning mistake without
requesting the same record again. Raw records preserve provider IDs, retrieval time, and even
malformed entries. New records also identify their ingestion run.

Cleaned records use OpenAlex work identity. Two equal titles can describe different works; two
source versions of one ID can describe the same work. Neither title similarity nor a shared DOI
silently merges different provider IDs. Author order is represented by `publication_authors.position`.

Missing values carry information. An unknown citation count is `None`, while a verified zero is `0`.
The UI displays unknown counts honestly. Missing publication dates cannot match precise dates;
missing abstracts cannot support an explanation. Never fill these gaps with invented metadata.

**Exercise:** Run the null-count tests. Explain why converting missing counts to zero changes both
sorting and the user's understanding of a paper.

## 3. Reconcile each ingestion

A pipeline can return plausible results while silently dropping records. Counters expose that:

```text
received = rejected + valid
valid = repeated IDs + excluded unique IDs + usable unique IDs
raw records retained = received
```

Repeated IDs are counted inside one response, not across the entire raw-history table. Repeated
raw history across refreshes is expected. Excluded dates apply to unique, valid records. The latest
source snapshot for a repeated ID determines whether that record is eligible for the preview.

The pipeline stores raw records, cleaned upserts, ingestion counters, and cache decisions together.
Incomplete previews are explicitly labeled and not cached as complete. A provider outage produces
an honest partial-local fallback; it does not prove that no matching papers exist elsewhere.

**Exercise:** Inspect `test_ingestion_accounting_reconciles_raw_and_unique_usable`. Identify the raw
entry, identity, and filter stages rather than treating every counter as interchangeable.

## 4. Understand transactions and concurrency

A select-then-insert can race: two requests both see that a work is absent and both attempt to
create it. Native `INSERT ... ON CONFLICT` lets the database enforce identity while updating one
row. Publication writes also protect replacement of ordered author links.

Database operations run in worker threads because the current SQLAlchemy drivers are synchronous.
A session is created, used, and closed within one operation's thread. Returning Pydantic values
instead of live ORM objects prevents accidental lazy database access on the async event loop.
Provider HTTP calls remain asynchronous. Transient lock/serialization failures retry the complete
transaction with a fresh session; malformed input is not retried as if it were a lock problem.

The local app permits one server process. File/advisory locks enforce that rule. A topic claim
prevents competing turns from overwriting its memory. Heartbeats and stale recovery handle work
that could not finish; they are not a claim of distributed-worker support.

**Exercise:** Read the SQLite concurrency test and the optional PostgreSQL test. Explain why
compiling PostgreSQL DDL cannot prove that PostgreSQL transactions work.

## 5. Bound external requests and cost

`providers.py` shares an HTTP client, caps response bytes and concurrent requests, and counts each
attempt against a persistent UTC-day budget. Retries have a maximum and respect Retry-After; a
long wait stops the operation instead of retrying early. Authentication and malformed response
failures are not transient retries. An ambiguous Gemini timeout is not automatically retried
because the original generation may already have run.

Daily limits distinguish AI-assisted turns from actual Gemini attempts. Local cache and metadata
operations do not consume provider attempts. When a limit is exhausted, the system can still use
rules, local records, or evidence excerpts. Token counts are recorded only when supplied by the
provider; they are not estimated from text length or presented as a billing guarantee.

**Exercise:** Set a zero provider budget in an offline test. Verify that a cached result still works
and that no external request is made.

## 6. Keep conversation memory separate from evidence

The database stores saved messages, effective filters, ordered results, and paper references.
Only eight bounded context entries are sent to the planner. Indexed references allow a title or
OpenAlex ID to find an older paper without loading every old abstract into a prompt.

Evidence comes from backend-loaded source abstracts, never from prior assistant prose. The answerer
limits itself to five available abstracts and 4,000 characters per source. Each generated citation
must identify a supplied source and contain a matching excerpt. Invalid output is discarded in
favor of real excerpts. These checks do not prove that a paraphrased claim follows from the quote;
that needs the separate review rubric.

**Exercise:** Explain why a valid quotation can still support an overconfident interpretation. Read
[evaluation/HUMAN_REVIEW.md](evaluation/HUMAN_REVIEW.md) before calling an answer accurate.

## 7. Stream actual observations

`StreamingResponse` sends server-sent event frames while work is running. A queue connects real
stage callbacks to the HTTP response. Each event is also stored as its own `chat_events` row.
The frontend buffers incomplete frames and uses `TextDecoder` for multibyte text. It verifies
request/conversation IDs before updating the originating topic.

Stable DOM nodes preserve keyboard focus and expanded evidence during progress updates. Concise
live announcements avoid rereading the entire transcript. Browser storage is only a convenience;
denied storage cannot replace or break authoritative SQL memory. Error messages belong beside
the action that failed, including inside dialogs.

**Exercise:** Open an activity step during a request. Verify it stays open after another event.
Then inspect the Node regression test and the real-browser test: what can each establish?

## 8. Migrate, back up, and recover

Alembic records applied schema revisions. Upgrading an old database must preserve records, raw
payloads, and valid known zeros. The SQLite migration uses an explicit transaction even during
batch table rebuilds; an injected failure must restore schema, data, and revision state.

The app makes a managed daily SQLite copy before schema maintenance and retention. Inactive chats
expire after 30 days, while shared research/raw records remain. Managed daily backups expire after
seven days; deleting a chat cannot instantly remove older backup copies. PostgreSQL recovery uses
`pg_dump`/`pg_restore` and explicit operator procedures.

Restore into a separate destination, verify integrity and counts, and only then consider switching
the application's database. A backup file existing is not evidence that it can be restored.

**Exercise:** Use the commands in [operations](docs/OPERATIONS.md) to restore a copy into `.runtime`.
Compare restored counts with the source without modifying the personal database.

## 9. Evaluate what is actually measured

A useful report states the dataset, workload, environment, denominator, and limitations. For this
project, inspect provenance, identity, reconciliation, null rates, freshness, cache correctness,
provider outcomes, retry exhaustion, citation rejection, latency, cost limits, and recovery.

The disposable benchmark uses synthetic records and five clients. It measures local cache behavior
and fault handling, not internet latency, scientific recall, or cloud capacity. A snapshot with old
caches does not establish a cache bug; serving one without validating its age would. Empty
numerators/denominators are not replaced with a fabricated perfect score.

Run `python scripts/evaluate.py --benchmark --output evaluation/reports/current.json`. Compare
compatible workloads only. Read the report's limitations before interpreting its percentiles.

## Tools and changed modules

| Tool or module | Function and concept |
|---|---|
| FastAPI / `main.py` | Validated HTTP routes, startup/shutdown ownership, readiness and local configuration |
| SQLAlchemy / `models.py` | Portable schema, relationships, sessions, transactions, nullable values and native upserts |
| `database.py` | SQLite FK integrity and database-specific single-process locks |
| Alembic / `migrations.py`, root `migrations/` | Versioned schema adoption, data repair and rollback validation |
| `repository.py` | Source cleaning, raw retention, identity upserts, ordered authors and bounded retrieval |
| `service.py` | Complete-cache decisions, provider fallback, reconciliation and owned database phases |
| HTTPX / `providers.py` | Shared async HTTP, quotas, retry policy, deadlines and response bounds |
| Pydantic / `schemas.py` | Structured model output and request validation; a narrow interface to model suggestions |
| `interpreter.py`, `openalex.py` | Validated planning/rule fallback and one source-specific API contract |
| `answers.py` | Bounded evidence and source/quote checks without treating the model as a trusted database |
| `chats.py` | Persistent context, indexed references, claims, idempotency, paging and append-only SSE activity |
| `maintenance.py` | Managed local backups, protected retention and shutdown-safe work |
| `telemetry.py`, `http_policy.py` | Content-free metrics/logs, correlation, local host/origin and request-size policy |
| `frontend/app.js` | Async state ownership, stable DOM, optional storage, stream decoding and error recovery |
| HTML / CSS | Semantic headings, native dialogs, consistent controls, contrast, responsive layout and reduced motion |
| pytest / coverage | Behavior and fault-path tests; coverage identifies untested code rather than proving correctness |
| Node test runner | Fast state-machine regression checks with a small DOM substitute |
| Playwright / axe | Real browser journeys and automated accessibility checks, supplemented by visual review |
| Ruff | Explicit project lint and formatting rules that do not depend on a machine's parent configuration |
| Locks / CI / Docker | Repeatable dependency selection, automated release gates and nonroot local images |
| `scripts/` / `evaluation/` | Repeatable operational commands and measured reports with honest scope |
| `scripts/showcase_server.py` | Copies a verified snapshot into a disposable database; empty keys and zero provider budgets prevent new source calls |
| `scripts/record_showcase.cjs` | Uses Playwright assertions for readiness, deliberate pauses for readable footage, and labeled historical AI output; saves video, screenshots, and a chapter guide |

The [showcase guide](docs/DEMO_STORYBOARD.md) describes recording the interface without changing the personal
database. Replaying a saved answer demonstrates the UI and its stored evidence, not a successful new
API call. The capture manifest records source provenance and the absence of external provider attempts.

All explanations are written around the actual implementation. The main README describes the
current system; this guide explains its reasoning, tests, and limits. Use the code map to avoid
rereading unrelated files when making the next change.

## 10. Learn the concepts through the audit

Evidence IDs below refer to the private audit bundle described in the [archive index](docs/audits/2026-11-25/README.md). Fake providers
check our engineering behavior; they cannot prove a real provider is accurate or available.

| Concept | Meaning and why it matters | Implementation | Verification and limitation |
|---|---|---|---|
| API contract | Agreement about fields, authentication and response shape prevents incompatible input | `openalex.py`, `schemas.py` | E01 mocked success/bad envelopes; E12 one anonymous live GET, not full live ingestion |
| Pagination | A page/cursor retrieves more than a bounded first preview | One 1–20-result page today | No cursor traversal; full corpus acquisition is not implemented |
| Rate limiting | Daily attempts, simultaneous calls and requests per second are different limits | `providers.py` | Quotas/concurrency tested in E01; no per-second scheduler |
| Timeout | Stops a dependency holding work indefinitely | Provider runtime and chat/request deadline | E01 transport and deadline injection tests |
| Retry/backoff | Repeats eligible transient work with bounded delay; unsafe repetition can duplicate cost | `providers.py` | E01 status/Retry-After tests; ambiguous Gemini transport failures not blindly retried |
| ETL/normalization | Extract source records, transform field representations and store usable rows | Service and repository | E01 DOI/abstract/date/null tests; raw evidence retained beside cleaned data |
| Raw/canonical data | Raw is what the provider said; canonical is our current normalized view | Raw/publication tables | E04/E05: 126 raw observations, 116 works; agreement is not independent truth |
| Missingness | Unknown is information, not an invented zero or string | Nullable metadata, UI unknown counts | E01/E02; E06 found blank strings mishandled by quality API |
| Accuracy | A value agrees with reality or an independent authority | Source metadata enables later checks | No publisher/Crossref verification; NOT VERIFIED |
| Completeness | Fraction of a defined population with usable values | Quality API/read-only evaluator | E04/E05: 95/116 abstracts, 113/116 DOIs; source gaps preserved |
| Validity/consistency | Validity checks individual fields; consistency checks related fields agree | Repository validator/schemas | E01 malformed-field tests; E06 accepted a year/date conflict |
| Uniqueness/deduplication | Identity determines whether observations refer to one entity | Provider-ID unique constraints/native upserts | E01 concurrency tests; E05 zero duplicate works; names/titles are not safe merge keys |
| Idempotency | Repeating an operation does not duplicate its intended effect | Work upserts, chat UUID/replay | E01/E02 repeat/reconnect tests; raw history may intentionally grow |
| Reconciliation | Explain where every input went | Service/model counters | E04/E05 equations hold for three measured runs; older counts unknown |
| Incremental ingestion | Process changes since a saved checkpoint | Cache/query refresh only today | No source watermark, deletion/update checkpoint or full sync scheduler |
| Relational normalization | Separate identities and ordered relationships to reduce repeated mutable data | SQLAlchemy models | E01 FK/order tests; E05 found 39 unreferenced authors needing a lifecycle policy |
| ACID transactions | Writes are all-or-nothing, constrained, isolated and committed | Sessions/database transactions | E01 failed-write/migration rollback; no destructive power-loss campaign |
| Index/query plan | An index can avoid scanning/sorting everything; EXPLAIN shows the chosen plan | Identity/reference indexes, local search | E05 scans/temporary sort; tiny-corpus latency is not scale proof |
| Candidate ranking | A ranker cannot return a best match that was excluded before ranking | Local keyword fallback | E06 301-row fixture exposed the unordered 250-candidate cutoff |
| Cache correctness | Reuse is safe only when key, coverage, constraints and age agree | Fingerprint/TTL/preview checks | E01 cache faults; E06 year-only gap; identity coverage alone is insufficient |
| Migration | A versioned change moves older schema/data forward | Alembic/SQLite transactional upgrades | E01 preservation/rollback; PostgreSQL skipped; mutable historical helpers are a future drift risk |
| Backup/restoration | A copy is useful only if it recovers intended application state | Managed backup/operations scripts | E09 manual restore passed; E06 empty daily backup incorrectly accepted |
| RTO/RPO | RTO is acceptable downtime; RPO is acceptable data-loss age | Objectives not defined | 199.42 ms local restore-through-ready is not guaranteed RTO; RPO unmeasured |
| Provenance/lineage | Provenance identifies origin; lineage connects transformations and runs | Raw IDs/times, run FK, chat events | Work provenance 116/116; run linkage 30/126; full model/transform history incomplete |
| Licensing/retention | Origin does not establish rights or how long copies should remain | Registry/raw metadata, retention policy | Policy tests pass; rights inventory/off-device recovery not established |
| Throughput/concurrency | Useful completions per second differ from simultaneously attempted work | Admission/provider semaphores | E08 separates completed work from rejection; 95 fast 429s are not useful throughput |
| P50/P95/P99 | Ordered latency percentiles show tails hidden by averages | Audit workload timing | E04/E08 nearest-rank values; saturated case has only five successes |
| RSS/Python heap | RSS is process working set; tracemalloc tracks Python allocations, not all native memory | Audit helper | E08 samples both; no peak/soak/leak guarantee |
| Precision@K/Recall@K | Precision measures relevance of returned items; recall needs a relevant universe | Review rubric only | No labels/universe, so no scores; provider count is not recall ground truth |
| Citation validity/faithfulness | Identity/quote membership differs from whether the claim follows from evidence | Answerer checks | E01 rejects wrong quotations; E10 three historical mechanical matches, no semantic labels |
| Hallucination rate | Unsupported claims / reviewed claims with a defined judgment method | Not measured | Zero current claims is an empty denominator, not zero hallucinations |
| Validation/SQL injection | Constrain input and keep values separate from executable syntax | Pydantic/SQLAlchemy/fixed tools | E01 invalid/adversarial requests; no broad penetration test |
| Authentication/authorization | Identify callers, then enforce which records each may access | Absent in personal app | Shared-service NO-GO; Host/Origin policy is not user ownership |
| Logs/metrics/traces | Events, aggregate measurements and operation relationships answer different questions | HTTP policy/telemetry/chat events | E07 disabled INFO logs/missing rejection/cache metrics; no distributed tracing/alerts |
| Liveness/readiness | Process responsiveness differs from ability to serve dependencies | Health/database-ready routes | E01 injected DB failure; E09 restored app ready |
| CI/CD/reproducibility | Repeatable checks and controlled artifacts reduce release surprises | Locks/workflow/nonroot image source | E11 advisory audits pass; remote CI/container/PostgreSQL execution unverified |
| UI/KPI correctness | A clear display still needs accurate denominators/filter semantics | Frontend/quality API | E02/E03 interactions pass; E06 KPI/retrieval defects |

### A valid SQLite file can be a useless backup

The managed daily backup checks `PRAGMA quick_check`. That establishes SQLite structural validity,
but an empty database can pass it. The isolated probe had 301 source publications and a zero-table
backup, yet backup acceptance returned normally. The separate manual restore drill passed. These
facts describe different paths; one does not cancel the other. See [G01](docs/audits/2026-11-25/GAP_ANALYSIS.md) before
relying on automatic recovery. No fix was made during the audit.

### A passing suite is not a complete specification

The suite passed 224 tests with 93.18% combined line/branch coverage. Additional probes still found
year filtering, ranking, backup, consistency and KPI problems. Coverage asks which code ran, not
which business invariants were asserted. PostgreSQL DDL compilation also cannot prove transactions
on a real PostgreSQL server; the two runtime checks were skipped.

**Exercise:** For G01–G08, write the expected invariant, the smallest fixture that can break it, and
the assertion that would catch it. Do that before changing implementation. A future regression test
should assert corrected behavior, not that the known bug remains reproducible forever.

## 11. Interpret the measured results correctly

At capture time, the current database held 116 papers and zero stored AI claims. The older snapshot
held 97 papers and three claims. Mixing populations would create a misleading score. Abstract
completeness is `95 / 116 = 81.90%`; this says nothing about papers outside the subset. A provider
omitting an abstract is different from our quality API incorrectly counting a blank abstract as present.

The instrumented five-client workload completed 100/100 requests with P95 162.58 ms. At ten clients,
five completed and 95 were rejected. Its 442.24 attempts/s mostly counts rejected traffic; useful
throughput was 22.11 successes/s. Its P99 uses only five success samples. The separate 20-request
benchmark had different input/overhead, so the numbers do not establish a speedup or production SLO.

## 12. Reproduce the audit and read the deliverables

Use the [current evaluation commands](evaluation/README.md) for published tooling. The private historical helper creates
temporary databases and never seeds fixtures into the personal catalog. `probes` reports defects,
`load` times synthetic cached requests, and `recovery` reads a source copy and starts the app against
a separate restored target. Unknowns and unavailable environments stay explicit.

| Audit file/tool | Function and lesson |
|---|---|
| `PRODUCTION_READINESS_REPORT.md` | Connects target, actual result, method, status and evidence; separates three deployment decisions |
| `DATA_QUALITY_REPORT.md` | Reports denominators, reconciliation, provenance, freshness and integrity without treating source agreement as truth |
| `GAP_ANALYSIS.md` | Separates reproduced defects, missing capabilities and unverified gates; supplies root causes/acceptance criteria |
| `ENGINEERING_SCORECARD.md` | Summarizes defensible interview claims and unsupported claims |
| `evaluation/audits/2026-11-25/reproduce.py` | Reproduces diagnostic fixtures/load/recovery; audit tooling, not a production pipeline or a replacement for regression tests |
| Logs/JSON/coverage/Graphify/checksums | Preserve observations and navigational context; a checksum is integrity evidence, not a release signature |

Graphify indexed 52 code files locally without LLM calls. It helps find callers; tests and observations
establish behavior. Unsupported formats, documentation and semantic meaning still require direct
inspection. The graph is a navigation aid, not a correctness or scientific-quality score.

The local learning prototype is a limited GO with loopback binding, one worker and verified manual
backups. Internal multiuser and enterprise use are NO-GO. Describing those limits accurately is part
of engineering the system.

## November 26: Groq free models as Gemini fallback

**What changed.** Live checks showed Gemini returning 503s, timeouts and 429s, while the old code only
switched to Groq on quota errors, so a timeout ended the AI turn. The configured default model
`llama-3.3-70b-versatile` also returned 404 for this account. `llm.generate_json` now hands the turn
to Groq on any Gemini failure except the shared AI-turn cap, and `GROQ_MODEL` is an ordered list
(`openai/gpt-oss-120b,openai/gpt-oss-20b`): 404/408/429/5xx moves to the next model, while auth errors
stop immediately because another model would fail the same way.

**Concepts.** Fallback classification (availability failures vs. configuration failures), ordered
degradation, and honest reporting: every switch is a visible warning plus a `provider_fallback`
activity event carrying the Gemini failure code. Validation is unchanged, so Groq output still passes
schema checks and quote-membership checks, and a Groq citation mismatch still yields evidence only.

**Tests.** `tests/test_llm_fallback.py` covers timeout/503/404 switching with reason codes, the Groq
model walk, no-walk on auth errors, the shared cap blocking both providers, and 401/403/500 switching
through the HTTP route.

**Demo.** `scripts/record_live_demo.cjs` records the real UI against a live server; `scripts/build_live_demo.py`
compresses backend waits to produce a 60-second MP4 (`docs/media/research-navigator-demo.mp4`).
