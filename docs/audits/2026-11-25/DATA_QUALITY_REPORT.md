# Research Navigator — data quality report

**Later measurement, November 26:** See the [new aggregate evaluation](../../../evaluation/published/2026-11-26.md)
and [remediation record](../../VERIFICATION.md). The denominators and observations below remain the
November 25 population; they must not be mixed with the newer stored subset.

**Captured:** 2026-11-25, 19:04:34 UTC. This report describes the stored local subset at that time.
It does not estimate completeness across OpenAlex or verify publisher facts. The personal SQLite
database was read without cleaning, updating, migrating or deleting its research records.

The private evidence index (see the [archive index](README.md)) explains E01–E15 and how to reproduce
the checks. See the [readiness report](PRODUCTION_READINESS_REPORT.md) for runtime/deployment decisions
and [gap analysis](GAP_ANALYSIS.md) for remediation. PASS/FAIL/PARTIAL/NOT VERIFIED have the same
meaning across the reports.

## Population and provenance

| Measurement | Target | Actual | Method | Status |
|---|---|---:|---|---|
| Canonical publications | Defined population, no invented rows | 116 | Read-only SQL, E04 | PASS |
| Raw source observations | Preserve observations, including repeated IDs | 126 | Read-only SQL, E04/E05 | PASS |
| Raw payloads passing current validator | Validate stored engineering shape | 126/126 (100%) | `_valid_openalex_work` over raw JSON, E05 | PASS |
| Work-level raw provenance | Every canonical ID backed by source identity and retrieval time | 116/116 (100%) | Raw-ID membership and timestamp check, E04 | PASS |
| Raw-to-ingestion-run lineage | Responsible run for each new source observation | 30/126 (23.81%) overall | FK/link count, E04 | PARTIAL |
| Historical unlinked raw observations | Preserve uncertainty rather than invent lineage | 96 | 126 minus 30 linked | PARTIAL |
| Tracked ingestion runs | Stage counts for complete ledger | 3/15 (20%) | Non-null `records_valid`, E04 | PARTIAL |
| Author entities | Measure retained population | 861 | Recovery inventory, E09 | PASS |
| Ordered publication-author links | Preserve attribution order | 843 | Recovery inventory, E09 | PASS |
| Unknown provider author IDs | Preserve unknown identity, do not merge by name | 190/861 (22.07%) | SQL, E05 | PARTIAL |
| Unreferenced author rows | Account for retained orphan entities | 39/861 (4.53%) | Left join to publication authors, E05 | PARTIAL |

Multiple raw versions of one provider ID are expected history, not canonical duplicates. A source-ID
match is a useful provenance check, but it does not prove an exact transformation lineage or the
scientific accuracy of the source. Historical run links are not backfilled because the true run
cannot be established from missing evidence. Unknown author IDs must not be “repaired” by merging
people with the same name.

## Quality dimensions

| Dimension / metric | Target | Actual | Evidence/method | Status |
|---|---|---|---|---|
| Accuracy against external authority | Independent verification of titles, authors, DOI and claims | Not performed | No publisher/Crossref cross-check; source fidelity is not accuracy | NOT VERIFIED |
| Title completeness | Report real missingness | 116/116 present; 0 missing | Nonblank value check, E04/E05 | PASS for observed population |
| Abstract completeness | Report missingness; no fabricated abstracts | 95/116 present (81.90%); 21 missing (18.10%) | E04/E05 | PARTIAL |
| DOI completeness | Report missingness; no invented DOI | 113/116 present (97.41%); 3 missing (2.59%) | E04/E05 | PARTIAL |
| Publication-date completeness | Report missingness; no inferred day | 116/116 present; 0 missing | E04 | PASS for observed population |
| Provider work uniqueness | Zero canonical duplicate IDs | 0/116 | SQL distinct identity count, E04 | PASS |
| DOI duplicate groups | Measure, do not silently merge on DOI | 0 non-null groups with count >1 | SQL, E05 | PASS for observed population |
| FK/integrity consistency | Zero FK violations, valid SQLite structure | 0 violations; integrity `ok` | PRAGMA checks, E04/E09 | PASS |
| Citation-count domain | No negative known values | 0 negative counts | E05 | PASS for observed population |
| Latest-raw count agreement | Stored count agrees with latest valid source observation | 0 mismatches | Compare canonical count to latest valid raw count, E05 | PASS for this field |
| Observed date/year consistency | Matching year where both exist | 0 disagreements | SQL, E05 | PASS for observed population |
| Enforced date/year consistency | Detect conflicting fields before accepting cleaned data | Year 1900 with date 2026-01-01 accepted | Isolated synthetic probe, E06 | FAIL |
| Quality API denominator | Empty or whitespace-only string is not complete data | Blank title/DOI/abstract counted present | Isolated synthetic API probe, E06 | FAIL |
| Timeliness of metadata | Defined freshness objective | Oldest retrieval age 58.61 h; no approved freshness SLO | Age at capture, E04 | PARTIAL |
| Complete research-corpus coverage | Known relevant universe and measured recall | Unknown; local subset and single-page previews only | Architecture/source registry | NOT VERIFIED |

