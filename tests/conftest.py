"""All external async HTTP defaults to a transport that refuses network calls."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def offline_http(monkeypatch):
    original_client = httpx.AsyncClient

    def refuse(request):
        raise AssertionError(f"Unexpected external HTTP call to {request.url.host}")

    def client(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(refuse))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("OPENALEX_API_KEY", "")


@pytest.fixture
def mock_http(monkeypatch, offline_http):
    """Swap a shared transport's responder, like an upstream changing between requests."""
    original_client = httpx.AsyncClient
    state = {}

    def dispatch(request):
        return state["respond"](request)

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(dispatch)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    def install(payload=None, status=200, error=None, raw=None):
        requests = []

        def respond(request):
            requests.append(request)
            if error:
                raise error
            return httpx.Response(status, text=raw) if raw is not None else httpx.Response(status, json=payload)

        state["respond"] = respond
        return requests

    return install
