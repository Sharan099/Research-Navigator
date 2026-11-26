"""Read-only OpenAlex client. API keys never reach the browser."""

from .config import Settings
from .providers import request_json
from .schemas import SearchIntent


class OpenAlexClient:
    endpoint = "https://api.openalex.org/works"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, intent: SearchIntent) -> tuple[list[object], int]:
        params: dict[str, str | int] = {
            "search": intent.query,
            "per_page": intent.max_results,
        }
        filters = []
        lower, upper = intent.date_bounds()
        if lower:
            filters.append(f"from_publication_date:{lower.isoformat()}")
        if upper:
            filters.append(f"to_publication_date:{upper.isoformat()}")
        if filters:
            params["filter"] = ",".join(filters)
        if intent.sort == "most_cited":
            params["sort"] = "cited_by_count:desc"
        elif intent.sort == "newest":
            params["sort"] = "publication_date:desc"
        headers = {"User-Agent": "ResearchNavigator/0.1 (educational research metadata project)"}
        if self.settings.openalex_api_key:
            headers["Authorization"] = f"Bearer {self.settings.openalex_api_key}"
        payload = await request_json(self.settings, "openalex", "GET", self.endpoint, params=params, headers=headers)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ValueError("OpenAlex response did not contain a result list")
        records = payload["results"]
        meta = payload.get("meta")
        count = meta.get("count") if isinstance(meta, dict) else None
        if type(count) is not int or not len(records) <= count <= 2_147_483_647:
            raise ValueError("OpenAlex response did not contain a consistent total count")
        return records, count
