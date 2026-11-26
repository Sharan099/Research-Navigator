# Setup and usage

Choose local Python or Docker. Keep provider credentials in a private `.env`, using `.env.example`
as the template. For operational checks and backup/restore, see [operations](OPERATIONS.md).

## How to use it

1. Open the app and start a topic.
2. Ask a question such as “Find research on tactile sensing for robotic grasping since 2022.”
3. Adjust the date and sort controls, or ask a follow-up in the same chat.
4. Open paper details, explain one paper, compare selected papers, or summarize the topic.
5. Check the activity panel and supporting abstract quotes. Always check the original sources.

Public-demo chats belong to your browser cookie. Clearing cookies loses access. Free hosting
may erase all data on restart. Provider allowances are shared across visitors. If cookies are
blocked inside Hugging Face, use **Open application** to open the direct app URL.

## Run with Python

Use Python 3.11–3.13. From the project folder:

```text
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --workers 1
```

Open http://127.0.0.1:8001. SQLite is created automatically. Copy `.env.example` to `.env`
to configure keys and limits. Keep `.env` private. `APP_MODE=local` is the default.

## Run with Docker

```text
docker build -t research-navigator:local .
docker volume create research-data
docker run --rm --name research-navigator -p 127.0.0.1:8000:8000 -v research-data:/data research-navigator:local
```

Open http://127.0.0.1:8000. The volume keeps your database between container runs.
To load backend keys, add `--env-file .env -e DATABASE_URL=sqlite:////data/research_navigator.db`
before the image name. Never bake keys into the image.

After a passing GitHub release publishes the image, you can replace the local image name with
`ghcr.io/sharan099/research-navigator:latest`. A commit tag gives a fixed version. The package
must be made public in GitHub package settings before anonymous pulls work.

For local PostgreSQL, run `docker compose up --build`. Compose binds ports to localhost.

## Hugging Face deployment

Create `sharan099/Research-Navigator` as a **public Docker Space** using free CPU hardware only.
Copy the [Space README template](huggingface/README.md) into the Space checkout as its root `README.md`; it sets the Docker SDK and port. Add these Space settings:

| Setting | Value |
|---|---|
| Variable `APP_MODE` | `public_demo` |
| Variable `PUBLIC_HOST` | The exact app hostname, for example `sharan099-research-navigator.hf.space` |
| Secret `SESSION_SECRET` | A securely generated random value of at least 32 characters |
| Optional secret `OPENALEX_API_KEY` | Your OpenAlex key |
| Optional secret `GEMINI_API_KEY` | Enable only after successful live Gemini verification |
| Optional secret `GROQ_API_KEY` | Groq quota fallback, backend only |
| Variable `GROQ_MODEL` | A Groq model verified for your account |
| Variable `GEMINI_MODEL` | A model verified for your account |

Do not upload a local database. Temporary Space storage is not a backup. No paid storage or
hardware is required by this project; platform account eligibility may still block free Docker creation.

Gemini generation is currently **unverified**: earlier requests timed out and the local AI-turn
budget was exhausted. With neither LLM configured, the app uses rules and shows stored evidence honestly.
To check your configured account without generation:

```text
python -m scripts.diagnose_gemini
```

Use `--live --max-attempts 4` only when you intend to spend provider attempts. The command respects
durable daily limits, uses a temporary research copy, and writes `.runtime/gemini-diagnostic.json`.
Default daily allowances are 3 AI turns, 6 Gemini attempts, and 20 OpenAlex attempts (UTC reset).
Groq has its own default limit of 6 attempts via `GROQ_REQUESTS_PER_DAY`. Set `GROQ_API_KEY` and
optionally `GROQ_MODEL` in your private `.env`. It accepts a comma-separated list of free Groq models
(default `openai/gpt-oss-120b,openai/gpt-oss-20b`); the next model is tried on 404/408/429/5xx.
`AI_TURNS_PER_DAY` is shared across Gemini and Groq. Exhausting that shared limit blocks both;
fallback does not reset counters or increase limits. Any Gemini failure (quota, timeout, 5xx,
authentication, unusable output) hands the turn to Groq; the warning names the Gemini failure code. With only Groq configured,
Groq can act as the primary provider. Daily usage and result metadata identify the provider used.
Temporary hosting resets local counters when storage is lost; these are not provider billing guarantees.

## Push to GitHub

Follow the [step-by-step PowerShell guide](GITHUB.md). It uses a credential-checked clean
export, reviews staged files, signs in through Git Credential Manager and pushes to your repository.
The guide lists files to include/exclude and explains GHCR publication of the smoke-tested image.
Keep `.env`, local data, virtual environments, caches, logs and recordings outside Git. Commit
`.env.example`, source, tests, lockfiles and CI so another person can reproduce the application.

Check Groq metadata and allowance with `python -m scripts.diagnose_groq`. Add
`--live --max-attempts 2` only to authorize generation checks. The latest metadata check passed,
but live generation stopped at the shared 3/3 daily AI-turn limit. No counters were reset.
