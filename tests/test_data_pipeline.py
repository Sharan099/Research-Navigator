"""Synthetic fixtures test contracts; they do not describe real publications."""

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.interpreter import rule_based_intent
from backend.app.main import create_app
from backend.app.repository import abstract_from_index, normalize_doi

SAMPLE_WORK = {
    "id": "https://openalex.org/W1234567890",
    "title": "Tactile Sensors for Robot Manipulation",
    "doi": "https://doi.org/10.1234/TEST.123",
    "publication_year": 2024,
    "type": "article",
    "cited_by_count": 42,
    "abstract_inverted_index": {"Tactile": [0], "sensors": [1], "help": [2], "robots": [3]},
    "authorships": [
        {"author": {"id": "https://openalex.org/A111", "display_name": "Researcher A"}},
        {"author": {"id": "https://openalex.org/A222", "display_name": "Researcher B"}},
    ],
    "primary_location": {
        "landing_page_url": "https://doi.org/10.1234/TEST.123",
        "source": {"display_name": "Example Robotics Journal"},
    },
}


class FakeOpenAlex:
    def __init__(self):
        self.calls = 0
        self.fail = False

    async def search(self, intent):
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("Simulated upstream outage")
        return [SAMPLE_WORK], 1


def test_doi_and_abstract_normalization():
    assert normalize_doi("https://doi.org/10.1234/TEST.123") == "10.1234/test.123"
    assert normalize_doi(None) is None
    assert normalize_doi("unregistered-identifier") is None
    assert abstract_from_index({"robots": [2], "Tactile": [0], "help": [1]}) == "Tactile help robots"


def test_search_intent_is_not_sql():
    intent = rule_based_intent("Find most cited tactile robot manipulation papers since 2023")
    assert intent.query == "tactile robot manipulation"
    assert intent.sort == "most_cited"
    assert intent.year_from == 2023


def test_fetch_cache_and_local_fallback(tmp_path):
    fake = FakeOpenAlex()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}", cache_ttl_hours=24, gemini_api_key="", openalex_api_key=""
    )
    application = create_app(settings=settings, openalex_client=fake)
    with TestClient(application) as client:
        first = client.post("/api/search", json={"message": "Find tactile robot manipulation papers since 2023"})
        assert first.status_code == 200
        assert first.json()["retrieval_source"] == "openalex_fresh"
        assert first.json()["works"][0]["authors"] == ["Researcher A", "Researcher B"]
        assert first.json()["works"][0]["doi"] == "10.1234/test.123"
        assert fake.calls == 1

        repeat = client.post("/api/search", json={"message": "Find tactile robot manipulation papers since 2023"})
        assert repeat.status_code == 200
        assert repeat.json()["retrieval_source"] == "local_cache"
        assert fake.calls == 1

        fake.fail = True
        fallback = client.post("/api/search", json={"message": "Find tactile sensors"})
        assert fallback.status_code == 200
        assert fallback.json()["retrieval_source"] == "local_fallback"
        assert len(fallback.json()["works"]) == 1
        assert fallback.json()["warnings"]

        stats = client.get("/api/stats").json()
        assert stats["publications"] == 1
        assert stats["authors"] == 2
        assert stats["raw_records"] == 1
        assert stats["ingestion_runs"] == 2


def test_refresh_is_idempotent_and_website_serves(tmp_path):
    fake = FakeOpenAlex()
    # A zero-hour TTL forces a fresh remote request for every search.
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'refresh.db'}", cache_ttl_hours=0, gemini_api_key="", openalex_api_key=""
    )
    application = create_app(settings=settings, openalex_client=fake)
    with TestClient(application) as client:
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
        for _ in range(2):
            response = client.post("/api/search", json={"message": "Find tactile sensors"})
            assert response.status_code == 200
            assert response.json()["retrieval_source"] == "openalex_fresh"
            assert response.json()["works"][0]["authors"] == ["Researcher A", "Researcher B"]
        counts = client.get("/api/stats").json()
        assert fake.calls == 2
        assert counts["publications"] == 1
        assert counts["authors"] == 2
        assert counts["raw_records"] == 2  # History is preserved.
