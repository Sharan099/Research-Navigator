# Coding agent instructions

This project teaches a beginner to reason like a data engineer while building a real prototype.

Before changes:

0. Start with `CODE_MAP.md`. Select the affected modules, interfaces, callers, and tests; verify that slice with `rg`. Avoid rereading the entire repository unless the map is insufficient or the task crosses the whole system.
1. Inspect relevant files with `rg --files` and `rg` before editing.
2. Explain which data flow, schema, or interface contract the change affects.
3. Keep source API access in the backend; never place keys in the browser.
4. Keep the current PostgreSQL-compatible schema and offline SQLite tests working.

After changes:

1. Run `pytest -q` and `node --check frontend/app.js` when relevant.
2. Add a test for each data-cleaning rule, cache decision, or retrieval error path.
3. Explain each changed file, its function, and the concepts it demonstrates.
4. Update `LEARNING_GUIDE.md` if the engineering behavior changed.
5. Never fabricate paper titles, authors, DOI values, citation counts, or successful API calls.
6. Update relevant entries in `CODE_MAP.md` when dependencies, contracts, table relationships, or test locations change. Treat the map as a navigation aid and confirm it against actual code.

Data quality rules:

- Preserve raw source records, provider identifiers, retrieval time, and missing values.
- Do not merge records solely on title similarity.
- Do not assume the local subset represents all of OpenAlex.
- Record uncertainty and surface upstream failures honestly.
- Do not interpret an LLM response as trusted SQL or executable code.
- Read official source documentation before changing ingestion or licensing assumptions.

This is a local learning prototype, not an authenticated employee service. Do not publish it as a company application or ingest non-public company data.

