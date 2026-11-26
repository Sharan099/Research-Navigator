# Release and deployment

## Published repository and CI observation

The repository is now public at [Sharan099/Research-Navigator](https://github.com/Sharan099/Research-Navigator).
At commit `959fcfa`, the [observed workflow](https://github.com/Sharan099/Research-Navigator/actions/runs/45839781017)
passed Python 3.11/3.12/3.13, PostgreSQL and the dependency audit. Browser setup failed during
`npx playwright install --with-deps chromium`, before browser tests. Container and publication jobs
were skipped. Public annotations report exit code 1; detailed logs require access unavailable to this
agent, so the installation root cause is not established. These results supersede the earlier
"remote CI not executed" observations below for these specific jobs only.

The GitHub README is now a concise project entry page. The Hugging Face YAML metadata is in
[the Space template](huggingface/README.md). Copy that template as `README.md` only in a separate
Space checkout alongside the application/Dockerfile; keep the GitHub README free of Space metadata.

## Latest preparation: November 26

The [push guide](GITHUB.md) lists required source, ignored files, Git steps and GHCR usage.
Image archives, recordings and downloaded models are ignored. Docker context exclusions now reject
nested secrets and databases after the source-directory allowlist. CI saves its smoke-tested image
and publishes that same artifact on a passing main push, without rebuilding in the publication job.

The explicitly authorized Groq check accepted the key/model discovery for `openai/gpt-oss-120b`,
but shared AI usage was already 3/3. It stopped with `local_budget_exhausted`, zero generation
attempts and unchanged limits. Retry `python -m scripts.diagnose_groq --live --max-attempts 2`
after the UTC reset. Offline integration tests pass; live generation remains unverified.

A fresh actual Docker build failed before building because Docker config/Buildx access is denied;
the daemon is also inaccessible. GitHub credentials remain unavailable to the agent. No image
build, digest or remote publication is claimed. The sections below retain earlier dated observations;
see [VERIFICATION.md](VERIFICATION.md) for subsequent application fixes and regression evidence.

## Groq fallback update

The latest quota-fallback change passed 271 Python tests (2 PostgreSQL checks skipped) and 34 Node
frontend tests. Gemini remains first choice. Groq is selected for any Gemini provider failure
(updated November 26; previously quota only) or absent Gemini configuration, while shared AI-turn limits and citation validation remain enforced.
Groq model discovery returned HTTP 200 and listed the locally configured `openai/gpt-oss-120b`.
This was a metadata request; no live Groq generation was performed. No provider key was printed.
Git ignore checks confirmed 20 private/generated paths are excluded and 9 required source paths
remain available. See [GitHub procedure](GITHUB.md) for the current clean-export push process.

## Verified locally on 2026-11-26

- Python: 253 passed, 2 PostgreSQL tests skipped.
- Release export checks: 2 additional tests passed (private-file exclusion and credential rejection).
- Node frontend behavior: 33 passed.
- Chromium public-demo journeys: 2 passed, using a synthetic provider and HTTPS-origin proxy to
  a disposable application. No live OpenAlex or Gemini requests were made.
- Chromium local desktop/mobile and accessibility journeys: 4 passed.
- Public mode: signed cookie ownership, cross-visitor denial on every conversation interface,
  hidden historical chats, expiry/tampering rejection, host/origin checks, disabled public metrics.
- Gemini generation remains unverified. No Gemini key will be installed in a public deployment
  until real planning and cited-answer checks pass within the configured budget.

## External release gates

- GitHub repository: https://github.com/Sharan099/Research-Navigator (empty at inspection).
- GitHub push was attempted with interactive prompts disabled. It failed because Git Credential
  Manager could not use/persist Windows credentials and could not obtain a password. The public
  repository remains empty. Switching Git to its OpenSSL TLS backend fixed read access.
- Local Docker is installed, but access to its daemon/configuration is denied. No image size,
  successful build, running image, or published digest has been measured locally.
  An actual `docker build -t research-navigator:local .` attempt failed with access denied for
  the Docker configuration and Buildx instances directory, before building an image.
- The cached Hugging Face login belongs to `sharan099`; the account reports no PRO subscription.
  An actual public Docker Space creation request for free `cpu-basic` was rejected with HTTP 403:
  the credential has no rights to create a Space under `sharan099`. No Space was created and no
  paid resources were selected. The credential permission must be repaired first. Account plan
  eligibility may impose a separate restriction afterwards; that has not been tested successfully.

`python -m scripts.prepare_release` exports only allowlisted source files to a new `.runtime/release-*`
folder and checks them against configured credential values before copying. A local release commit
has been prepared there. Generated reports, databases, secrets, caches and recordings stay local.
Authenticate GitHub outside the restricted credential environment, then push the prepared checkout.
Grant the Hugging Face deployment credential Space creation/write permission in your own namespace.
Never paste a token into a commit, Git URL or Dockerfile.

The workflow in `.github/workflows/ci.yml` tests the application and container before publishing
to `ghcr.io/sharan099/research-navigator` on pushes to `main`. A package owner may need to make
the GitHub package public before anonymous image pulls work. Keep the commit tag for rollback.
CI source is not evidence that the remote checks have run.

## Public-demo operating limits

Run one worker. Set `APP_MODE=public_demo`, an exact `PUBLIC_HOST`, and a random `SESSION_SECRET`
of at least 32 characters. Save that secret in the hosting service, never in Git. Rotating it makes
existing browser cookies invalid. Cookies are capabilities, not verified human accounts.

Anyone with a valid browser session can search. Conversation data is isolated; public publication
metadata, cache entries and provider budgets are shared. Clearing cookies loses chat access.
New identities are not a billing boundary; provider-side quotas must protect the account.
Research/raw records accumulate and are not pruned by chat retention. This is a bounded demo,
not an enterprise or high-traffic deployment.

Free temporary disk may disappear on restart/rebuild. Daily usage on that disk disappears too;
local daily caps do not guarantee a monetary spending limit. No personal SQLite data is uploaded.
No persistent storage has been purchased. Use local Docker with a named volume for durable personal use.

## Recovery and changes

Use the backup/restore instructions in [OPERATIONS.md](OPERATIONS.md) for local data. A managed
daily backup has scoped fixes in [VERIFICATION.md](VERIFICATION.md); retain an independently verified
manual backup before upgrades. A free Space restart is a fresh demo, not a restoration guarantee.

Public mode adds nullable `conversations.visitor_hash` in migration `0003`. Old records remain
available locally and hidden publicly. Do not reuse a personal database as a public deployment.
If a release fails, redeploy the last verified image/Space commit. Do not claim migrations can be
rolled back without testing a restored copy first.
