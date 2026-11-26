"""Serve an isolated copy of real saved research for an offline screen recording."""

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--port", type=int, default=8016)
    args = parser.parse_args()
    source = args.snapshot.resolve(strict=True)
    original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="showcase-", dir=runtime) as directory:
        destination = Path(directory) / "recording.sqlite3"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
        with closing(sqlite3.connect(destination)) as connection, connection:
            saved = next(
                (
                    (conversation, json.loads(result))
                    for conversation, result in connection.execute(
                        "SELECT conversation_id, result FROM chat_messages WHERE role='assistant' ORDER BY id"
                    )
                    if result and json.loads(result).get("answer_mode") == "gemini"
                ),
                None,
            )
            if not saved:
                raise ValueError("Snapshot must contain a genuine saved Gemini answer for the labeled replay.")
            replay_id, answer = saved
            connection.execute(
                "UPDATE conversations SET title=?, draft='' WHERE id=?", ("Saved AI session - replay", replay_id)
            )
            metadata = {
                "replay_topic_id": replay_id,
                "replay_title": "Saved AI session - replay",
                "saved_claims": len(answer.get("claims", [])),
                "source_sha256": original_digest,
                "source": source.name,
                "isolation": "Disposable database copy; original snapshot opened read-only",
                "network": "Both provider keys empty; both provider budgets zero",
            }
        application = create_app(
            Settings(
                database_url=f"sqlite:///{destination.as_posix()}",
                gemini_api_key="",
                groq_api_key="",
                openalex_api_key="",
                ai_turns_per_day=0,
                gemini_requests_per_day=0,
                openalex_requests_per_day=0,
            )
        )
        server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=args.port, log_level="warning"))

        @application.get("/__showcase/metadata")
        async def recording_metadata():
            return metadata

        @application.post("/__showcase/shutdown")
        async def shutdown_recording_server():
            server.should_exit = True
            return {"status": "stopping isolated recording server"}

        # These routes exist only in this recording helper, before the static catch-all.
        application.router.routes.insert(0, application.router.routes.pop())
        application.router.routes.insert(0, application.router.routes.pop())
        server.run()
    if hashlib.sha256(source.read_bytes()).hexdigest() != original_digest:
        raise RuntimeError("Source snapshot changed during recording.")


if __name__ == "__main__":
    main()
