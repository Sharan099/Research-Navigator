"""Shared, bounded upstream access. Persistent quotas count every HTTP attempt."""

import asyncio
import json
import random
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import monotonic

import anyio
import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import DailyUsage
from .telemetry import Metrics

_runtime = ContextVar("provider_runtime", default=None)
_ai_charged = ContextVar("ai_turn_charged", default=False)


def record_cache_decision(decision):
    runtime = _runtime.get()
    if runtime is not None:
        runtime.metrics.increment("cache", {"decision": decision})


class ProviderBudgetExceeded(httpx.RequestError):
    """The local daily budget was exhausted; local fallback is still available."""

    def __init__(self, message, *, resource=None):
        super().__init__(message)
        self.resource = resource


def retry_after(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


class ProviderRuntime:
    def __init__(self, settings, session_factory=None, metrics=None):
        self.settings = settings
        self.sessions = session_factory
        self.metrics = metrics or Metrics()
        self.client = None
        self.limits = {}

    @contextmanager
    def turn(self):
        runtime_token = _runtime.set(self)
        charged_token = _ai_charged.set(False)
        try:
            yield
        finally:
            _ai_charged.reset(charged_token)
            _runtime.reset(runtime_token)

    async def close(self):
        if self.client:
            await self.client.aclose()

    def _charge(self, provider, charge_turn):
        if not self.sessions:
            return
        resources = [
            (
                provider,
                self.settings.gemini_requests_per_day
                if provider == "gemini"
                else self.settings.groq_requests_per_day
                if provider == "groq"
                else self.settings.openalex_requests_per_day,
            )
        ]
        if charge_turn:
            resources.append(("ai_turns", self.settings.ai_turns_per_day))
        with self.sessions() as session:
            insert = sqlite_insert if session.bind.dialect.name == "sqlite" else pg_insert
            for resource, cap in resources:
                if cap == 0:
                    raise ProviderBudgetExceeded(
                        "The configured daily provider budget is exhausted.", resource=resource
                    )
                statement = insert(DailyUsage).values(day=datetime.now(timezone.utc).date(), resource=resource, used=1)
                statement = statement.on_conflict_do_update(
                    index_elements=[DailyUsage.day, DailyUsage.resource],
                    set_={"used": DailyUsage.used + 1},
                    where=DailyUsage.used < cap,
                ).returning(DailyUsage.used)
                if session.scalar(statement) is None:
                    raise ProviderBudgetExceeded(
                        "The configured daily provider budget is exhausted.", resource=resource
                    )
            session.commit()

    async def request_json(self, provider, method, url, **kwargs):
        try:
            return await self._request_json(provider, method, url, **kwargs)
        except TimeoutError as exc:
            raise httpx.TimeoutException("The provider request reached its time limit") from exc

    async def _request_json(self, provider, method, url, **kwargs):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds, follow_redirects=False)
        semaphore = self.limits.setdefault(provider, asyncio.Semaphore(self.settings.provider_concurrency))
        async with asyncio.timeout(self.settings.request_timeout_seconds), semaphore:
            for attempt in range(self.settings.provider_attempts):
                charge_turn = provider in {"gemini", "groq"} and not _ai_charged.get()
                try:
                    await anyio.to_thread.run_sync(self._charge, provider, charge_turn)
                except ProviderBudgetExceeded:
                    self.metrics.increment("provider_attempts", {"provider": provider, "outcome": "quota"})
                    raise
                if charge_turn:
                    _ai_charged.set(True)
                started = monotonic()
                retry_delay = None
                try:
                    async with self.client.stream(method, url, **kwargs) as response:
                        if (
                            response.status_code in (429, 502, 503, 504)
                            and not (provider == "gemini" and response.status_code == 429)
                            and attempt + 1 < self.settings.provider_attempts
                        ):
                            delay = retry_after(response.headers.get("Retry-After"))
                            # A long Retry-After is respected by stopping, never retrying early.
                            if delay > 2:
                                response.raise_for_status()
                            retry_delay = max(delay, random.uniform(0.05, 0.15) * (2**attempt))
                        if retry_delay is None:
                            response.raise_for_status()
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > self.settings.max_response_bytes:
                                    raise ValueError("Provider response exceeded the configured size limit")
                                chunks.append(chunk)
                            payload = json.loads(b"".join(chunks))
                            if provider == "gemini" and isinstance(payload, dict):
                                metadata = payload.get("usageMetadata", {})
                                for name in ("promptTokenCount", "candidatesTokenCount"):
                                    value = metadata.get(name) if isinstance(metadata, dict) else None
                                    if type(value) is int and value >= 0:
                                        self.metrics.increment("provider_tokens", {"kind": name}, value)
                            self.metrics.increment("provider_attempts", {"provider": provider, "outcome": "success"})
                            return payload
                        self.metrics.increment("provider_attempts", {"provider": provider, "outcome": "retry"})
                except httpx.TransportError:
                    self.metrics.increment("provider_attempts", {"provider": provider, "outcome": "transport_error"})
                    # Ambiguous Gemini timeouts are not retried: generation may have been billed.
                    if provider in {"gemini", "groq"} or attempt + 1 == self.settings.provider_attempts:
                        raise
                    retry_delay = random.uniform(0.05, 0.15) * (2**attempt)
                except (httpx.HTTPStatusError, ValueError):
                    self.metrics.increment("provider_attempts", {"provider": provider, "outcome": "rejected"})
                    raise
                finally:
                    self.metrics.observe("provider_seconds", monotonic() - started, {"provider": provider})
                await asyncio.sleep(retry_delay)

    def usage(self):
        values = {"ai_turns": 0, "gemini": 0, "groq": 0, "openalex": 0}
        if self.sessions:
            with self.sessions() as session:
                values.update(
                    {
                        row.resource: row.used
                        for row in session.scalars(
                            select(DailyUsage).where(DailyUsage.day == datetime.now(timezone.utc).date())
                        )
                    }
                )
        return {
            "day": datetime.now(timezone.utc).date().isoformat(),
            "used": values,
            "limits": {
                "ai_turns": self.settings.ai_turns_per_day,
                "gemini": self.settings.gemini_requests_per_day,
                "groq": self.settings.groq_requests_per_day,
                "openalex": self.settings.openalex_requests_per_day,
            },
        }


async def request_json(settings, provider, method, url, **kwargs):
    runtime = _runtime.get()
    if runtime:
        return await runtime.request_json(provider, method, url, **kwargs)
    standalone = ProviderRuntime(settings)
    try:
        with standalone.turn():
            return await standalone.request_json(provider, method, url, **kwargs)
    finally:
        await standalone.close()
