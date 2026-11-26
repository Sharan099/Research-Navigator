"""Durable chat state and a small allowlisted interface to the research tools.

The browser supplies a command, never authoritative history or evidence. Each run
owns its database session; SSE is only a view of its durable operational events.
"""

import asyncio
import json
import re
from contextlib import suppress
from datetime import timedelta
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

import anyio
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, literal, select, update

from .interpreter import InvalidSearchIntent
from .models import ChatEvent, ChatMessage, ChatRun, Conversation, MessagePaper, utc_now
from .repository import as_work_out, publications_by_ids
from .schemas import SearchFilters, SearchRequest, WorkDetail, WorkID
from .telemetry import logger


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str
    message: str = Field(min_length=1, max_length=500)
    action: Literal["auto", "search", "answer", "details"] = "auto"
    filters: SearchFilters | None = None
    selected_work_ids: list[WorkID] = Field(default_factory=list, max_length=5)
    refresh: bool = False
    reuse_query: bool = False

    @field_validator("request_id")
    @classmethod
    def uuid_id(cls, value):
        return str(UUID(value))


class ChatPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    draft: str | None = Field(default=None, max_length=500)
    filters: SearchFilters | None = None
    selected_work_ids: list[WorkID] | None = Field(default=None, max_length=5)


def messages(session, conversation_id, before_id=None, limit=30):
    statement = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if before_id is not None:
        statement = statement.where(ChatMessage.id < before_id)
    rows = session.scalars(statement.order_by(ChatMessage.id.desc()).limit(limit)).all()
    return list(reversed(rows))


def known_papers(rows):
    papers = {}
    for row in rows:
        if row.result:
            works = row.result.get("works", []) + row.result.get("sources", [])
            if row.result.get("work"):
                works.append(row.result["work"])
            for work in works:
                papers[work["id"]] = work
    return papers


def paper_references(session, conversation_id, text="", selected=()):
    # Query compact references, not every historical message/abstract payload.
    statement = select(MessagePaper).where(MessagePaper.conversation_id == conversation_id)
    candidates = (
        statement.where(
            MessagePaper.work_id.in_(selected)
            | literal(text.lower()).contains(func.lower(MessagePaper.title))
            | MessagePaper.work_id.in_(re.findall(r"https://openalex\.org/W\d+", text))
            | MessagePaper.work_id.in_(["https://openalex.org/" + value for value in re.findall(r"\bW\d+\b", text)])
        )
        .order_by(MessagePaper.message_id.desc())
        .limit(100)
    )
    return {row.work_id: {"id": row.work_id, "title": row.title} for row in session.scalars(candidates)}


def index_papers(session, row):
    for position, work in enumerate(known_papers([row]).values()):
        session.add(
            MessagePaper(
                message_id=row.id,
                conversation_id=row.conversation_id,
                work_id=work["id"],
                position=position,
                title=work.get("title"),
            )
        )


def get_conversation(session, conversation_id):
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


def summary(conversation):
    return {
        "id": conversation.id,
        "title": conversation.title,
        "updated_at": conversation.updated_at.isoformat(),
        "busy": bool(conversation.active_request),
    }


def message_out(row):
    return {
        "id": row.id,
        "role": row.role,
        "text": row.text,
        "result": row.result,
        "request_id": row.request_id,
        "error": row.text if row.role == "assistant" and row.result is None else None,
    }


def event_page(session, conversation_id, run_id, after_id=0, limit=100):
    run = session.get(ChatRun, run_id)
    if run is None or run.conversation_id != conversation_id:
        raise HTTPException(404, "Request activity not found.")
    rows = session.scalars(
        select(ChatEvent)
        .where(ChatEvent.run_id == run_id, ChatEvent.sequence > after_id)
        .order_by(ChatEvent.sequence)
        .limit(limit + 1)
    ).all()
    page = rows[:limit]
    return {
        "events": [row.payload for row in page],
        "next_after": page[-1].sequence if page else after_id,
        "has_more": len(rows) > limit,
        "status": run.status,
    }


