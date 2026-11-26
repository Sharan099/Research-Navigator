# Push Research Navigator to GitHub

These PowerShell steps assume `Sharan099/Research-Navigator` is still empty. Do not force-push over
someone else's commits. Use your normal terminal so Windows Credential Manager can store your login.

Code goes to the Git repository. Docker images go to GitHub Container Registry (GHCR), linked to
the repository. Do not commit a Docker image archive into Git.

## Files to include and exclude

| Push to Git | Purpose |
|---|---|
| `backend/`, `frontend/`, `migrations/`, `alembic.ini` | Application and versioned schema |
| `scripts/`, `tests/` | Operations, diagnostics, evaluation and verification |
| `Dockerfile`, `.dockerignore`, `compose.yaml`, `.github/workflows/` | Container and CI/release automation |
| `pyproject.toml`, requirements locks, `package.json`, `package-lock.json`, Playwright configs | Reproducible dependencies and browser tests |
| `.env.example`, `.gitignore`, `LICENSE` | Safe configuration template and repository rules |
| README, code map, learning/audit docs, `docs/`, `evaluation/README.md`, `evaluation/published/` | Usage, architecture and reviewed aggregate evidence |

| Keep out of Git | Explicit ignore coverage |
|---|---|
| Keys, personal configuration and credentials | `.env`, `.env.*` except `.env.example`, keys/certificates, cloud/token/config directories |
| Databases, journals, backups and SQL exports | Database/SQLite patterns, `backups/`, `*.dump`, `*.backup`, local data directories |
| Environments, packages and caches | `.venv*/`, `venv/`, `.tools/`, `.cache/`, `.runtime/`, `node_modules/`, Python/test caches |
| Logs, coverage and browser reports | `*.log`, coverage patterns, `test-results/`, `playwright-report/` |
| Private reports and personal learning progress | `evaluation/reports/`, `evaluation/audits/`, `learning/PROGRESS.md` |
| Recorded media and downloaded models | `showcase/`, `*.mp4`, `*.webm`, `*.wav`, `*.onnx`, `*.onnx.json`, `*.safetensors` |
| Container exports and build artifacts | `*.tar`, `*.tar.gz`, `*.tgz`, `build/`, `dist/` |

Use the clean exporter and review staged filenames. The table is not permission to add private
files under other extensions. Share the demo video separately as a reviewed GitHub Release asset
if desired; recordings and voice models are not needed to build the application.

## 1. Export the important files

Open PowerShell in `H:\research-navigator` using the project's Python environment.

```powershell
Set-Location H:\research-navigator
$release = python -m scripts.prepare_release | ConvertFrom-Json
$release
Set-Location $release.directory
```

The exporter copies source, tests, dependency locks, documentation and CI into a new clean folder.
It checks files against configured credential values. The original project and research data stay
where they are. A new export includes your latest code; older `.runtime/release-*` exports do not
update automatically. This is the folder to commit and push.

## 2. Create the local Git repository

```powershell
git init -b main
git config user.name "Sharan099"
git config user.email "Sharan099@users.noreply.github.com"
git config http.sslBackend openssl
git remote add origin https://github.com/Sharan099/Research-Navigator.git
```

The OpenSSL setting avoids the Windows Schannel error observed in the restricted environment.
It does not replace authentication. Use your GitHub-provided commit email instead if it differs.

## 3. Confirm private files are ignored

```powershell
git check-ignore .env .env.local research_navigator.db research_navigator.db-wal .runtime/example .tools/example node_modules/example backups/example
git check-ignore research-navigator-image.tar demo.mp4 local-voice.onnx evaluation/reports/private.json
git status --short
```

The first command should print all those private/generated paths. `.env.example`, application code,
tests, Dockerfile, workflow and dependency locks must remain available for staging.
Do not copy `.env` or your database into the exported folder.

## 4. Review and commit

```powershell
git add --all
git diff --cached --stat
git ls-files -- .env "*.db" "*.db-*" "*.sqlite3" ".runtime/**" ".tools/**"
git commit -m "Add Research Navigator with Docker and LLM quota fallback"
```

The `git ls-files` command should print nothing. Review the staged filenames before committing.
Never paste keys into source code, README, commands in Git history, or the remote URL.
`.gitignore` does not remove files already tracked in an existing repository. If a key was previously
published, revoke it; ignoring the file afterwards does not erase history.

## 5. Sign in and push

```powershell
git credential-manager github login --username Sharan099 --browser
git push -u origin main
```

Complete GitHub's sign-in in your browser. Do not put a personal access token in the command or URL.
If Windows credential storage is denied, run these steps from your normal desktop terminal or use
GitHub Desktop to add this exported repository and publish it to the existing empty repository.
If a push is rejected because the remote already has commits, fetch/reconcile them; do not use `--force`.

## 6. Verify the release

Open https://github.com/Sharan099/Research-Navigator and check that README, source and tests appear.
Open the **Actions** tab. The workflow tests Python, PostgreSQL, browsers, dependencies and Docker.
It publishes the image only after those checks pass. Open the repository's **Packages** link to
find `ghcr.io/sharan099/research-navigator`; set the package to Public if anonymous pulls are desired.

No image should be described as published until the workflow has actually succeeded.
For future work, export a fresh release and copy its reviewed changes into your existing checkout,
then commit and push. Keep the existing Git history instead of reinitializing the published repository.

## 7. Run the published Docker image

After Actions succeeds, set the package visibility to Public for anonymous downloads. CI publishes
the exact image that passed its readiness/nonroot checks. Use a commit-SHA tag for a fixed release,
or `latest` for the latest passing main push.

```powershell
docker pull ghcr.io/sharan099/research-navigator:latest
docker volume create research-data
docker run --rm --name research-navigator -p 127.0.0.1:8000:8000 -v research-data:/data ghcr.io/sharan099/research-navigator:latest
```

Open http://127.0.0.1:8000. For backend keys, copy `.env.example` to private `.env` and run:

```powershell
docker run --rm --name research-navigator -p 127.0.0.1:8000:8000 -v research-data:/data --env-file .env -e DATABASE_URL=sqlite:////data/research_navigator.db ghcr.io/sharan099/research-navigator:latest
```

The database override keeps data in the mounted volume even if `.env` specifies a different local
path. Start Docker Desktop and run one backend worker. For a local build first, run
`docker build -t research-navigator:local .` and substitute that local image name in the run command.
These instructions do not establish successful image publication. This restricted agent cannot
access the Docker daemon/Buildx configuration or store your GitHub login in Windows Credential Manager.

## Verify Groq

```powershell
python -m scripts.diagnose_groq
# Only when you intend to spend up to two generation attempts:
python -m scripts.diagnose_groq --live --max-attempts 2
```

Preflight checks key/model metadata and usage without generating text. Live verification exercises
real app routes on a temporary research copy, with Gemini and scholarly retrieval disabled. The real
usage ledger is charged; no limits are reset or raised. Safe results go to ignored
`.runtime/groq-diagnostic.json` without provider bodies or keys.

On November 26 the configured model was listed, but shared AI usage was **3/3**. The authorized live
check made **zero generation requests**. Offline tests verify planning, answers, citation rejection
and fallback. Successful live generation remains unverified. Counters reset by UTC date; rerun after
allowance is available. Groq's separate allowance cannot bypass the shared AI-turn cap.

See the [official GHCR documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
for registry authentication and package visibility.
