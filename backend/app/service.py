"""Orchestrate local-first reuse, remote discovery, and safe storage."""

from datetime import datetime, timedelta, timezone
from time import monotonic

import anyio
import httpx
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .answers import GroundedAnswerer
from .config import Settings
from .interpreter import InvalidSearchIntent, SearchInterpreter
from .models import IngestionRun, SearchCache, utc_now
from .openalex import OpenAlexClient
from .providers import ProviderBudgetExceeded, record_cache_decision
from .repository import (
    as_work_out,
    fingerprint,
    local_search,
    matches_dates,
    publications_by_ids,
    save_openalex_work,
    upsert_values,
)
from .schemas import (
    AnswerResponse,
    ClarificationResponse,
    ClarificationTurn,
    SearchIntent,
    SearchPlan,
    SearchRequest,
    SearchResponse,
)


def is_fresh(fetched_at: datetime, ttl_hours: int) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    return timedelta(0) <= age < timedelta(hours=max(0, ttl_hours))


def explain_results(query: str, count: int, total: int | None, source: str) -> str:
    if not count:
        if source == "local_fallback":
            return (
                f"Live search is unavailable and the local subset has no matching records for '{query}'. "
                "This does not establish that no publications exist. Try live search again later."
            )
        if total:
            return (
                f"OpenAlex reports {total:,} matches for '{query}', but no usable records were ingested. "
                "See the retrieval warnings and try again later."
            )
        return f"I could not find matching publications for '{query}'. Try different keywords or a wider year range."
    prefix = (
        "From the saved research records"
        if source == "local_cache"
        else (
            "From OpenAlex" if source == "openalex_fresh" else "From the local records available during the API outage"
        )
    )
    summary = f"{prefix}, I found {count} publication{'s' if count != 1 else ''} for '{query}'."
    if total is not None and total > count:
        summary += f" OpenAlex reports {total:,} matching works; this is a preview, not the complete catalog."
    summary += " Open a publication card to inspect its DOI, authors, source, and citation count."
    return summary


