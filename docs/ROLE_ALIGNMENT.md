# Working-student role assessment

Assessment date: November 26, 2026. This is an evidence-based portfolio review, not a prediction
of a hiring decision. See [verification](VERIFICATION.md) for executed checks and limitations.

## Hiring-manager view

Research Navigator is relevant evidence for a working-student interview. It shows an end-to-end
application with real scholarly metadata, SQL persistence, validation, provenance, conversational
exploration and reproducible offline verification. Its scope fits a prototype that a student could
extend under supervision. It does not establish experience operating an internal employee platform.

| JD responsibility | Current evidence | Alignment and remaining work |
|---|---|---|
| Collect, process, validate and store publications | OpenAlex adapter; raw/cleaned separation; provider-ID upserts; ingestion counters | Strong prototype alignment; only one scholarly source and bounded query previews |
| Automated acquisition | Backend requests, bounded retries, timeouts, quotas and cache reuse | Partial: user-triggered acquisition; no scheduler, cursor sync, watermark or checkpoint recovery |
| Reliable backend integration | Transactional persistence, owned database sessions, request idempotency and fallback | Relevant implementation; PostgreSQL execution and deployment remain unverified locally |
| Data cleaning and quality assurance | DOI normalization, abstract reconstruction, shape/date validation, missingness and reconciliation | Relevant deterministic QA; not independent verification of research facts |
| Traceability | Raw observations, provider IDs, retrieval time, ingestion links, saved activity | Partial: historical lineage gaps and no complete model/prompt/transform version ledger |
| Frontend access and exploration | Saved chats, date/sort controls, details, explanation/comparison, activity timeline | Strong exploration prototype; no evidence of colleagues using it daily or usability studies |
| AI-supported content analysis | Schema-constrained planning and quote-checked answers over stored abstracts | Partial: no full-text extraction, corpus classification or judged semantic evaluation |
| Trends and KPI dashboards | Quality endpoint and aggregate evaluation reports | Partial: no research-trend charts, emerging-topic detector or representative field coverage |
| AI-assisted development | Project was developed with coding-agent assistance; regression tests exercise behavior | Directly relevant practice; the candidate must demonstrate understanding and ownership |
| Internal application maintenance | Migration, backup, health, CI and container source exist | Partial: anonymous demo isolation is not employee authentication, authorization or monitoring operations |

There is no defensible percentage match: responsibilities have different weights and the employer's
interview rubric is unknown. Local passing tests are evidence of tested contracts, not every JD outcome.

## Interview claims supported by evidence

- “I built an AI-assisted research exploration prototype using FastAPI, SQLAlchemy and browser JavaScript.”
- “I preserve original source records and separate ingestion validity from missing optional metadata.”
- “I reproduced and fixed filter, ranking, quality-count and backup defects, with isolated regression tests.”
- “I can explain where the LLM participates and why its output must be validated.”

Use “built with AI coding assistance” openly. Be ready to explain a function, alter a validation rule,
write its regression test, follow an HTTP request through storage, and debug a failed provider call.
Do not claim independent authorship, live LLM reliability, scientific accuracy or production operation
without evidence. No employee usage or hiring outcome has been measured.

## Next priorities after this pass

1. Learn and demonstrate ownership of request → validation → transaction → response, then explain failure recovery.
2. Validate PostgreSQL, container startup and remote CI in accessible isolated environments.
3. Build representative, labeled retrieval and answer-evaluation sets before claiming research quality.
4. Add scheduled incremental acquisition with checkpoints and explicit corpus coverage.
5. Add trend dashboards and classification only with documented sampling, denominators and user feedback.

Employee accounts, access policies, persistent monitoring and approved recovery objectives are separate
prerequisites for an internal service. This pass intentionally strengthens the existing prototype.
