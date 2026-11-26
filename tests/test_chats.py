"""Saved conversations and real operational events; all paper data is synthetic."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_topics import Papers, paper

from backend.app.chats import ChatInput, build_request, reference_scope
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import ChatMessage, ChatRun, Conversation, Publication


@pytest.fixture
def chat_app(tmp_path):
    provider = Papers([paper(1), paper(2)])
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'chat.db'}", gemini_api_key="", openalex_api_key="")
    app = create_app(settings, provider)
    with TestClient(app) as client:
        yield app, client, provider, settings


def topic(client):
    result = client.post("/api/conversations")
    assert result.status_code == 201
    return result.json()["id"]


def events(response):
    assert response.status_code == 200, response.text
    values = []
    for frame in response.text.split("\n\n"):
        if frame.startswith("event:"):
            name, data = frame.split("\ndata: ", 1)
            values.append((name.removeprefix("event: "), json.loads(data)))
    return values


def send(client, conversation_id, message, **fields):
    payload = {"request_id": str(uuid4()), "message": message, **fields}
    result = events(client.post(f"/api/conversations/{conversation_id}/messages", json=payload))
    return payload, result


def response_result(stream):
    assert stream[-1][0] == "result", stream
    return stream[-1][1]["result"]


def test_chat_search_persists_messages_state_and_real_events(chat_app):
    app, client, provider, _ = chat_app
    conversation_id = topic(client)
    command, stream = send(client, conversation_id, "Find tactile sensor papers")
    result = response_result(stream)
    assert result["status"] == "results"
    assert provider.calls == 1
    activity = [value for name, value in stream if name == "activity"]
    assert [event["id"] for event in activity] == list(range(1, len(activity) + 1))
    assert all(
        event["conversation_id"] == conversation_id and event["request_id"] == command["request_id"]
        for event in activity
    )
    assert {"memory", "planning", "cache", "source_search", "record_validation"} <= {
        event["stage"] for event in activity
    }
    saved = client.get(f"/api/conversations/{conversation_id}").json()
    assert len(saved["messages"]) == 2
    assert saved["state"]["results"] == result["works"]
    assert saved["busy"] is False
    assert saved["runs"][0]["status"] == "completed"
    # Exact duplicate returns saved activity/result, no message or API duplication.
    replay = events(client.post(f"/api/conversations/{conversation_id}/messages", json=command))
    assert response_result(replay) == result
    assert provider.calls == 1
    assert len(client.get(f"/api/conversations/{conversation_id}").json()["messages"]) == 2
    second, _ = send(client, conversation_id, "Explain the second paper")
    saved = client.get(f"/api/conversations/{conversation_id}").json()
    assert [run["id"] for run in saved["runs"]] == [command["request_id"], second["request_id"]]


def test_memory_restores_after_restart_and_keeps_topics_isolated(chat_app):
    app, client, provider, settings = chat_app
    first, second = topic(client), topic(client)
    send(client, first, "Find tactile sensors")
    client.patch(
        f"/api/conversations/{first}",
        json={
            "draft": "My saved draft",
            "title": "Sensors",
            "filters": {"sort": "newest"},
            "selected_work_ids": ["https://openalex.org/W2"],
        },
    )
    with TestClient(create_app(settings, provider)) as restarted:
        saved = restarted.get(f"/api/conversations/{first}").json()
        assert saved["title"] == "Sensors" and saved["draft"] == "My saved draft"
        assert saved["state"]["filters"]["sort"] == "newest"
        assert saved["state"]["selected_work_ids"] == ["https://openalex.org/W2"]
        assert restarted.get(f"/api/conversations/{second}").json()["messages"] == []
        _, stream = send(restarted, first, "Explain the second paper")
        answer = response_result(stream)
        assert [source["id"] for source in answer["sources"]] == ["https://openalex.org/W2"]
        assert answer["answer_mode"] == "evidence_only"


def test_reference_clarification_stays_in_same_chat(chat_app):
    _, client, _, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, stream = send(client, conversation_id, "Explain this paper")
    assert response_result(stream)["status"] == "clarification"
    _, stream = send(client, conversation_id, "2")
    assert response_result(stream)["sources"][0]["id"] == "https://openalex.org/W2"
    assert client.get(f"/api/conversations/{conversation_id}").json()["state"]["pending"] is None


def test_details_are_local_and_inline_and_previous_papers_remain_referenceable(chat_app):
    app, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    provider.records = [paper(3)]
    send(client, conversation_id, "Find robot sensors", refresh=True)
    _, stream = send(client, conversation_id, "Show details for https://openalex.org/W2")
    detail = response_result(stream)
    assert detail["status"] == "details" and detail["work"]["id"] == "https://openalex.org/W2"
    assert provider.calls == 2
    _, stream = send(client, conversation_id, "Explain https://openalex.org/W2")
    assert response_result(stream)["sources"][0]["id"] == "https://openalex.org/W2"


def test_delete_cascades_chat_records_but_preserves_publications(chat_app):
    app, client, _, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 204
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    with app.state.session_factory() as session:
        assert session.scalars(select(ChatMessage)).all() == []
        assert session.scalars(select(ChatRun)).all() == []
        assert len(session.scalars(select(Publication)).all()) == 2


def test_active_request_and_duplicate_id_guards(chat_app):
    app, client, provider, _ = chat_app
    first, second = topic(client), topic(client)
    command = ChatInput(request_id=str(uuid4()), message="Find tactile sensors")
    assert app.state.chats.reserve(first, command)
    for cid, payload in [
        (first, command.model_dump()),
        (first, {"request_id": str(uuid4()), "message": "Find robots"}),
        (second, command.model_dump()),
    ]:
        assert client.post(f"/api/conversations/{cid}/messages", json=payload).status_code == 409
    assert client.delete(f"/api/conversations/{first}").status_code == 409
    assert client.patch(f"/api/conversations/{first}", json={"filters": {"sort": "newest"}}).status_code == 409
    assert provider.calls == 0


def test_restart_marks_abandoned_run_interrupted(chat_app):
    app, client, provider, settings = chat_app
    conversation_id = topic(client)
    app.state.chats.reserve(conversation_id, ChatInput(request_id=str(uuid4()), message="Find sensors"))
    with TestClient(create_app(settings, provider)) as restarted:
        saved = restarted.get(f"/api/conversations/{conversation_id}").json()
        assert not saved["busy"]
        assert saved["runs"][0]["status"] == "interrupted"
        assert saved["runs"][0]["events"][-1]["status"] == "interrupted"


def test_stream_cancel_persists_interruption_and_releases_topic(chat_app):
    app, client, _, _ = chat_app
    conversation_id = topic(client)
    command = ChatInput(request_id=str(uuid4()), message="Find sensors")
    app.state.chats.reserve(conversation_id, command)

    async def disconnect():
        stream = app.state.chats.stream(conversation_id, command, True)
        assert "event: activity" in await anext(stream)
        await stream.aclose()

    asyncio.run(disconnect())
    saved = client.get(f"/api/conversations/{conversation_id}").json()
    assert not saved["busy"]
    assert saved["runs"][0]["status"] == "interrupted"


def test_untrusted_scope_and_context_are_rejected(chat_app):
    _, client, _, _ = chat_app
    conversation_id = topic(client)
    _, stream = send(client, conversation_id, "Explain this", selected_work_ids=["https://openalex.org/W99"])
    assert stream[-1][0] == "error"
    assert "already shown" in stream[-1][1]["message"]
    for fields in ({"history": []}, {"previous_query": {"query": "robots"}}, {"request_id": "not-a-uuid"}):
        payload = {"request_id": str(uuid4()), "message": "Find sensors", **fields}
        assert client.post(f"/api/conversations/{conversation_id}/messages", json=payload).status_code == 422


def test_history_is_bounded_and_old_titles_resolve_without_sending_entire_chat(chat_app):
    app, client, _, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    with app.state.session_factory() as session:
        for _ in range(15):
            session.add(
                ChatMessage(
                    conversation_id=conversation_id,
                    request_id=str(uuid4()),
                    role="user",
                    text="Synthetic context " * 100,
                )
            )
        session.commit()
        conversation = session.get(Conversation, conversation_id)
        from backend.app.chats import messages

        prepared, clarification, _ = build_request(
            ChatInput(request_id=str(uuid4()), message="Explain https://openalex.org/W1"),
            conversation,
            messages(session, conversation_id),
        )
        request, _ = prepared
        assert clarification is None
        assert len(request.history) == 8
        assert all(len(turn.content) <= 1000 for turn in request.history)
        assert request.selected_work_ids == ["https://openalex.org/W1"]


def test_cache_and_upstream_failure_activity_match_actual_path(chat_app):
    app, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, cached = send(client, conversation_id, "Refresh topic filters", action="search", reuse_query=True)
    assert response_result(cached)["retrieval_source"] == "local_cache"
    assert not any(value.get("stage") == "source_search" for name, value in cached if name == "activity")
    counters = app.state.metrics.render()
    assert 'research_cache_total{decision="miss"} 1.0' in counters
    assert 'research_cache_total{decision="hit"} 1.0' in counters
    provider.fail = True
    _, failed = send(client, conversation_id, "Refresh topic sources", action="search", reuse_query=True, refresh=True)
    assert response_result(failed)["retrieval_source"] == "local_fallback"
    assert 'research_cache_total{decision="refresh"} 1.0' in app.state.metrics.render()
    assert any(
        value.get("stage") == "source_search" and value["status"] == "failed"
        for name, value in failed
        if name == "activity"
    )


def test_chat_sort_tool_preserves_topic_and_explicit_controls_win(chat_app):
    _, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, stream = send(client, conversation_id, "Sort these by most cited")
    sorted_result = response_result(stream)
    assert sorted_result["parser_mode"] == "reused"
    assert sorted_result["query"]["query"] == "tactile sensors"
    assert sorted_result["query"]["sort"] == "most_cited"
    _, stream = send(client, conversation_id, "Sort these by most cited", filters={"sort": "newest"})
    assert response_result(stream)["query"]["sort"] == "newest"


def test_conversational_date_filter_preserves_topic(chat_app):
    _, client, _, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, stream = send(client, conversation_id, "Only the last 30 days")
    result = response_result(stream)
    assert result["query"]["query"] == "tactile sensors"
    assert result["query"]["date_from"] and result["query"]["date_to"]


def test_evidence_failure_events_and_no_abstract_path_are_honest(chat_app, mock_http):
    app, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    app.state.search_service.answerer.settings = Settings(gemini_api_key="synthetic-test-key", openalex_api_key="")
    mock_http(status=503, payload={})
    _, stream = send(client, conversation_id, "Explain the second paper")
    assert response_result(stream)["answer_mode"] == "evidence_only"
    assert any(
        event.get("stage") == "explanation"
        and event.get("details", {}).get("code") == "provider_unavailable"
        and event.get("details", {}).get("http_status") == 503
        for name, event in stream
        if name == "activity"
    )
    assert any(
        event.get("stage") == "explanation" and event["status"] == "failed"
        for name, event in stream
        if name == "activity"
    )
    provider.records = [paper(3, abstract=False)]
    send(client, conversation_id, "Find robot sensors", refresh=True)
    _, stream = send(client, conversation_id, "Explain this paper")
    assert response_result(stream)["answer_mode"] == "insufficient_evidence"
    assert any(
        event.get("stage") == "citation_checks" and event["status"] == "skipped"
        for name, event in stream
        if name == "activity"
    )


def test_selected_paper_does_not_force_discovery_into_an_explanation(chat_app):
    _, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, stream = send(
        client, conversation_id, "Find more papers on robotic sensors", selected_work_ids=["https://openalex.org/W1"]
    )
    assert response_result(stream)["status"] == "results"
    assert provider.calls == 2


def test_chat_refresh_bypasses_cache_without_changing_topic(chat_app):
    _, client, provider, _ = chat_app
    conversation_id = topic(client)
    send(client, conversation_id, "Find tactile sensors")
    _, stream = send(client, conversation_id, "Refresh sources")
    result = response_result(stream)
    assert result["query"]["query"] == "tactile sensors"
    assert result["parser_mode"] == "reused"
    assert provider.calls == 2
    assert any(event.get("details", {}).get("decision") == "refresh" for name, event in stream if name == "activity")


def test_disconnect_after_commit_preserves_completed_answer(chat_app):
    app, client, _, _ = chat_app
    conversation_id = topic(client)
    command = ChatInput(request_id=str(uuid4()), message="Find tactile sensors")
    app.state.chats.reserve(conversation_id, command)

    async def disconnect_at_completion():
        stream = app.state.chats.stream(conversation_id, command, True)
        async for frame in stream:
            data = json.loads(frame.split("\ndata: ", 1)[1])
            if data.get("stage") == "request" and data["status"] == "completed":
                await stream.aclose()
                break

    asyncio.run(disconnect_at_completion())
    saved = client.get(f"/api/conversations/{conversation_id}").json()
    assert saved["runs"][0]["status"] == "completed"
    assert saved["messages"][-1]["result"]["status"] == "results"
    assert len(saved["messages"]) == 2


def test_failed_operation_hides_internal_exception_and_releases_topic(chat_app):
    app, client, _, _ = chat_app
    conversation_id = topic(client)

    async def fail(*args, **kwargs):
        raise RuntimeError("Synthetic secret must not enter the response")

    app.state.search_service.search = fail
    _, stream = send(client, conversation_id, "Find tactile sensors")
    assert stream[-1][0] == "error"
    assert "Synthetic secret" not in json.dumps(stream)
    saved = client.get(f"/api/conversations/{conversation_id}").json()
    assert not saved["busy"]
    assert saved["runs"][0]["status"] == "failed"


def test_duplicate_titles_require_identity_and_short_ordinals_resolve():
    current = [
        {"id": "https://openalex.org/W1", "title": "Shared synthetic title"},
        {"id": "https://openalex.org/W2", "title": "Shared synthetic title"},
    ]
    known = {work["id"]: work for work in current}
    selected, question = reference_scope("Explain Shared synthetic title", current, known)
    assert not selected and question
    assert reference_scope("Compare first and second", current, known) == ([work["id"] for work in current], None)


@pytest.mark.parametrize(
    "text,expected,error",
    [
        ("Explain the second paper", ["https://openalex.org/W2"], False),
        ("Compare first and second papers", ["https://openalex.org/W1", "https://openalex.org/W2"], False),
        ("Explain paper 9", [], True),
        ("Explain this paper", [], True),
        ("Explain the previous second paper", [], True),
        ("Explain Synthetic long title", ["https://openalex.org/W1"], False),
    ],
)
def test_reference_rules(text, expected, error):
    current = [
        {"id": "https://openalex.org/W1", "title": "Synthetic long title"},
        {"id": "https://openalex.org/W2", "title": "Other synthetic title"},
    ]
    selected, question = reference_scope(text, current, {work["id"]: work for work in current})
    assert selected == expected
    assert bool(question) == error
