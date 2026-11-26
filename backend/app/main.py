"""FastAPI app. Keep the external research APIs behind this server."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .chats import (
    ChatController,
    ChatInput,
    ChatPatch,
    event_page,
    get_conversation,
    message_page,
    paper_references,
    recover_interrupted,
    remove_conversation,
    snapshot,
    summary,
)
from .config import Settings
from .database import create_database_engine, single_instance
from .http_policy import LocalPolicy
from .interpreter import InvalidSearchIntent, SearchInterpreter
from .maintenance import daily_backup, maintain
from .migrations import migrate_database
from .models import Conversation, IngestionRun, Publication, RawPublicationRecord, utc_now
from .openalex import OpenAlexClient
from .providers import ProviderRuntime
from .public_access import COOKIE, MAX_AGE, VisitorPolicy, issue_cookie, read_visitor
from .quality import present_condition
from .repository import as_work_out, publications_by_ids, stats
from .schemas import AnswerResponse, ClarificationResponse, SearchRequest, SearchResponse, WorkDetail
from .service import ResearchSearchService
from .telemetry import Metrics, logger


def db_session(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def create_app(settings: Settings | None = None, openalex_client: OpenAlexClient | None = None) -> FastAPI:
    configuration = settings or Settings()
    engine = create_database_engine(configuration.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with single_instance(engine):
            await anyio.to_thread.run_sync(daily_backup, engine)
            await anyio.to_thread.run_sync(migrate_database, engine)

            def recover():
                with app.state.session_factory() as session:
                    recover_interrupted(session, app.state.chats.owner_id)

            await anyio.to_thread.run_sync(recover)
            shutdown = asyncio.Event()

            async def maintenance():
                while not shutdown.is_set():
                    try:
                        await anyio.to_thread.run_sync(
                            maintain, engine, app.state.session_factory, configuration.chat_retention_days
                        )
                    except Exception as exc:
                        logger.warning("maintenance_failed error_type=%s", type(exc).__name__)
                    try:
                        await asyncio.wait_for(shutdown.wait(), timeout=86400)
                    except TimeoutError:
                        pass

            task = asyncio.create_task(maintenance())
            try:
                yield
            finally:
                # Let an in-flight backup close its SQLite handles before disposal.
                shutdown.set()
                await task
                await app.state.providers.close()
                engine.dispose()

    app = FastAPI(title="Research Navigator", version="0.2.0", lifespan=lifespan)
    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.search_service = ResearchSearchService(
        configuration,
        SearchInterpreter(configuration),
        openalex_client or OpenAlexClient(configuration),
    )
    app.state.metrics = Metrics()
    app.state.providers = ProviderRuntime(configuration, app.state.session_factory, app.state.metrics)
    app.state.chats = ChatController(app.state.session_factory, app.state.search_service, app.state.providers)
    app.state.engine = engine
    app.add_middleware(VisitorPolicy, settings=configuration, sessions=app.state.session_factory)
    app.add_middleware(
        LocalPolicy,
        metrics=app.state.metrics,
        max_active_requests=configuration.max_active_requests,
        public_host=configuration.public_host if configuration.app_mode == "public_demo" else "",
    )

    @app.post("/api/session")
    def browser_session(request: Request):
        response = JSONResponse(
            {
                "public_demo": configuration.app_mode == "public_demo",
                "temporary_storage": configuration.app_mode == "public_demo",
            }
        )
        if configuration.app_mode == "public_demo" and not read_visitor(request, configuration.session_secret):
            response.set_cookie(
                COOKIE,
                issue_cookie(configuration.session_secret),
                max_age=MAX_AGE,
                secure=True,
                httponly=True,
                samesite="none",
                path="/",
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/conversations")
    def conversation_list(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
        before_updated_at: str | None = None,
        before_id: str | None = None,
        session: Session = Depends(db_session),
    ):
        statement = select(Conversation)
        if configuration.app_mode == "public_demo":
            statement = statement.where(Conversation.visitor_hash == request.state.visitor_hash)
        if before_updated_at:
            from datetime import datetime

            try:
                timestamp = datetime.fromisoformat(before_updated_at)
            except ValueError as exc:
                raise HTTPException(422, "Invalid conversation cursor.") from exc
            statement = statement.where(
                (Conversation.updated_at < timestamp)
                | ((Conversation.updated_at == timestamp) & (Conversation.id < (before_id or "")))
            )
        return [
            summary(row)
            for row in session.scalars(
                statement.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit)
            )
        ]

    @app.post("/api/conversations", status_code=201)
    def conversation_create(request: Request, session: Session = Depends(db_session)):
        row = Conversation(id=str(uuid4()), state={}, visitor_hash=getattr(request.state, "visitor_hash", None))
        session.add(row)
        session.commit()
        return snapshot(session, row)

    @app.get("/api/conversations/{conversation_id}")
    def conversation_read(conversation_id: str, session: Session = Depends(db_session)):
        return snapshot(session, get_conversation(session, conversation_id))

    @app.patch("/api/conversations/{conversation_id}")
    def conversation_patch(conversation_id: str, patch: ChatPatch, session: Session = Depends(db_session)):
        row = get_conversation(session, conversation_id)
        if row.active_request and (patch.filters is not None or patch.selected_work_ids is not None):
            raise HTTPException(409, "Research controls cannot change while this topic is processing.")
        if patch.title is not None:
            row.title = patch.title
        if patch.draft is not None:
            row.draft = patch.draft
        state = dict(row.state)
        if patch.filters is not None:
            state["filters"] = patch.filters.model_dump(mode="json")
        if patch.selected_work_ids is not None:
            if not set(patch.selected_work_ids) <= set(
                paper_references(session, row.id, selected=patch.selected_work_ids)
            ):
                raise HTTPException(422, "Select papers already shown in this conversation.")
            state["selected_work_ids"] = list(dict.fromkeys(patch.selected_work_ids))
        row.state = state
        row.updated_at = utc_now()
        session.commit()
        return summary(row)

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def conversation_delete(conversation_id: str, session: Session = Depends(db_session)):
        remove_conversation(session, get_conversation(session, conversation_id))

    @app.post("/api/conversations/{conversation_id}/messages")
    async def conversation_message(conversation_id: str, command: ChatInput):
        fresh = await anyio.to_thread.run_sync(app.state.chats.reserve, conversation_id, command)
        return StreamingResponse(
            app.state.chats.stream(conversation_id, command, fresh),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/conversations/{conversation_id}/messages")
    def conversation_messages(
        conversation_id: str,
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=30, ge=1, le=100),
        session: Session = Depends(db_session),
    ):
        return message_page(session, conversation_id, before_id, limit)

    @app.get("/api/conversations/{conversation_id}/runs/{run_id}/events")
    def conversation_events(
        conversation_id: str,
        run_id: str,
        after_id: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        session: Session = Depends(db_session),
    ):
        return event_page(session, conversation_id, run_id, after_id, limit)

    @app.get("/api/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"status": "ready", "database": "available"}
        except Exception:
            return JSONResponse({"status": "unavailable", "database": "unavailable"}, status_code=503)

    @app.get("/api/usage")
    def usage():
        return app.state.providers.usage()

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return PlainTextResponse(app.state.metrics.render(), media_type="text/plain; version=0.0.4")

    @app.get("/api/quality")
    def quality(session: Session = Depends(db_session)):
        def count(model):
            return session.scalar(select(func.count()).select_from(model))

        total = count(Publication)
        fields = {}
        for name in ("title", "doi", "abstract", "publication_date", "cited_by_count"):
            column = getattr(Publication, name)
            present = session.scalar(select(func.count()).select_from(Publication).where(present_condition(column)))
            fields[name] = {"present": present, "total": total}
        runs = session.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(100)).all()
        outcomes = {}
        for run in runs:
            outcomes[run.status] = outcomes.get(run.status, 0) + 1
        return {
            "scope": "stored local subset",
            "publications": total,
            "raw_records": count(RawPublicationRecord),
            "field_completeness": fields,
            "recent_ingestion_outcomes": outcomes,
            "recent_tracked_runs": sum(run.records_valid is not None for run in runs),
            "unreconciled_tracked_runs": sum(
                run.records_valid is not None
                and (
                    run.records_received != run.records_rejected + run.records_valid
                    or run.records_valid != run.records_duplicates + run.records_excluded_dates + run.records_usable
                )
                for run in runs
            ),
        }

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "application": "Research Navigator",
            "llm_enabled": bool(configuration.gemini_api_key or configuration.groq_api_key),
            "llm_model": configuration.gemini_model
            if configuration.gemini_api_key
            else configuration.groq_model
            if configuration.groq_api_key
            else None,
        }

    @app.get("/api/stats")
    def data_stats(session: Session = Depends(db_session)):
        return stats(session)

    @app.get("/api/works/{work_id}", response_model=WorkDetail)
    def work_detail(work_id: str = PathParam(pattern=r"^W\d+$", max_length=80), session: Session = Depends(db_session)):
        rows = publications_by_ids(session, [f"https://openalex.org/{work_id}"])
        if not rows:
            raise HTTPException(
                status_code=404, detail="This paper is not stored locally. Refresh the topic's results."
            )
        work = rows[0]
        return WorkDetail(
            **as_work_out(work).model_dump(), abstract=work.abstract, last_fetched_at=work.last_fetched_at
        )

    @app.post("/api/search", response_model=SearchResponse | ClarificationResponse | AnswerResponse)
    async def search(request: SearchRequest, http_request: Request):
        runtime = http_request.app.state.providers
        if not hasattr(app.state, "search_slots"):
            app.state.search_slots = asyncio.Semaphore(configuration.max_active_requests)
        async with app.state.search_slots:
            try:
                with runtime.turn():
                    async with asyncio.timeout(configuration.request_timeout_seconds):
                        return await http_request.app.state.search_service.search_owned(
                            request.message, app.state.session_factory, request.clarifications, request=request
                        )
            except InvalidSearchIntent as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="The request reached its time limit.") from exc

    frontend = Path(__file__).resolve().parents[2] / "frontend"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
