"""Synthetic provider envelopes and diagnostic redaction; no network or credentials."""

import httpx
import pytest

from backend.app.gemini import GeminiResponseError, failure_details, response_text


def test_response_parts_exclude_thoughts_and_join_public_text():
    payload = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {"thought": True, "text": "private reasoning"},
                        {"text": '{"claims":'},
                        {"text": "[]}"},
                    ]
                },
            }
        ]
    }
    assert response_text(payload) == '{"claims":[]}'


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "provider_blocked"),
        ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "generation_incomplete"),
        ({"candidates": None}, "invalid_response"),
        ({"candidates": [{"content": {"parts": []}}]}, "invalid_response"),
    ],
)
def test_unusable_output_is_rejected(payload, code):
    with pytest.raises(GeminiResponseError, match=code):
        response_text(payload)


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "provider_request_rejected"),
        (401, "provider_auth_error"),
        (403, "provider_auth_error"),
        (404, "provider_model_unavailable"),
        (429, "provider_rate_limit"),
        (503, "provider_unavailable"),
    ],
)
def test_diagnostics_never_echo_provider_body_url_or_exception(status, code, caplog):
    secret = "synthetic-sensitive-value"
    request = httpx.Request("POST", f"https://example.test/?key={secret}")
    response = httpx.Response(status, request=request, text=secret)
    error = httpx.HTTPStatusError(secret, request=request, response=response)
    details = failure_details(error, "answer")
    assert details["code"] == code and details["http_status"] == status
    assert secret not in str(details) + caplog.text
