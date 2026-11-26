# Source registry — first approved release

This registry records both implemented and candidate sources. A source is **not integrated** unless its implementation status says so.

| Provider | Role | Authority and identifiers | API/documentation | Status | Known limitations |
|---|---|---|---|---|---|
| OpenAlex | Broad discovery, citations, linked authors/institutions | Aggregates many scholarly feeds; work `W` IDs and DOIs | https://help.openalex.org/api/ | Implemented | Incomplete metadata, matching mistakes, API budget, paid true sync, query previews are partial |
| Crossref | DOI metadata and relation verification | DOI registration agency; publisher deposits | https://www.crossref.org/documentation/retrieve-metadata/rest-api/ | Planned, selective | Deposits can be incomplete or inaccurate; follow rate limits and polite access |
| DataCite | DOI metadata for datasets and other research outputs | DOI registration agency | https://support.datacite.org/docs/rest-api | Candidate | Coverage differs from Crossref; validate field mappings |
| arXiv | Preprint discovery / verification | Research repository; arXiv identifiers | https://arxiv.org/ | Candidate | Preprints are not necessarily peer-reviewed; DOI/version relationships matter |
| PubMed | Biomedical and medical robotics literature | NLM curated index; PMID | https://www.ncbi.nlm.nih.gov/home/develop/api/ | Candidate | Medical-focused; review access policies and API usage limits |

## How to add a source responsibly

For each candidate, write down:

1. User questions that justify adding it.
2. Exact fields and identifier types to import.
3. Official API documentation and license.
4. Request/rate limits and authentication method.
5. Ten to thirty relevant sample records and missing-field counts.
6. Conflict-resolution rules when a field differs from OpenAlex.
7. A source-specific adapter, tests, and `fetched_at` timestamps.

**Do not** silently combine citation counts from different providers. They may count different corpora. Keep the provider and retrieval date with each metric.

Last reviewed: 2026-11-20.

