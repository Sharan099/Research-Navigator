"""Public browser isolation is tested with disposable SQLite and synthetic requests."""

import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import Conversation
from backend.app.public_access import COOKIE, MAX_AGE, issue_cookie, visitor_hash


@pytest.fixture
def public_app(tmp_path):
    config = Settings(
        app_mode="public_demo",
        public_host="demo.hf.space",
        session_secret="test-secret-" * 4,
        database_url=f"sqlite:///{tmp_path / 'public.db'}",
        gemini_api_key="",
        openalex_api_key="",
    )
    app = create_app(config)
    with TestClient(app, base_url="https://demo.hf.space") as owner:
        yield app, owner, config


def test_cookie_properties_and_isolation_across_every_conversation_route(public_app):
    app, owner, config = public_app
    response = owner.post("/api/session")
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"] and "SameSite=none" in response.headers["set-cookie"]
    topic = owner.post("/api/conversations").json()["id"]
    assert owner.get("/api/conversations").json()[0]["id"] == topic
    with TestClient(app, base_url="https://demo.hf.space") as stranger:
        assert stranger.get("/api/conversations").status_code == 401
        stranger.post("/api/session")
        assert stranger.get("/api/conversations").json() == []
        command = {"message": "Explain these papers", "request_id": str(uuid4())}
        for method, path, body in [
            ("GET", f"/api/conversations/{topic}", None),
            ("PATCH", f"/api/conversations/{topic}", {"title": "stolen"}),
            ("DELETE", f"/api/conversations/{topic}", None),
            ("GET", f"/api/conversations/{topic}/messages?before_id=1", None),
            ("POST", f"/api/conversations/{topic}/messages", command),
            ("GET", f"/api/conversations/{topic}/runs/{command['request_id']}/events", None),
        ]:
            assert stranger.request(method, path, **({"json": body} if body else {})).status_code == 404
        own = stranger.post("/api/conversations").json()["id"]
        assert owner.get(f"/api/conversations/{own}").status_code == 404
    assert owner.get(f"/api/conversations/{topic}").json()["title"] != "stolen"
    assert owner.delete(f"/api/conversations/{topic}").status_code == 204


def test_unowned_local_chats_hidden_and_tampered_cookie_rejected(public_app):
    app, client, _ = public_app
    with app.state.session_factory() as session:
        session.add(Conversation(id="old-local", state={}))
        session.commit()
    client.post("/api/session")
    assert client.get("/api/conversations").json() == []
    assert client.get("/api/conversations/old-local").status_code == 404
    client.cookies.set(COOKIE, "tampered", domain="demo.hf.space", path="/")
    assert client.get("/api/conversations").status_code == 401


def test_cookie_expiry_and_invalid_signature(monkeypatch):
    secret = "synthetic-secret" * 3
    token = issue_cookie(secret)
    assert visitor_hash(token, secret)
    assert visitor_hash(token, "different-secret") is None
    future = time.time() + MAX_AGE + 1
    monkeypatch.setattr(time, "time", lambda: future)
    assert visitor_hash(token, secret) is None


def test_public_host_origin_metrics_and_health(public_app):
    _, client, _ = public_app
    assert client.get("/api/health").status_code == 200
    assert client.get("/metrics").status_code == 404
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/session", headers={"Origin": "http://demo.hf.space"}).status_code == 403
    assert "frame-ancestors https://huggingface.co" in client.get("/").headers["content-security-policy"]


@pytest.mark.parametrize("override", [{"session_secret": "short"}, {"public_host": "*.hf.space"}])
def test_invalid_hosted_configuration_fails(override):
    with pytest.raises(ValueError):
        Settings(**{"app_mode": "public_demo", "session_secret": "s" * 32, "public_host": "demo.hf.space", **override})
