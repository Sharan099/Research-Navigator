"""Gemini first; Groq's free models take over when Gemini is missing or fails.

The shared AI-turn cap is the one failure that never switches: it limits every provider.
"""

import json

import httpx

from .gemini import GeminiResponseError, LLMFailure, failure_details, response_text
from .providers import ProviderBudgetExceeded, request_json

# Groq statuses that mean "this model is unavailable right now", so the next free model may work.
NEXT_GROQ_MODEL = {404, 408, 429, 500, 502, 503, 504}


def shared_cap(exc):
    return isinstance(exc, ProviderBudgetExceeded) and exc.resource == "ai_turns"


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
        except (httpx.HTTPError, GeminiResponseError) as exc:
            if shared_cap(exc) or not settings.groq_api_key:
                raise
            code = failure_details(exc, "fallback")["code"]
            warnings.append(f"Gemini failed ({code}); used Groq instead.")
            if emit:
                await emit("provider_fallback", "completed", {"from": "Gemini", "to": "Groq", "reason": code})
    else:
        warnings.append("Gemini is not configured; used Groq instead.")
    schema = payload["generationConfig"]["responseJsonSchema"]
    instructions = payload["systemInstruction"]["parts"][0]["text"]
    user = payload["contents"][0]["parts"][0]["text"]
    models = settings.groq_models
    for index, model in enumerate(models):
        try:
            result = await request_json(
                settings,
                "groq",
                "POST",
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": instructions + "\nReturn JSON matching this schema:\n" + json.dumps(schema),
                        },
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_completion_tokens": 4096,
                },
            )
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise GeminiResponseError("generation_incomplete")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise GeminiResponseError("invalid_response")
            return content, "groq", warnings
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in NEXT_GROQ_MODEL and index + 1 < len(models):
                warnings.append(
                    f"Groq model {model} returned HTTP {exc.response.status_code}; tried {models[index + 1]}."
                )
                continue
            raise LLMFailure("Groq", exc) from exc
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise LLMFailure("Groq", exc) from exc
