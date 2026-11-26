"""Validated API contracts. The LLM cannot invent arbitrary query fields."""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClarificationTurn(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True, extra="forbid")

    question: str = Field(min_length=5, max_length=300)
    answer: str = Field(min_length=1, max_length=500)


WorkID = Annotated[str, Field(pattern=r"^https://openalex\.org/W\d+$", max_length=100)]
SortMode = Literal["relevance", "most_cited", "newest"]


def iso_date(value):
    """Parse wire dates before strict Pydantic validation; never infer a day from a year."""
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use an ISO date: YYYY-MM-DD")
        return date.fromisoformat(value)
    return value


class SearchIntent(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    query: str = Field(min_length=2, max_length=200)
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    date_from: date | None = None
    date_to: date | None = None
    sort: SortMode = "relevance"
    max_results: int = Field(default=10, ge=1, le=20)

    _dates = field_validator("date_from", "date_to", mode="before")(iso_date)

    @field_validator("query")
    @classmethod
    def nonempty_search(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("Search query is too short")
        return cleaned

    @model_validator(mode="after")
    def ordered_years(self):
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("Start year must not exceed end year")
        lower, upper = self.date_bounds()
        if lower and upper and lower > upper:
            raise ValueError("Start date must not exceed end date")
        return self

    def date_bounds(self) -> tuple[date | None, date | None]:
        lower = [x for x in (self.date_from, date(self.year_from, 1, 1) if self.year_from else None) if x]
        upper = [x for x in (self.date_to, date(self.year_to, 12, 31) if self.year_to else None) if x]
        return max(lower, default=None), min(upper, default=None)


class SearchPlan(SearchIntent):
    """Planning context is kept out of the retrieval cache fingerprint."""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: Literal["search", "answer"] = "search"
    clarification_question: str | None = Field(default=None, min_length=5, max_length=300)
    priorities: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=3)
    search_rationale: str = Field(default="", max_length=400)


class SearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    date_range: Literal["auto", "all_time", "last_30_days", "last_90_days", "this_year", "custom"] = "auto"
    date_from: date | None = None
    date_to: date | None = None
    sort: SortMode | None = None
    _dates = field_validator("date_from", "date_to", mode="before")(iso_date)

    @model_validator(mode="after")
    def valid_range(self):
        if self.date_range == "custom":
            if not self.date_from or not self.date_to or self.date_from > self.date_to:
                raise ValueError("Custom filters require an ordered start and end date")
        elif self.date_from or self.date_to:
            raise ValueError("Explicit dates require a custom date range")
        return self

    def overrides(self, today: date | None = None) -> dict:
        today = today or datetime.now(timezone.utc).date()
        result = {}
        if self.date_range != "auto":
            lower, upper = None, None
            if self.date_range in ("last_30_days", "last_90_days"):
                days = 30 if self.date_range == "last_30_days" else 90
                lower, upper = today - timedelta(days=days - 1), today
            elif self.date_range == "this_year":
                lower, upper = date(today.year, 1, 1), today
            elif self.date_range == "custom":
                lower, upper = self.date_from, self.date_to
            result.update(year_from=None, year_to=None, date_from=lower, date_to=upper)
        if self.sort is not None:
            result["sort"] = self.sort
        return result


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1000)


class SearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message: str = Field(min_length=3, max_length=500)
    clarifications: list[ClarificationTurn] = Field(default_factory=list, max_length=2)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=8)
    previous_query: SearchIntent | None = None
    filters: SearchFilters = Field(default_factory=SearchFilters)
    refresh: bool = False
    reuse_query: bool = False
    action: Literal["auto", "search", "answer"] = "auto"
    work_ids: list[WorkID] = Field(default_factory=list, max_length=20)
    selected_work_ids: list[WorkID] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def valid_paper_scope(self):
        if self.reuse_query and (self.previous_query is None or self.action != "search"):
            raise ValueError("Reusing a query requires a previous query and search action")
        if len(set(self.work_ids)) != len(self.work_ids) or len(set(self.selected_work_ids)) != len(
            self.selected_work_ids
        ):
            raise ValueError("Paper IDs must be unique")
        if not set(self.selected_work_ids) <= set(self.work_ids):
            raise ValueError("Selected papers must belong to the supplied results")
        return self


class ClarificationResponse(BaseModel):
    status: Literal["clarification"] = "clarification"
    question: str
    priorities: list[str] = Field(default_factory=list)
    search_rationale: str = ""
    parser_mode: Literal["gemini", "groq", "rules"]
    warnings: list[str] = Field(default_factory=list)


class WorkOut(BaseModel):
    id: str
    title: str | None
    doi: str | None
    publication_year: int | None
    publication_date: date | None = None
    has_abstract: bool = False
    authors: list[str]
    cited_by_count: int | None
    source: str | None
    landing_page_url: str | None


class WorkDetail(WorkOut):
    abstract: str | None
    last_fetched_at: datetime


class EvidenceSource(WorkOut):
    excerpt: str
    fetched_at: datetime
    truncated: bool = False


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    work_id: WorkID
    quote: str = Field(min_length=1, max_length=400)


class AnswerClaim(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=1200)
    citations: list[EvidenceCitation] = Field(min_length=1, max_length=5)


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    claims: list[AnswerClaim] = Field(min_length=1, max_length=8)


class AnswerResponse(BaseModel):
    status: Literal["answer"] = "answer"
    answer_mode: Literal["gemini", "groq", "evidence_only", "insufficient_evidence"]
    answer: str
    claims: list[AnswerClaim] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    status: Literal["results"] = "results"
    priorities: list[str] = Field(default_factory=list)
    search_rationale: str = ""
    answer: str
    works: list[WorkOut]
    retrieval_source: Literal["local_cache", "openalex_fresh", "local_fallback"]
    parser_mode: Literal["gemini", "groq", "rules", "reused"]
    query: SearchIntent
    warnings: list[str] = Field(default_factory=list)
    total_matches: int | None = None
    fetched_at: datetime | None = None
