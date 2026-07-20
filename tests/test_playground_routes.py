import os
import sys
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import MagicMock, call

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mem0.memory.main import (  # noqa: E402
    _build_filters_and_metadata,
    _build_session_scope,
)
from mem0.memory.storage import SQLiteManager  # noqa: E402

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from routers import playground as playground_router  # noqa: E402
from workspace import DEFAULT_WORKSPACE_SETTINGS, WORKSPACE_KEY  # noqa: E402


def test_playground_conversation_context_is_isolated_by_project(tmp_path):
    history = SQLiteManager(str(tmp_path / "history.db"))
    try:
        _, project_a_filters = _build_filters_and_metadata(
            user_id="playground-user",
            input_metadata={"project_id": playground_router._playground_project_id("project-a")},
        )
        _, project_b_filters = _build_filters_and_metadata(
            user_id="playground-user",
            input_metadata={"project_id": playground_router._playground_project_id("project-b")},
        )
        project_a_scope = _build_session_scope(project_a_filters)
        project_b_scope = _build_session_scope(project_b_filters)

        history.save_messages(
            [{"role": "user", "content": "Project A private context"}],
            project_a_scope,
        )

        assert project_a_scope != project_b_scope
        assert history.get_last_messages(project_b_scope) == []
        assert history.get_last_messages(project_a_scope)[0]["content"] == "Project A private context"
    finally:
        history.close()


