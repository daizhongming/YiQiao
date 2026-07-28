import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from starlette.requests import Request

SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from routers import memories  # noqa: E402


def _memory(
    memory_id: str,
    created_at: str | None,
    *,
    user_id: str = "alice",
    categories: list[str] | None = None,
    metadata: dict | None = None,
    content: str | None = None,
):
    return {
        "id": memory_id,
        "memory": content or f"memory {memory_id}",
        "project_id": "default-project",
        "user_id": user_id,
        "agent_id": None,
        "app_id": None,
        "run_id": None,
        "categories": categories or [],
        "metadata": metadata or {},
        "created_at": created_at,
        "updated_at": created_at,
    }


def _request(project_id: str = "default-project") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/memories/query",
            "headers": [(b"x-project-id", project_id.encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def test_query_rows_sorts_paginates_and_builds_category_facets():
    rows = [
        _memory("old", "2026-01-01T00:00:00Z", categories=["health"]),
        _memory("new", "2026-01-05T00:00:00Z", categories=["technology"]),
        _memory("middle", "2026-01-03T00:00:00Z", categories=["technology", "health"]),
        _memory("newer", "2026-01-04T00:00:00Z", categories=["technology"]),
        _memory("older", "2026-01-02T00:00:00Z", categories=[]),
    ]

    result = memories._query_rows(rows, memories.MemoryQuery(page=2, page_size=2))

    assert [item["id"] for item in result["results"]] == ["middle", "older"]
    assert result["total"] == 5
    assert result["total_pages"] == 3
    assert result["facets"] == {
        "total": 5,
        "categories": [
            {"name": "technology", "count": 3},
            {"name": "health", "count": 2},
        ],
    }


def test_query_rows_supports_all_and_any_with_nested_metadata():
    rows = [
        _memory("both", "2026-01-03T00:00:00Z", metadata={"profile": {"tier": "gold"}}),
        _memory("entity", "2026-01-02T00:00:00Z", metadata={"profile": {"tier": "free"}}),
        _memory(
            "metadata",
            "2026-01-01T00:00:00Z",
            user_id="bob",
            metadata={"profile": {"tier": "gold"}},
        ),
    ]
    filters = [
        memories.MemoryFilter(field="entity", entity_type="user", value="ALICE"),
        memories.MemoryFilter(field="metadata", key="profile.tier", value="gold"),
    ]

    all_result = memories._query_rows(rows, memories.MemoryQuery(match="all", filters=filters))
    any_result = memories._query_rows(rows, memories.MemoryQuery(match="any", filters=filters))

    assert [item["id"] for item in all_result["results"]] == ["both"]
    assert [item["id"] for item in any_result["results"]] == ["both", "entity", "metadata"]


def test_query_rows_date_boundaries_are_inclusive_and_category_is_separate():
    rows = [
        _memory("before", "2026-01-09T23:59:59Z", categories=["technology"]),
        _memory("start", "2026-01-10T00:00:00Z", categories=["technology"]),
        _memory("end", "2026-01-12T23:59:59Z", categories=["health"]),
        _memory("after", "2026-01-13T00:00:00Z", categories=["technology"]),
        _memory("undated", None, categories=["technology"]),
    ]
    query = memories.MemoryQuery(
        start_date="2026-01-10",
        end_date="2026-01-12",
        category="technology",
    )

    result = memories._query_rows(rows, query)

    assert [item["id"] for item in result["results"]] == ["start"]
    assert result["facets"]["total"] == 2
    assert result["facets"]["categories"] == [
        {"name": "health", "count": 1},
        {"name": "technology", "count": 1},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"field": "entity", "value": "alice"},
        {"field": "metadata", "value": "gold"},
        {"field": "memory_id", "value": "  "},
    ],
)
def test_memory_filter_rejects_incomplete_conditions(payload):
    with pytest.raises(ValidationError):
        memories.MemoryFilter.model_validate(payload)


def test_query_endpoint_sets_request_log_context(monkeypatch):
    rows = [_memory("one", "2026-01-01T00:00:00Z")]
    monkeypatch.setattr(memories, "_all_project_memories", lambda _project_id: rows)
    request = _request()
    body = memories.MemoryQuery(filters=[memories.MemoryFilter(field="entity", entity_type="user", value="alice")])

    response = memories.query_memories(body, request, None)

    assert response["total"] == 1
    assert request.state.request_log_event_type == "GET_ALL"
    assert request.state.request_log_entities == {"user_id": "alice"}
    assert request.state.request_log_result_count == 1


