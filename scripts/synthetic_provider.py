"""Offline fixture shared by browser verification and pipeline benchmarks."""

import asyncio

import httpx


class SyntheticProvider:
    """Synthetic metadata only; never a measurement of live OpenAlex behavior."""

    def __init__(self):
        self.fail = False
        self.calls = 0

    async def search(self, intent):
        self.calls += 1
        if self.fail:
            raise httpx.TimeoutException("Synthetic provider outage")
        await asyncio.sleep(0.01)
        return [
            {
                "id": "https://openalex.org/W900000001",
                "title": "Synthetic robot learning benchmark fixture",
                "publication_year": 2026,
                "publication_date": "2026-01-01",
                "type": "article",
                "cited_by_count": 0,
                "authorships": [],
                "abstract_inverted_index": {"Synthetic": [0], "robot": [1], "learning": [2], "fixture.": [3]},
            }
        ], 1