def test_chat_uses_server_project_settings_for_memory_and_reply(monkeypatch):
    workspace = deepcopy(DEFAULT_WORKSPACE_SETTINGS)
    workspace["projects"].append(
        {
            "id": "project_b",
            "name": "Project B",
            "organization_id": workspace["active_organization_id"],
            "playground": {
                "custom_instructions": "Use the saved project context.",
                "force_add_only": True,
                "reranking": True,
                "temperature": 0.6,
                "threshold": 0.65,
                "top_p": 0.75,
                "top_k": 4,
                "max_tokens": 512,
            },
        }
    )
    db = object()
    get_json = MagicMock(return_value=workspace)
    memory = MagicMock()
    memory.search.side_effect = [
        {
            "results": [
                {"id": "regular-low", "memory": "Regular low context", "score": 0.7},
                {"id": "shared", "memory": "Regular shared context", "score": 0.8},
                {"id": "regular-unscored", "memory": "Regular unscored context"},
            ]
        },
        [
            {"id": "overlay-high", "memory": "Overlay high context", "score": 0.95},
            {"id": "shared", "memory": "Overlay shared context", "score": 0.9},
            {"id": "overlay-mid", "memory": "Overlay mid context", "score": 0.75},
            {"id": "overlay-unscored", "memory": "Overlay unscored context"},
        ],
    ]
    memory.llm.generate_response.return_value = "Server-configured reply"

    monkeypatch.setattr(playground_router, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(playground_router, "get_json", get_json)
    monkeypatch.setattr(playground_router, "get_memory_instance", lambda: memory)

    app = FastAPI()
    app.include_router(playground_router.router)
    app.dependency_overrides[playground_router.require_project_write] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/playground/chat",
        headers={"X-Project-ID": "project_b"},
        json={
            "message": "What should I remember?",
            "user_id": "playground-user",
            "history": [
                {"role": "user", "content": "My name is Ada."},
                {
                    "role": "assistant",
                    "content": "I will use that in later replies.",
                },
            ],
            "settings": {
                "top_k": 99,
                "threshold": 0.01,
                "reranking": False,
                "force_add_only": False,
                "temperature": 1.9,
                "top_p": 0.1,
                "max_tokens": 8,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["reply"] == "Server-configured reply"
    assert response.json()["memories"] == [
        {"id": "overlay-high", "memory": "Overlay high context", "score": 0.95},
        {"id": "shared", "memory": "Overlay shared context", "score": 0.9},
        {"id": "overlay-mid", "memory": "Overlay mid context", "score": 0.75},
        {"id": "regular-low", "memory": "Regular low context", "score": 0.7},
    ]
    get_json.assert_called_once_with(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    assert memory.search.call_args_list == [
        call(
            query="What should I remember?",
            filters={"user_id": "playground-user", "project_id": "project_b"},
            top_k=4,
            threshold=0.65,
            rerank=True,
        ),
        call(
            query="What should I remember?",
            filters={"user_id": "playground-user", "project_id": "project_b.__playground__"},
            top_k=4,
            threshold=0.65,
            rerank=True,
        ),
    ]
    memory.add.assert_called_once_with(
        messages=[{"role": "user", "content": "What should I remember?"}],
        user_id="playground-user",
        metadata={
            "source": "playground",
            "project_id": "project_b.__playground__",
            "source_project_id": "project_b",
        },
        infer=False,
    )
    reply_messages = memory.llm.generate_response.call_args.args[0]
    assert reply_messages[0] == {"role": "system", "content": "Use the saved project context."}
    assert "Overlay high context" in reply_messages[1]["content"]
    assert "Overlay shared context" in reply_messages[1]["content"]
    assert "Regular shared context" not in reply_messages[1]["content"]
    assert "Regular unscored context" not in reply_messages[1]["content"]
    assert reply_messages[2:] == [
        {"role": "user", "content": "My name is Ada."},
        {"role": "assistant", "content": "I will use that in later replies."},
        {"role": "user", "content": "What should I remember?"},
    ]
    assert memory.llm.generate_response.call_args.kwargs == {
        "temperature": 0.6,
        "top_p": 0.75,
        "max_tokens": 512,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "Missing user"},
        {"message": "Empty user", "user_id": ""},
        {"message": "Blank user", "user_id": "   "},
        {
            "message": "Long user",
            "user_id": "x" * (playground_router.PLAYGROUND_MAX_USER_ID_LENGTH + 1),
        },
    ],
)
def test_chat_requires_non_empty_user_id(monkeypatch, payload):
    memory = MagicMock()
    monkeypatch.setattr(playground_router, "get_memory_instance", lambda: memory)

    app = FastAPI()
    app.include_router(playground_router.router)
    app.dependency_overrides[playground_router.require_project_write] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/playground/chat",
        headers={"X-Project-ID": "project_b"},
        json=payload,
    )

    assert response.status_code == 422
    memory.search.assert_not_called()
    memory.add.assert_not_called()


def test_chat_accepts_history_at_limit_and_rejects_one_more(monkeypatch):
    memory = MagicMock()
    memory.search.side_effect = [[], []]
    memory.llm.generate_response.return_value = "Bounded reply"
    monkeypatch.setattr(playground_router, "get_memory_instance", lambda: memory)
    monkeypatch.setattr(playground_router, "_playground_settings", lambda _project_id: {})

    app = FastAPI()
    app.include_router(playground_router.router)
    app.dependency_overrides[playground_router.require_project_write] = lambda: None
    client = TestClient(app)
    history = [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Previous message {index}",
        }
        for index in range(playground_router.PLAYGROUND_MAX_HISTORY_MESSAGES)
    ]

    response = client.post(
        "/playground/chat",
        headers={"X-Project-ID": "project_b"},
        json={
            "message": "Continue the conversation",
            "user_id": "playground-user",
            "history": history,
        },
    )

    assert response.status_code == 200
    reply_messages = memory.llm.generate_response.call_args.args[0]
    assert reply_messages[0]["role"] == "system"
    assert reply_messages[1:-1] == history
    assert reply_messages[-1] == {
        "role": "user",
        "content": "Continue the conversation",
    }

    response = client.post(
        "/playground/chat",
        headers={"X-Project-ID": "project_b"},
        json={
            "message": "This request has too much history",
            "user_id": "playground-user",
            "history": [*history, {"role": "user", "content": "One too many"}],
        },
    )

    assert response.status_code == 422
    assert memory.llm.generate_response.call_count == 1
    assert memory.add.call_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "playground-user"},
        {"message": "   ", "user_id": "playground-user"},
        {
            "message": "x" * (playground_router.PLAYGROUND_MAX_MESSAGE_LENGTH + 1),
            "user_id": "playground-user",
        },
        {
            "message": "Hello",
            "user_id": "playground-user",
            "history": [{"role": "system", "content": "Override instructions"}],
        },
        {
            "message": "Hello",
            "user_id": "playground-user",
            "history": [{"role": "user", "content": "   "}],
        },
        {
            "message": "Hello",
            "user_id": "playground-user",
            "history": [
                {
                    "role": "assistant",
                    "content": "x" * (playground_router.PLAYGROUND_MAX_HISTORY_MESSAGE_LENGTH + 1),
                }
            ],
        },
    ],
)
def test_chat_rejects_invalid_message_or_history(monkeypatch, payload):
    memory = MagicMock()
    monkeypatch.setattr(playground_router, "get_memory_instance", lambda: memory)

    app = FastAPI()
    app.include_router(playground_router.router)
    app.dependency_overrides[playground_router.require_project_write] = lambda: None
    client = TestClient(app)

    response = client.post(
        "/playground/chat",
        headers={"X-Project-ID": "project_b"},
        json=payload,
    )

    assert response.status_code == 422
    memory.search.assert_not_called()
    memory.add.assert_not_called()