class ResearchSearchService:
    def __init__(self, settings: Settings, interpreter: SearchInterpreter, openalex: OpenAlexClient):
        self.settings = settings
        self.interpreter = interpreter
        self.openalex = openalex
        self.answerer = GroundedAnswerer(settings)

    async def search_owned(self, message, session_factory, clarifications=None, *, request=None, emit=None):
        """HTTP paths keep every ORM session inside one worker-owned DB operation."""

        async def database(operation):
            def execute():
                with session_factory() as session:
                    return operation(session)

            for attempt in range(3):
                try:
                    return await anyio.to_thread.run_sync(execute)
                except OperationalError as exc:
                    code = getattr(exc.orig, "sqlstate", None)
                    retryable = code in {"40001", "40P01"} or "database is locked" in str(exc.orig).lower()
                    if not retryable or attempt == 2:
                        raise
                    await anyio.sleep(0.02 * (2**attempt))

        return await self.search(message, None, clarifications, request=request, emit=emit, _db=database)

    async def search(
        self,
        message: str,
        session: Session,
        clarifications: list[ClarificationTurn] | None = None,
        *,
        request: SearchRequest | None = None,
        emit=None,
        _db=None,
    ) -> SearchResponse | ClarificationResponse | AnswerResponse:
        async def direct(operation):
            return operation(session)

        database = _db or direct

        async def progress(stage, status, details=None):
            if emit:
                await emit(stage, status, details)

        async def answer():
            await progress("dispatch", "completed", {"tool": "abstract_answer"})
            if _db:
                return await self.answerer.answer(request, None, emit=emit, _db=database)
            if emit:
                return await self.answerer.answer(request, session, emit=emit)
            return await self.answerer.answer(request, session)

        request = request or SearchRequest(message=message, clarifications=clarifications or [])
        if request.action == "answer":
            return await answer()
        await progress("planning", "running")
        advanced = (
            request.previous_query is not None
            or request.history
            or request.work_ids
            or request.filters.model_dump() != type(request.filters)().model_dump()
        )
        if request.reuse_query:
            intent = SearchPlan(
                **request.previous_query.model_dump(),
                search_rationale="Reused the topic's search parameters with the selected filters.",
            )
            parser_mode, warnings = "reused", []
        elif advanced:
            intent, parser_mode, warnings = await self.interpreter.interpret(message, clarifications, request=request)
        elif clarifications:
            intent, parser_mode, warnings = await self.interpreter.interpret(message, clarifications)
        else:
            intent, parser_mode, warnings = await self.interpreter.interpret(message)
        plan = SearchPlan.model_validate(intent.model_dump())
        await progress("planning", "completed", {"parser": parser_mode, "action": plan.action})
        if plan.clarification_question:
            if clarifications and len(clarifications) >= 2:
                raise InvalidSearchIntent(
                    "The search is still ambiguous. Start a new search with a specific topic and priority."
                )
            await progress("clarification", "completed", {"question": plan.clarification_question})
            return ClarificationResponse(
                question=plan.clarification_question,
                priorities=plan.priorities,
                search_rationale=plan.search_rationale,
                parser_mode=parser_mode,
                warnings=warnings,
            )
        if plan.action == "answer" and request.action == "auto" and request.work_ids:
            return await answer()
        # Only actual retrieval parameters identify a cache entry, never wording of the rationale.
        fields = plan.model_dump(include=set(SearchIntent.model_fields))
        fields.update(request.filters.overrides())
        intent = SearchIntent.model_validate(fields)
        await progress(
            "dispatch", "completed", {"tool": "search_publications", "parameters": intent.model_dump(mode="json")}
        )
        planning = {"priorities": plan.priorities, "search_rationale": plan.search_rationale}
        key = fingerprint(intent)
        await progress("cache", "running")

        def read_cache(db):
            cached = db.get(SearchCache, key)
            if request.refresh:
                return None, "refresh"
            if cached is None:
                return None, "miss"
            if not is_fresh(cached.fetched_at, self.settings.cache_ttl_hours):
                return None, "stale"
            rows = publications_by_ids(db, cached.work_ids)
            complete = len(rows) == len(set(cached.work_ids)) == len(cached.work_ids) == min(
                cached.total_matches, intent.max_results
            ) and all(matches_dates(row, intent) for row in rows)
            if not complete:
                return None, "incomplete"
            works = [as_work_out(row) for row in rows]
            return SearchResponse(
                **planning,
                answer=explain_results(intent.query, len(works), cached.total_matches, "local_cache"),
                works=works,
                retrieval_source="local_cache",
                parser_mode=parser_mode,
                query=intent,
                total_matches=cached.total_matches,
                fetched_at=cached.fetched_at,
                warnings=warnings,
            ), "hit"

        cached_response, decision = await database(read_cache)
        record_cache_decision(decision)
        await progress(
            "cache",
            "completed",
            {"decision": decision, **({"papers": len(cached_response.works)} if cached_response else {})},
        )
        if cached_response:
            return cached_response
        if decision == "incomplete":
            warnings.append("Saved preview coverage is incomplete. A live refresh was requested.")

        retrieval_started = monotonic()
        try:
            await progress("source_search", "running", {"source": "OpenAlex"})
            raw_works, total_matches = await self.openalex.search(intent)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            # The local database remains available if the upstream service fails.
            failure_type = type(exc).__name__

            def failed_retrieval(db):
                db.add(
                    IngestionRun(
                        query=intent.query,
                        status="failed",
                        error_message=failure_type,
                        completed_at=utc_now(),
                        duration_ms=round((monotonic() - retrieval_started) * 1000),
                        records_valid=0,
                        records_rejected=0,
                        records_duplicates=0,
                        records_excluded_dates=0,
                        records_usable=0,
                    )
                )
                db.commit()
                return [as_work_out(row) for row in local_search(db, intent)]

            works = await database(failed_retrieval)
            await progress("source_search", "failed", {"source": "OpenAlex", "message": "Live retrieval unavailable."})
            await progress("local_fallback", "running")
            warnings.append(
                "The local daily OpenAlex budget is exhausted. Results are from the local subset and may be incomplete."
                if isinstance(exc, ProviderBudgetExceeded)
                else "Live search is unavailable. Results are from the local subset and may be incomplete."
            )
            await progress("local_fallback", "completed", {"papers": len(works), "coverage": "partial local subset"})
            return SearchResponse(
                **planning,
                answer=explain_results(intent.query, len(works), None, "local_fallback"),
                works=works,
                retrieval_source="local_fallback",
                parser_mode=parser_mode,
                query=intent,
                warnings=warnings,
            )

        await progress("source_search", "completed", {"received": len(raw_works), "total_matches": total_matches})
        await progress("record_validation", "running")

        def persist(db):
            run = IngestionRun(query=intent.query, status="running", records_received=len(raw_works))
            db.add(run)
            db.flush()
            fetched_ids, seen = [], set()
            rejected = duplicates = excluded_dates = 0
            for record in raw_works:
                work_id = save_openalex_work(db, record, ingestion_run_id=run.id)
                if work_id is None:
                    rejected += 1
                    continue
                if work_id in seen:
                    duplicates += 1
                else:
                    seen.add(work_id)
                    fetched_ids.append(work_id)
            # Evaluate the final provider snapshot for repeated IDs, not an earlier copy.
            rows = publications_by_ids(db, fetched_ids)
            included = [row for row in rows if matches_dates(row, intent)]
            excluded_dates = len(rows) - len(included)
            fetched_ids = [row.openalex_id for row in included]
            fetched_at = utc_now()
            complete = rejected == 0 and len(fetched_ids) == len(raw_works) == min(total_matches, intent.max_results)
            if complete:
                upsert_values(
                    db,
                    SearchCache,
                    {
                        "fingerprint": key,
                        "intent": intent.model_dump(mode="json"),
                        "work_ids": fetched_ids,
                        "total_matches": total_matches,
                        "fetched_at": fetched_at,
                    },
                    "fingerprint",
                )
            else:
                cached = db.get(SearchCache, key)
                if cached is not None:
                    db.delete(cached)
            run.status = "succeeded" if complete else "partial"
            run.records_valid = len(raw_works) - rejected
            run.records_rejected, run.records_duplicates = rejected, duplicates
            run.records_excluded_dates, run.records_usable = excluded_dates, len(fetched_ids)
            run.completed_at, run.duration_ms = utc_now(), round((monotonic() - retrieval_started) * 1000)
            run.error_message = None if complete else "Incomplete preview; inspect retained raw records."
            works = [as_work_out(row) for row in included[: intent.max_results]]
            db.commit()
            return works, fetched_at, complete, rejected, duplicates, excluded_dates, len(fetched_ids)

        works, fetched_at, complete, rejected, duplicates, excluded_dates, usable = await database(persist)
        if not complete:
            warnings.append(
                f"Retrieved {len(raw_works)} records; rejected {rejected} malformed records. "
                f"Excluded {excluded_dates} records without matching publication dates. "
                f"The preview contains {usable} unique usable works and is incomplete; it was not cached."
            )
        await progress(
            "record_validation",
            "completed",
            {
                "usable": usable,
                "rejected": rejected,
                "duplicates": duplicates,
                "excluded_dates": excluded_dates,
                "cached": complete,
            },
        )

        return SearchResponse(
            **planning,
            answer=explain_results(intent.query, len(works), total_matches, "openalex_fresh"),
            works=works,
            retrieval_source="openalex_fresh",
            parser_mode=parser_mode,
            query=intent,
            warnings=warnings,
            total_matches=total_matches,
            fetched_at=fetched_at,
        )