def test_details_returns_source_history_and_saved_feedback(monkeypatch):
    class FakeHistory:
        def get_last_messages(self, scope, limit):
            assert scope == "project_id=default-project&user_id=alice"
            assert limit == 10
            return [{"role": "user", "content": "I like hiking"}]

    instance = SimpleNamespace(
        get=lambda _memory_id: _memory("one", "2026-01-01T00:00:00Z"),
        history=lambda memory_id: [{"memory_id": memory_id, "event": "ADD"}],
        db=FakeHistory(),
    )
    monkeypatch.setattr(memories, "get_memory_instance", lambda: instance)
    monkeypatch.setattr(
        memories,
        "get_json",
        lambda _db, _key, _default: {"rating": "positive", "feedback": "Accurate"},
    )

    response = memories.memory_details("one", _request(), None, SimpleNamespace())

    assert response["memory"]["id"] == "one"
    assert response["source"][0]["content"] == "I like hiking"
    assert response["history"] == [{"memory_id": "one", "event": "ADD"}]
    assert response["feedback"]["rating"] == "positive"


def test_save_feedback_is_project_scoped(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        memories,
        "_get_project_memory",
        lambda memory_id, project_id: _memory(memory_id, "2026-01-01T00:00:00Z"),
    )
    monkeypatch.setattr(
        memories,
        "set_json",
        lambda _db, key, value: saved.update({"key": key, "value": value}),
    )

    response = memories.save_memory_feedback(
        "one",
        memories.MemoryFeedbackUpdate(
            rating="negative",
            reason="Conflicting memories detected",
            feedback="Needs review",
        ),
        _request("project-a"),
        None,
        SimpleNamespace(),
    )

    assert saved["key"] == "memory_feedback:project-a:one"
    assert saved["value"]["project_id"] == "project-a"
    assert response["feedback"]["rating"] == "negative"


def test_delete_memory_feedback_removes_project_scoped_record(monkeypatch):
    record = SimpleNamespace(key="memory_feedback:project-a:one")

    class FakeSession:
        deleted = None
        committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, _model, key):
            assert key == "memory_feedback:project-a:one"
            return record

        def delete(self, value):
            self.deleted = value

        def commit(self):
            self.committed = True

    session = FakeSession()
    monkeypatch.setattr(memories, "SessionLocal", lambda: session)

    assert memories.delete_memory_feedback("project-a", "one") is True
    assert session.deleted is record
    assert session.committed is True


def test_duplicate_groups_normalize_text_but_preserve_entity_scope():
    rows = [
        _memory("old", "2026-01-01T00:00:00Z", content="Apply\u00a0now"),
        _memory("new", "2026-01-02T00:00:00Z", content="Apply   now"),
        _memory("other-user", "2026-01-03T00:00:00Z", user_id="bob", content="Apply now"),
        _memory("different", "2026-01-04T00:00:00Z", content="Apply later"),
    ]

    groups = memories._duplicate_memory_groups(rows)

    assert [[row["id"] for row in group] for group in groups] == [["old", "new"]]


def test_deduplicate_endpoint_keeps_oldest_and_cleans_related_state(monkeypatch):
    rows = [
        _memory("old", "2026-01-01T00:00:00Z", categories=["jobs"], content="Apply now"),
        _memory("new", "2026-01-02T00:00:00Z", categories=["outreach"], content="Apply  now"),
        _memory("unique", "2026-01-03T00:00:00Z", content="Interview tomorrow"),
    ]
    instance = SimpleNamespace(delete=MagicMock(), update=MagicMock())
    deleted_graph = []
    webhook_events = []
    monkeypatch.setattr(memories, "_all_project_memories", lambda _project_id: rows)
    monkeypatch.setattr(memories, "get_memory_instance", lambda: instance)
    monkeypatch.setattr(memories, "delete_memory_feedback", lambda project_id, memory_id: True)
    monkeypatch.setattr(memories, "delete_graph_memory", deleted_graph.append)
    monkeypatch.setattr(
        memories,
        "upsert_graph_memory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        memories,
        "queue_webhook_event",
        lambda event, payload, project_id: webhook_events.append((event, payload, project_id)),
    )
    request = _request()

    response = memories.deduplicate_memories(request, None)

    assert response == {"scanned": 3, "duplicate_groups": 1, "removed": 1, "failed": 0}
    instance.update.assert_called_once_with("old", metadata={"categories": ["jobs", "outreach"]})
    instance.delete.assert_called_once_with("new")
    assert deleted_graph == ["new"]
    assert webhook_events == [("memory.deleted", {"memory_id": "new"}, "default-project")]
    assert request.state.request_log_result_count == 1
