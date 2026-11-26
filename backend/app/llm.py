"""Gemini first; Groq only for quota exhaustion or missing Gemini configuration."""

import json

import httpx

from .gemini import GeminiResponseError, LLMFailure, response_text
from .providers import ProviderBudgetExceeded, request_json


async def generate_json(settings, payload, *, emit=None):
    warnings = []
    if settings.gemini_api_key:
        try:
            result = await request_json(
                settings,
                "gemini",
                "POST",
                f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key},
                json=payload,
            )
            return response_text(result), "gemini", warnings
        except (ProviderBudgetExceeded, httpx.HTTPStatusError) as exc:
            quota = (
                isinstance(exc, ProviderBudgetExceeded)
                and exc.resource in {None, "gemini"}
                or isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code == 429
            )
            if not quota or not settings.groq_api_key:
                raise
            warnings.append("Gemini quota is exhausted; used Groq instead.")
            if emit:
                await emit(
                    "provider_fallback", "completed", {"from": "Gemini", "to": "Groq", "reason": "quota_exhausted"}
                )
    else:
        warnings.append("Gemini is not configured; used Groq instead.")
    schema = payload["generationConfig"]["responseJsonSchema"]
    instructions = payload["systemInstruction"]["parts"][0]["text"]
    user = payload["contents"][0]["parts"][0]["text"]
    try:
        result = await request_json(
            settings,
            "groq",
            "POST",
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            json={
                "model": settings.groq_model,
                "messages": [
                    {
                        "role": "system",
                        "content": instructions + "\nReturn JSON matching this schema:\n" + json.dumps(schema),
                    },
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 2048,
            },
        )
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise GeminiResponseError("generation_incomplete")
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise GeminiResponseError("invalid_response")
        return content, "groq", warnings
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise LLMFailure("Groq", exc) from exc
