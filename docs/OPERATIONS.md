# Operating the local release

This release targets one local server and public research metadata. Bind the host port to
loopback. It has no user authentication, shared-service authorization or company data controls.
Passing local checks is distinct from approval to deploy an employee service.

## Reproducible installation and checks

Use Python 3.11–3.13 and Node.js 22. Create an isolated virtual environment, then run:

```text
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
npm ci --ignore-scripts
npx playwright install chromium
python scripts/release_check.py --browser --audit --database research_navigator.db
```

The runtime lock contains exact versions; the development lock additionally pins test, lint,
coverage and audit tools. `package-lock.json` includes package integrity hashes. Update locks
deliberately and rerun the full release checks:

```text
python -m piptools compile pyproject.toml --output-file requirements.lock --strip-extras --no-header --no-annotate --upgrade
python -m piptools compile pyproject.toml --extra test --extra dev --constraint requirements.lock --output-file requirements-dev.lock --strip-extras --no-header --no-annotate
npm install --ignore-scripts
```

Python locks pin versions without wheel hashes: wheels vary by Python and OS. The CI matrix
must establish cross-platform compatibility; generating a lock on one machine is not evidence
that every interpreter has passed. Never commit `.env`, user databases, backups or API keys.

## CI and release candidate

`.github/workflows/ci.yml` runs lint, branch coverage, offline contracts, PostgreSQL integration,
Node behavior tests, desktop/mobile Chromium journeys, axe accessibility checks and dependency
audits. Its final job builds a container, checks readiness and confirms a nonroot user. The
manual workflow also saves a verified image as a seven-day artifact. Passing main-branch pushes
are configured to publish to GHCR after all gates pass. This configuration is not evidence of a
successful remote run or deployment. Secrets are not required for offline verification.

The current workspace has no Git remote. Workflow configuration is committed-ready source;
remote CI success must be established after a repository is initialized and pushed.

`release_check.py --browser` runs both the local desktop/mobile journeys and public-browser ownership
suite. Aggregate evidence suitable for Git lives in `evaluation/published/`; raw logs and databases
remain private. See [current verification](VERIFICATION.md) for measured results and remaining gates.

## Run and inspect

```text
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --workers 1
docker compose up --build
```

Compose exposes the application and PostgreSQL only on localhost. Its default credentials
are for an isolated local development database; use `POSTGRES_PASSWORD` to override them.
For passwords containing URL-reserved characters, provide an appropriately URL-encoded
database URL rather than interpolating an unescaped password into the connection URL.

`GET /api/health` is liveness, `GET /api/ready` checks database readiness, `/metrics` exposes
operational measurements, and `/api/usage` reports daily provider budgets. A configured key
does not establish successful upstream connectivity. Do not call paid APIs from health probes.

Run one backend worker. A second process must not recover the first process's in-flight work.
The application uses a database-specific single-instance guard. Do not bypass it to increase
workers; distributed leases and a queue require a separate architecture change.

The container runs as an unprivileged user. Direct image execution defaults to a SQLite
database under `/data`; mount a writable volume to preserve it. Compose instead uses a named
PostgreSQL volume. Deleting that volume deletes the research database.

## Migration and rollback

Take a backup before upgrading. The versioned database migration runs at startup and is also
available through Alembic. Keep the application stopped during maintenance and verify the
configured target before running upgrade commands. Follow the current migration instructions
in the project README. Do not downgrade a populated schema to roll back application code.
Restore the tested pre-upgrade backup into a separate database, validate it, then explicitly
switch the application's database setting.

## Backup and restore drill

SQLite uses the standard library's online backup API, so a WAL database is copied coherently:

```text
python scripts/operations.py backup research_navigator.db backups/research-2026-11-24.sqlite3
python scripts/operations.py restore backups/research-2026-11-24.sqlite3 .runtime/restored-2026-11-24.sqlite3
python scripts/evaluate.py --database .runtime/restored-2026-11-24.sqlite3 --output evaluation/reports/restore.json
```

Destinations must not exist; commands never overwrite the live database. Restore checks the
SQLite integrity result, foreign keys and table row counts. Store backup copies outside this
workstation if recovery from workstation loss is required. Protect access to saved chat text.

For PostgreSQL, install matching client utilities. Set `DATABASE_URL` in the environment for
backup and a separate `RESTORE_DATABASE_URL` for an empty disposable target:

```text
python scripts/postgres_backup.py backup backups/research-2026-11-24.dump
python scripts/postgres_backup.py restore backups/research-2026-11-24.dump --url-env RESTORE_DATABASE_URL
```

URLs and passwords are not printed or placed in child-process arguments. Restore refuses
the default live URL variable and a database with existing user tables. Validate restored
publication/raw/message counts and FK integrity through SQL before switching targets.
The SQLite evaluation command currently does not audit PostgreSQL. Actual PostgreSQL restore
execution remains a release gate until measured in a permitted environment.

## Retention

The policy retains inactive conversations for 30 days and owned SQLite backups for seven
days. Active conversations and shared publication/raw metadata are preserved. Preview first:

```text
python scripts/operations.py cleanup research_navigator.db --backups backups
python scripts/operations.py cleanup research_navigator.db --backups backups --apply
```

Only regular, nonsymlink `research-*.sqlite3` files inside the explicitly named backup folder
qualify. Nothing is deleted recursively. The write transaction rechecks chat inactivity before
deleting child records. The server also runs its database maintenance policy daily and creates
a daily SQLite recovery copy; these commands provide explicit preview and restore validation.
PostgreSQL backup rotation remains an operator responsibility. Keep at least one
separately protected verified recovery copy before removing old local backups.

## Release gates

| Gate | Required evidence |
|---|---|
| Correctness | Offline SQLite tests, Node checks, meaningful fault-path coverage |
| Data quality | Provenance and uniqueness intact; no unexplained rejected records |
| PostgreSQL | Actual concurrent integration and migration tests, not DDL compilation alone |
| UI | Desktop/mobile journeys, axe results and human visual/keyboard review |
| Recovery | Separate restore target passes integrity and count checks |
| Supply chain | Python/Node audit results reviewed and image built from the locks |
| Performance | Declared workload, environment, percentiles and errors; no invented SLO claims |
| Shared deployment | Authentication, authorization, TLS, ownership, privacy and operational approval |

Official references: [SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup),
[PostgreSQL pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html),
[Ruff configuration](https://docs.astral.sh/ruff/configuration/), and
[Playwright web servers](https://playwright.dev/docs/test-webserver).