def run_summaries(session, conversation_id, rows):
    request_ids = list(dict.fromkeys(row.request_id for row in rows))
    order = {value: index for index, value in enumerate(request_ids)}
    runs = sorted(
        session.scalars(select(ChatRun).where(ChatRun.conversation_id == conversation_id, ChatRun.id.in_(request_ids))),
        key=lambda run: order[run.id],
    )
    return [
        {
            "id": run.id,
            "status": run.status,
            "error": run.error,
            "events": event_page(session, conversation_id, run.id)["events"] if index == len(runs) - 1 else [],
        }
        for index, run in enumerate(runs)
    ]


def message_page(session, conversation_id, before_id=None, limit=30):
    get_conversation(session, conversation_id)
    rows = messages(session, conversation_id, before_id, limit + 1)
    more = len(rows) > limit
    page = rows[-limit:]
    return {
        "messages": [message_out(row) for row in page],
        "next_before": page[0].id if more and page else None,
        "runs": run_summaries(session, conversation_id, page),
    }


def snapshot(session, conversation):
    page = message_page(session, conversation.id)
    return {
        **summary(conversation),
        "draft": conversation.draft,
        "state": conversation.state,
        "messages": page["messages"],
        "messages_before": page["next_before"],
        "runs": page["runs"],
    }


def append_event(session, run, stage, status, details=None, duration_ms=0):
    sequence = (session.scalar(select(func.max(ChatEvent.sequence)).where(ChatEvent.run_id == run.id)) or 0) + 1
    if sequence > 200:
        raise InvalidSearchIntent("This request exceeded its activity limit. Please start a new turn.")
    payload = {
        "id": sequence,
        "request_id": run.id,
        "conversation_id": run.conversation_id,
        "stage": stage,
        "status": status,
        "details": details or {},
        "at": utc_now().isoformat(),
        "duration_ms": duration_ms,
    }
    session.add(ChatEvent(run_id=run.id, sequence=sequence, payload=payload))
    run.heartbeat_at = utc_now()
    return payload


def recover_interrupted(session, owner_id=None, stale_only=False):
    cutoff = utc_now() - timedelta(seconds=120)
    statement = select(ChatRun).where(ChatRun.status == "running")
    if stale_only:
        statement = statement.where((ChatRun.heartbeat_at.is_(None)) | (ChatRun.heartbeat_at < cutoff))
    elif owner_id:
        statement = statement.where((ChatRun.owner_id.is_(None)) | (ChatRun.owner_id != owner_id))
    for run in session.scalars(statement):
        run.status = "interrupted"
        run.error = "The request was interrupted. Send the question again to continue."
        append_event(session, run, "request", "interrupted", {"message": run.error})
        session.execute(
            update(Conversation)
            .where(Conversation.id == run.conversation_id, Conversation.active_request == run.id)
            .values(active_request=None)
        )
    session.execute(
        update(Conversation)
        .where(
            Conversation.active_request.is_not(None),
            Conversation.active_request.not_in(select(ChatRun.id).where(ChatRun.status == "running")),
        )
        .values(active_request=None)
    )
    session.commit()


def remove_conversation(session, conversation):
    if conversation.active_request:
        raise HTTPException(409, "Wait for this conversation's current request to finish.")
    run_ids = select(ChatRun.id).where(ChatRun.conversation_id == conversation.id)
    session.execute(delete(ChatEvent).where(ChatEvent.run_id.in_(run_ids)))
    session.execute(delete(MessagePaper).where(MessagePaper.conversation_id == conversation.id))
    for model in (ChatMessage, ChatRun):
        session.execute(delete(model).where(model.conversation_id == conversation.id))
    session.delete(conversation)
    session.commit()


