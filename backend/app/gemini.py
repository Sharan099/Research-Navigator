"""Gemini response parsing and safe, bounded failure descriptions."""

import json

import httpx
from pydantic import ValidationError

from .providers import ProviderBudgetExceeded
from .telemetry import logger


class GeminiResponseError(ValueError):
    pass


class CitationMismatch(ValueError):
    pass


class LLMFailure(ValueError):
    def __init__(self, provider, error):
        super().__init__("LLM provider failed")
        self.provider, self.error = provider, error


def response_text(payload):
    """Read public text parts only; never treat thought parts as answer JSON."""
    if not isinstance(payload, dict):
        raise GeminiResponseError("invalid_response")
    feedback = payload.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise GeminiResponseError("provider_blocked")
    try:
        candidate = payload["candidates"][0]
        if candidate.get("finishReason") not in (None, "STOP"):
            raise GeminiResponseError("generation_incomplete")
        parts = candidate["content"]["parts"]
        text = "".join(part["text"] for part in parts if not part.get("thought") and isinstance(part.get("text"), str))
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise GeminiResponseError("invalid_response") from exc
    if not text.strip():
        raise GeminiResponseError("invalid_response")
    return text


def failure_details(exc, operation, provider="Gemini"):
    """Do not expose exception messages, URLs, provider bodies, or user text."""
    if isinstance(exc, LLMFailure):
        provider, exc = exc.provider, exc.error
    provider = provider if provider in {"Gemini", "Groq"} else "Gemini"
    status = None
    if isinstance(exc, ProviderBudgetExceeded):
        code, reason = "local_budget_exhausted", "The local daily Gemini budget is exhausted."
        if exc.resource == "ai_turns":
            reason = "The shared daily AI-turn budget is exhausted. No provider can start another AI turn today."
    elif isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        code, reason = {
            400: ("provider_request_rejected", "Gemini rejected the request format or configuration."),
            401: ("provider_auth_error", "Gemini authentication failed. Check the backend API key."),
            403: ("provider_auth_error", "Gemini denied access. Check key permissions and account access."),
            404: ("provider_model_unavailable", "The configured Gemini model is unavailable for this account."),
            429: ("provider_rate_limit", "Gemini rate or account quota was exceeded."),
        }.get(status, ("provider_unavailable", "Gemini returned an unsuccessful HTTP response."))
    elif isinstance(exc, httpx.TimeoutException):
        code, reason = "provider_timeout", "Gemini did not respond within the time limit."
    elif isinstance(exc, httpx.TransportError):
        code, reason = "provider_transport_error", "The backend could not connect securely to Gemini."
    elif isinstance(exc, CitationMismatch):
        code, reason = "citation_mismatch", "Generated citations did not match the supplied abstract evidence."
    elif isinstance(exc, json.JSONDecodeError):
        code, reason = "invalid_json", "Gemini returned invalid JSON."
    elif isinstance(exc, ValidationError):
        code, reason = "invalid_schema", "Gemini output did not match the required data structure."
    elif isinstance(exc, GeminiResponseError):
        code = str(exc)
        reason = {
            "provider_blocked": "Gemini blocked this request.",
            "generation_incomplete": "Gemini did not complete its generated response.",
        }.get(code, "Gemini returned an unexpected response structure.")
    else:
        code, reason = "invalid_response", "Gemini returned an unexpected response structure."
    details = {"code": code, "reason": reason.replace("Gemini", provider), "provider": provider}
    if status is not None:
        details["http_status"] = status
    logger.warning("llm_failure operation=%s provider=%s code=%s http_status=%s", operation, provider, code, status)
    return details
