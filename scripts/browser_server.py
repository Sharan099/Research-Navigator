"""Disposable browser-test server. No real provider or user database is accessed."""

import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import Settings
from backend.app.main import create_app
from scripts.synthetic_provider import SyntheticProvider

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="research-browser-") as directory:
        application = create_app(
            Settings(
                database_url=f"sqlite:///{Path(directory) / 'browser.db'}",
                gemini_api_key="",
                groq_api_key="",
                openalex_api_key="",
                app_mode="public_demo" if "--public" in sys.argv else "local",
                public_host="demo.hf.space" if "--public" in sys.argv else "",
                session_secret="synthetic-browser-fixture-secret-32" if "--public" in sys.argv else "",
            ),
            openalex_client=SyntheticProvider(),
        )
        server = uvicorn.Server(uvicorn.Config(application, host="127.0.0.1", port=8015, log_level="warning"))

        @application.post("/__test_shutdown")
        async def shutdown_fixture_server():
            server.should_exit = True
            return {"status": "stopping disposable fixture server"}

        # The application's final static-file mount is a catch-all; fixture-only routes precede it.
        application.router.routes.insert(0, application.router.routes.pop())
        server.run()