def reference_scope(text, current, known):
    """Resolve stable IDs, displayed ordinals, and literal title mentions; never guess."""
    lower = text.casefold()
    explicit = [work_id for work_id in known if work_id in text or work_id.rsplit("/", 1)[-1] in text.split()]
    titles = [
        work_id
        for work_id, work in known.items()
        if work.get("title") and len(work["title"]) >= 8 and work["title"].casefold() in lower
    ]
    if not explicit and len(titles) > 1 and not re.search(r"compare|all|both", lower):
        return [], "Several papers share that title. Which OpenAlex ID do you mean?"
    if explicit or titles:
        return list(dict.fromkeys(explicit + titles))[:6], None
    words = {
        "first": 1,
        "second": 2,
        "third": 3,
        "fourth": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    indices = []
    for match in re.finditer(
        r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b|(?:paper|work|publication|#)\s*(\d{1,2})\b",
        lower,
    ):
        indices.append(words[match[1]] if match[1] else int(match[2]))
    if indices and re.search(
        r"\b(paper|papers|work|works|publication|publications|one|ones|compare)\b|#|(?:explain|summarize|summarise) (?:the )?(?:first|second|third|fourth|fifth)\b",
        lower,
    ):
        if any(index < 1 or index > len(current) for index in indices):
            return (
                [],
                "That paper number is outside the current result list. Which paper title or OpenAlex ID do you mean?",
            )
        if re.search(r"earlier|previous|older", lower):
            return [], "Which earlier result list do you mean? Please give the paper title or OpenAlex ID."
        return list(dict.fromkeys(current[index - 1]["id"] for index in indices)), None
    if re.search(r"\b(this|that|particular) (paper|work|publication)\b", lower):
        if len(current) == 1:
            return [current[0]["id"]], None
        return [], "Which paper do you mean? Give its number in the latest result list, title, or OpenAlex ID."
    return [], None


def build_request(command, conversation, rows, references=None):
    state = conversation.state
    known = {**(references or {}), **known_papers(rows)}
    current = state.get("results", [])
    pending = state.get("pending")
    original = pending["message"] if pending else command.message
    turns = (
        [*pending.get("clarifications", []), {"question": pending["question"], "answer": command.message}]
        if pending and pending.get("kind") != "reference"
        else []
    )
    selected = command.selected_work_ids
    if not set(selected) <= set(known):
        raise InvalidSearchIntent("Select papers already shown in this conversation.")
    error = None
    if command.action != "search" and not selected:
        selected, error = reference_scope(command.message, current, known)
        if pending and pending.get("kind") == "reference":
            # A bare number is a clarification answer, not a new search topic.
            selected, error = reference_scope(
                "paper " + command.message if command.message.isdigit() else command.message, current, known
            )
            error = error or (
                None if selected else "Please identify a paper by its displayed number, title, or OpenAlex ID."
            )
    if len(selected) > 5:
        error = "Choose up to five papers to explain or compare at once."
    if error:
        return (
            None,
            {"status": "clarification", "question": error, "priorities": [], "parser_mode": "rules", "warnings": []},
            original,
        )
    filters = command.filters or SearchFilters.model_validate(state.get("filters", {}))
    action = command.action
    if action == "auto" and re.search(r"\b(find|search|discover)\b", original, re.I):
        action = "search"
    if action == "auto" and selected:
        action = "details" if re.search(r"\b(details|metadata|full abstract)\b", original, re.I) else "answer"
    if action == "auto" and re.search(r"\b(details|metadata|full abstract)\b", original, re.I) and current:
        if len(current) != 1:
            return (
                None,
                {
                    "status": "clarification",
                    "question": "Which paper should I open? Give its number, title, or OpenAlex ID.",
                    "parser_mode": "rules",
                    "warnings": [],
                },
                original,
            )
        selected, action = [current[0]["id"]], "details"
    # Recent turns supply language context; saved state supplies durable constraints.
    history = []
    for row in rows[-8:]:
        text = row.text or (row.result or {}).get("answer") or (row.result or {}).get("question")
        if row.result and row.result.get("claims"):
            text = " ".join(claim["text"] for claim in row.result["claims"])
        if text:
            history.append({"role": row.role, "content": text[:1000]})
    # Title/ID lookup spans all saved messages but only matching context goes to Gemini.
    for work_id in selected:
        title = known[work_id].get("title") or "Untitled publication"
        if len(history) >= 8:
            history.pop(0)
        history.append({"role": "user", "content": f"Referenced publication: {work_id}; {title}"[:1000]})
    scope = list(dict.fromkeys([*(work["id"] for work in current), *selected]))
    if len(scope) > 20:
        scope = [*selected, *(work_id for work_id in scope if work_id not in selected)][:20]
    previous_query = state.get("query")
    reuse = command.reuse_query
    refresh = command.refresh
    if (
        command.action == "auto"
        and previous_query
        and re.fullmatch(
            r"(?:please )?refresh (?:the )?(?:current )?(?:topic(?:'s)? )?(?:sources|results|research|papers)\.?",
            original,
            re.I,
        )
    ):
        action, reuse, refresh, selected = "search", True, True, []
    if reuse:
        if not previous_query:
            raise InvalidSearchIntent("Search a research topic before reusing or refreshing its filters.")
        action = "search"
    # A bounded filter tool handles common conversational controls without a model call.
    # Other research refinements continue through the validated Gemini planner.
    if (
        command.action == "auto"
        and previous_query
        and re.fullmatch(
            r"\s*(?:please )?(?:sort|order|filter|show|only|keep|latest|newest|most cited)\b[\w\s,.-]{0,150}",
            original,
            re.I,
        )
        and re.search(r"newest|latest|recent|cited|relevance|last (?:30|90) days|this year", original, re.I)
    ):
        # Do not mistake a new topical query for a control instruction.
        residual = re.sub(
            r"\b(?:please|sort|order|filter|show|only|keep|these|those|them|the|papers|paper|works|results|publications|by|from|in|for|of|and|to|on|me|first|newest|latest|most|recent|cited|citations|citation|count|relevance|relevant|last|past|30|90|days|this|year)\b|[,. -]",
            "",
            original.lower(),
        )
        if not residual:
            previous_query = dict(previous_query)
            sort = (
                "most_cited"
                if re.search(r"cited|citation", original, re.I)
                else "newest"
                if re.search(r"newest|latest|recent", original, re.I)
                else "relevance"
                if re.search(r"relevan", original, re.I)
                else previous_query.get("sort", "relevance")
            )
            previous_query["sort"] = sort
            if filters.date_range == "auto":
                preset = (
                    "last_30_days"
                    if re.search(r"last 30 days", original, re.I)
                    else "last_90_days"
                    if re.search(r"last 90 days", original, re.I)
                    else "this_year"
                    if re.search(r"this year", original, re.I)
                    else None
                )
                if preset:
                    previous_query.update(SearchFilters(date_range=preset).overrides())
            action, reuse, selected = "search", True, []
    request = SearchRequest(
        message=original if len(original) >= 3 else f"Find {original}",
        clarifications=turns,
        history=history,
        previous_query=previous_query,
        filters=filters,
        action="answer" if action == "details" else action,
        work_ids=scope,
        selected_work_ids=selected,
        refresh=refresh,
        reuse_query=reuse,
    )
    return (request, action), None, original


class ChatController:
    def __init__(self, session_factory, service, runtime=None):
        self.sessions = session_factory
        self.service = service
        self.runtime = runtime
        self.owner_id = str(uuid4())
        self.reserve_lock = Lock()

    async def db(self, operation):
        def owned():
            with self.sessions() as session:
                return operation(session)

        return await anyio.to_thread.run_sync(owned)

    def reserve(self, conversation_id, command):
        # One local process: serialize admission across its worker threads.
        with self.reserve_lock:
            return self._reserve(conversation_id, command)

    def _reserve(self, conversation_id, command):
        with self.sessions() as session:
            recover_interrupted(session, stale_only=True)
            conversation = get_conversation(session, conversation_id)
            existing = session.get(ChatRun, command.request_id)
            payload = command.model_dump(mode="json")
            if existing:
                if existing.conversation_id != conversation_id or existing.payload != payload:
                    raise HTTPException(409, "Request ID already belongs to a different command.")
                if existing.status == "running":
                    raise HTTPException(409, "This request is still running. Reload to see its saved activity.")
                return False
            if self.runtime:
                count = session.scalar(select(func.count()).select_from(ChatRun).where(ChatRun.status == "running"))
                if count >= self.runtime.settings.max_active_requests:
                    raise HTTPException(
                        429, "The local request limit is busy. Retry shortly.", headers={"Retry-After": "2"}
                    )
            acquired = session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id, Conversation.active_request.is_(None))
                .values(active_request=command.request_id)
            )
            if acquired.rowcount != 1:
                raise HTTPException(409, "This conversation already has an active request.")
            session.add(
                ChatRun(
                    id=command.request_id,
                    conversation_id=conversation_id,
                    payload=payload,
                    events=[],
                    owner_id=self.owner_id,
                    heartbeat_at=utc_now(),
                )
            )
            session.add(
                ChatMessage(
                    conversation_id=conversation_id, request_id=command.request_id, role="user", text=command.message
                )
            )
            if conversation.title == "New topic":
                conversation.title = command.message[:65]
            conversation.updated_at = utc_now()
            conversation.draft = ""
            session.commit()
            return True

    async def stream(self, conversation_id, command, fresh):
        queue = asyncio.Queue()
        started = {}

        async def emit(stage, status, details=None):
            now = monotonic()
            if status == "running":
                started[stage] = now
            duration = round((now - started.get(stage, now)) * 1000)
            if status != "running":
                started.pop(stage, None)

            def record(session):
                run = session.get(ChatRun, command.request_id)
                event = append_event(session, run, stage, status, details, duration)
                session.commit()
                return event

            event = await self.db(record)
            if self.runtime:
                self.runtime.metrics.increment("stages", {"stage": stage, "status": status})
                if status != "running":
                    self.runtime.metrics.observe("stage_seconds", duration / 1000, {"stage": stage})
            await queue.put(("activity", event))

        async def finish(status, result=None, error=None):
            def persist(session):
                run = session.get(ChatRun, command.request_id)
                conversation = get_conversation(session, conversation_id)
                # A disconnect after result commit must never erase a completed answer.
                if run.status == "completed":
                    return
                if result or error:
                    row = ChatMessage(
                        conversation_id=conversation_id,
                        request_id=run.id,
                        role="assistant",
                        result=result,
                        text=error or "",
                    )
                    session.add(row)
                    session.flush()
                    if result:
                        index_papers(session, row)
                if result:
                    state = dict(conversation.state)
                    if command.filters:
                        state["filters"] = command.filters.model_dump(mode="json")
                    if result["status"] == "results":
                        state.update(query=result["query"], results=result["works"], selected_work_ids=[])
                    if result["status"] != "clarification":
                        state["pending"] = None
                    conversation.state = state
                run.status, run.result, run.error = status, result, error
                run.heartbeat_at = utc_now()
                if conversation.active_request == run.id:
                    conversation.active_request = None
                conversation.updated_at = utc_now()
                session.commit()

            await self.db(persist)

        def prepare(session):
            conversation = get_conversation(session, conversation_id)
            current_row = session.scalar(
                select(ChatMessage.id).where(ChatMessage.request_id == command.request_id, ChatMessage.role == "user")
            )
            rows = messages(session, conversation_id, before_id=current_row, limit=8)
            references = paper_references(session, conversation_id, command.message, command.selected_work_ids)
            prepared, clarification, original = build_request(command, conversation, rows, references)
            if clarification:
                conversation.state = {
                    **conversation.state,
                    "pending": {
                        "kind": "reference",
                        "message": original,
                        "question": clarification["question"],
                        "clarifications": [],
                    },
                }
                session.commit()
            return prepared, clarification, original, len(rows)

        def details(session, request):
            scope = request.selected_work_ids or request.work_ids
            if len(scope) != 1:
                raise InvalidSearchIntent("Choose one paper to open its details.")
            papers = publications_by_ids(session, scope)
            if not papers:
                raise InvalidSearchIntent("This paper is no longer stored. Refresh the search.")
            row = papers[0]
            return {
                "status": "details",
                "work": WorkDetail(
                    **as_work_out(row).model_dump(), abstract=row.abstract, last_fetched_at=row.last_fetched_at
                ).model_dump(mode="json"),
            }

        async def heartbeat():
            while True:
                await asyncio.sleep(5)

                def beat(session):
                    session.execute(
                        update(ChatRun)
                        .where(ChatRun.id == command.request_id, ChatRun.status == "running")
                        .values(heartbeat_at=utc_now())
                    )
                    session.commit()

                try:
                    await self.db(beat)
                except Exception as exc:
                    logger.warning(
                        "heartbeat_failed request_id=%s error_type=%s", command.request_id, type(exc).__name__
                    )

        async def work():
            committed = False
            beat = asyncio.create_task(heartbeat())
            try:
                await emit("request", "running")
                await emit("memory", "running")
                prepared, clarification, original, count = await self.db(prepare)
                await emit("memory", "completed", {"recent_messages": count})
                if clarification:
                    await emit("clarification", "completed", {"question": clarification["question"]})
                    result = clarification
                else:
                    request, action = prepared
                    await emit(
                        "dispatch",
                        "completed",
                        {
                            "tool": "paper_details" if action == "details" else "research",
                            "selected_papers": request.selected_work_ids,
                        },
                    )
                    if action == "details":
                        await emit("paper_details", "running")
                        result = await self.db(lambda session: details(session, request))
                        await emit("paper_details", "completed", {"papers": 1})
                    else:
                        response = await self.service.search_owned(
                            request.message, self.sessions, request.clarifications, request=request, emit=emit
                        )
                        result = response.model_dump(mode="json")
                    if result["status"] == "clarification":

                        def pending(session):
                            conversation = get_conversation(session, conversation_id)
                            conversation.state = {
                                **conversation.state,
                                "pending": {
                                    "kind": "search",
                                    "message": original,
                                    "question": result["question"],
                                    "clarifications": [turn.model_dump() for turn in request.clarifications],
                                },
                            }
                            session.commit()

                        await self.db(pending)
                await finish("completed", result=result)
                committed = True
                await emit("request", "completed", {"response": result["status"]})
                await queue.put(
                    ("result", {"request_id": command.request_id, "conversation_id": conversation_id, "result": result})
                )
            except asyncio.CancelledError:
                if not committed:
                    with anyio.CancelScope(shield=True):
                        with suppress(Exception):
                            await finish(
                                "interrupted", error="Connection interrupted. Send the question again to continue."
                            )
                            for stage in list(started):
                                await emit(stage, "interrupted")
                raise
            except Exception as exc:
                logger.warning("chat_failed request_id=%s error_type=%s", command.request_id, type(exc).__name__)
                error = (
                    str(exc)
                    if isinstance(exc, InvalidSearchIntent)
                    else "This request could not complete. Please retry."
                )
                with suppress(Exception):
                    await finish("failed", error=error)
                    for stage in list(started):
                        await emit(stage, "failed", {"message": error} if stage == "request" else {})
                await queue.put(("error", {"request_id": command.request_id, "message": error}))
            finally:
                beat.cancel()
                with suppress(asyncio.CancelledError):
                    await beat

        async def bounded_work():
            from contextlib import nullcontext

            timeout = self.runtime.settings.request_timeout_seconds if self.runtime else 60
            with self.runtime.turn() if self.runtime else nullcontext():
                try:
                    await asyncio.wait_for(work(), timeout=timeout)
                except TimeoutError:
                    with suppress(Exception):
                        await finish(
                            "interrupted", error="The request reached its time limit. Local research remains available."
                        )
                    await queue.put(
                        ("error", {"request_id": command.request_id, "message": "The request reached its time limit."})
                    )
                finally:
                    await queue.put(None)

        if not fresh:

            def replay(session):
                run = session.get(ChatRun, command.request_id)
                values = [
                    ("activity", row.payload)
                    for row in session.scalars(
                        select(ChatEvent).where(ChatEvent.run_id == run.id).order_by(ChatEvent.sequence)
                    )
                ]
                values.append(
                    ("result", {"request_id": run.id, "conversation_id": conversation_id, "result": run.result})
                    if run.result
                    else ("error", {"request_id": run.id, "message": run.error})
                )
                return values

            for name, data in await self.db(replay):
                yield self.frame(name, data)
            return
        task = asyncio.create_task(bounded_work())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                yield self.frame(*item)
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    @staticmethod
    def frame(name, data):
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