The percentages above use the 116-work denominator or the explicitly identified author/raw denominator.
Optional missing source fields are a coverage limitation, not proof of ingestion corruption. Conversely,
zero observed year/date disagreements cannot establish a validation rule: the fault probe proves that
inconsistent new records can be accepted.

Only citation counts were compared to latest raw values in E05. This is not a claim that every cleaned
field was exhaustively compared, that raw source values are authoritative, or that all unknown counts
are known zeros. Unknown counts remain nullable by design.

## Reconciliation

The pipeline counts raw entries and unique usable IDs separately:

```text
received = rejected + valid
valid = duplicates + excluded_dates + usable
```

For the **three measured runs**, totals were 30 received, 30 valid, 0 rejected, 0 duplicates and
30 usable. Their accounting equations had **zero violations** (E04/E05); the aggregate excluded-date
remainder is therefore zero for those runs. Thirty raw observations have run links. This is a **PASS**
for the tracked cohort, not retrospective proof for the other twelve ingestion rows.

All **15 stored ingestion rows** have status `succeeded`. The stored-ledger proportion is 15/15;
it is **PARTIAL** as a reliability measure because a process failing before a ledger commit or a cache
hit is outside that denominator. There is no measured all-attempt production success rate.

Synthetic mixed-record tests exercise valid, rejected, repeated-ID, out-of-date and partial-preview
cases. They also check that a failed transaction rolls back rather than duplicating raw rows on retry.
Those tests pass (E01), but they do not close the year-only-bound defect discovered by E06.

## Cache and retrieval correctness

| Metric/check | Target | Actual | Method | Status |
|---|---|---|---|---|
| Cache expected-ID coverage | Full expected preview without missing or repeated IDs | 9/9 entries pass identity coverage | Read-only audit, E04 | PASS for identity coverage |
| Reference 24-hour freshness | Report age; do not serve expired previews as fresh | 1/9 entries within 24 h (11.11%); 8 expired | E04 | PARTIAL |
| Runtime cache decisions | Check TTL, preview coverage, date constraints and refresh | Covered tests pass for documented cases | E01 | PASS within tested cases |
| Year-only bounds | Source response and cache reuse meet requested bounds | Since-2022 request returned a 1900 publication | E06 | FAIL |
| Local relevance candidate selection | Strongest keyword match remains eligible | Strongest fixture match omitted in a 301-row corpus | E06 | FAIL |
| Newest/most-cited behavior | Sort supported known values and leave unknowns unknown | Explicit sort tests pass; live scientific suitability not measured | E01/E02 | PASS for tested ordering |
| Local keyword query plan | Appropriate strategy at intended corpus size | Full table scan | SQLite EXPLAIN, E05 | PARTIAL |
| Date-sort query plan | Avoid unnecessary full sort at intended scale | Scan plus temporary B-tree | SQLite EXPLAIN, E05 | PARTIAL |

The cache identity audit does not revalidate every effective date bound; a 100% identity score is not
a 100% cache-correctness score. The known `matches_dates` defect affects year-only constraints.
The runtime's 24-hour default is distinct from a global refresh scheduler: old records can remain
stored without being actively reused, and the app does not continuously refresh the entire corpus.

## Citation and research-answer quality

The **current** chat population contains zero stored claims and zero citations (E04). Its citation
validity rate is **undefined**, not 100%. The **historical 97-work snapshot** contains three claims
and three citations; all three IDs/quotes passed mechanical membership checks (E10). It is not the
current chat sample and was not generated during the audit.

Mocked tests reject unknown citation IDs, quotes absent from the excerpt, uncited claims, malformed
model output, and unsupported evidence paths (E01). That engineering control **PASS** does not measure
semantic faithfulness. Precision@K, Recall@K, semantic citation usefulness, factual accuracy and
hallucination rate are **NOT VERIFIED** because no judged reference set exists. Use the existing
[human-review rubric](../../../evaluation/HUMAN_REVIEW.md) before reporting such metrics.

## Integrity versus recoverability

A manual online backup/restore into a separate target preserved all table counts, including the 116
works and 126 raw observations, with FK/integrity checks and readiness passing (E09). This is a bounded
**PASS**. It does not negate the managed daily-backup **FAIL**: an empty pre-existing SQLite file was
accepted even though it held no user tables (E06). Physical SQLite validity is weaker than a valid
application recovery point. No corruption or deletion of the personal research data was observed.

## Recommended next checks

1. Add regression cases for year-only bounds, conflicting year/date fields, blank KPI values and the
   301-row ranking fixture before changing those implementations.
2. Make backup acceptance verify application content, schema and recoverability; test interrupted and
   same-day pre-upgrade copies in a disposable environment.
3. Define an author lifecycle and provenance policy without name-only identity merging.
4. Define freshness objectives and the query population before designing incremental ingestion or full-text indexing.
5. Build labeled real research tasks and adjudicated claims; keep their results separate from mechanical checks.
