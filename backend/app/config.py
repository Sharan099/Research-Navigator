"""Validated server configuration, evaluated when an application is constructed."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def environment(name: str, default: str = "") -> str:
    load_dotenv()
    return os.getenv(name, default)


def number(name: str, default: int) -> int:
    try:
        return int(environment(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    app_mode: str = field(default_factory=lambda: environment("APP_MODE", "local"))
    public_host: str = field(default_factory=lambda: environment("PUBLIC_HOST"))
    session_secret: str = field(default_factory=lambda: environment("SESSION_SECRET"), repr=False)
    database_url: str = field(
        default_factory=lambda: environment("DATABASE_URL", "sqlite:///./research_navigator.db"), repr=False
    )
    openalex_api_key: str = field(default_factory=lambda: environment("OPENALEX_API_KEY"), repr=False)
    gemini_api_key: str = field(default_factory=lambda: environment("GEMINI_API_KEY"), repr=False)
    gemini_model: str = field(default_factory=lambda: environment("GEMINI_MODEL", "gemini-3.5-flash"))
    groq_api_key: str = field(default_factory=lambda: environment("GROQ_API_KEY"), repr=False)
    groq_model: str = field(default_factory=lambda: environment("GROQ_MODEL", "openai/gpt-oss-120b,openai/gpt-oss-20b"))
    groq_requests_per_day: int = field(default_factory=lambda: number("GROQ_REQUESTS_PER_DAY", 6))
    cache_ttl_hours: int = field(default_factory=lambda: number("CACHE_TTL_HOURS", 24))
    ai_turns_per_day: int = field(default_factory=lambda: number("AI_TURNS_PER_DAY", 3))
    gemini_requests_per_day: int = field(default_factory=lambda: number("GEMINI_REQUESTS_PER_DAY", 6))
    openalex_requests_per_day: int = field(default_factory=lambda: number("OPENALEX_REQUESTS_PER_DAY", 20))
    provider_concurrency: int = field(default_factory=lambda: number("PROVIDER_CONCURRENCY", 2))
    max_active_requests: int = field(default_factory=lambda: number("MAX_ACTIVE_REQUESTS", 5))
    provider_attempts: int = field(default_factory=lambda: number("PROVIDER_ATTEMPTS", 2))
    provider_timeout_seconds: int = field(default_factory=lambda: number("PROVIDER_TIMEOUT_SECONDS", 20))
    request_timeout_seconds: int = field(default_factory=lambda: number("REQUEST_TIMEOUT_SECONDS", 60))
    max_response_bytes: int = field(default_factory=lambda: number("MAX_RESPONSE_BYTES", 4_000_000))
    chat_retention_days: int = field(default_factory=lambda: number("CHAT_RETENTION_DAYS", 30))

    @property
    def groq_models(self) -> tuple[str, ...]:
        """Ordered Groq models; the next is tried when one is rate-limited or unavailable."""
        return tuple(model.strip() for model in self.groq_model.split(",") if model.strip())

    def __post_init__(self):
        if self.app_mode not in {"local", "public_demo"}:
            raise ValueError("APP_MODE must be local or public_demo")
        if self.app_mode == "public_demo":
            import re

            if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)+", self.public_host):
                raise ValueError("PUBLIC_HOST must be an exact lowercase hostname")
            if len(self.session_secret) < 32:
                raise ValueError("SESSION_SECRET must contain at least 32 characters")
        if not self.database_url.startswith(("sqlite:", "postgresql+psycopg://")):
            raise ValueError("DATABASE_URL must use SQLite or PostgreSQL with psycopg")
        if not self.gemini_model or not all(char.isalnum() or char in "-_." for char in self.gemini_model):
            raise ValueError("GEMINI_MODEL must be a model identifier")
        if not self.groq_models or not all(
            all(char.isalnum() or char in "-_./" for char in model) for model in self.groq_models
        ):
            raise ValueError("GROQ_MODEL must be a comma-separated list of model identifiers")
        if self.groq_requests_per_day < 0:
            raise ValueError("GROQ_REQUESTS_PER_DAY must not be negative")
        for name in ("cache_ttl_hours", "ai_turns_per_day", "gemini_requests_per_day", "openalex_requests_per_day"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name.upper()} must not be negative")
        for name in (
            "provider_concurrency",
            "max_active_requests",
            "provider_attempts",
            "provider_timeout_seconds",
            "request_timeout_seconds",
            "max_response_bytes",
            "chat_retention_days",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be positive")
        if self.provider_attempts > 3:
            raise ValueError("PROVIDER_ATTEMPTS must not exceed 3")
