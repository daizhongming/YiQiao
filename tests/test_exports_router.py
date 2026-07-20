import json
import os
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from routers import exports as exports_router  # noqa: E402


def _row(memory_id, project_id="project-a", **payload):
    if project_id is not None:
        payload["project_id"] = project_id
    payload.setdefault("data", f"memory-{memory_id}")
    return SimpleNamespace(id=str(memory_id), payload=payload)


class _ListStore:
    def __init__(self, rows, *, honor_filters=True):
        self.rows = rows
        self.honor_filters = honor_filters
        self.calls = []

    def list(self, filters=None, top_k=100):
        self.calls.append((filters, top_k))
        rows = self.rows
        if filters and self.honor_filters:
            rows = [row for row in rows if all(row.payload.get(key) == value for key, value in filters.items())]
        if top_k is not None:
            rows = rows[:top_k]
        return [rows]


@pytest.fixture
def export_app(monkeypatch):
    jobs = []
    store = _ListStore([])

    def get_json(_db, key, default):
        assert key == exports_router.KEY
        return deepcopy(jobs) if jobs else deepcopy(default)

    def set_json(_db, key, value):
        assert key == exports_router.KEY
        jobs[:] = deepcopy(value)
        return value

    monkeypatch.setattr(exports_router, "get_json", get_json)
    monkeypatch.setattr(exports_router, "set_json", set_json)
    monkeypatch.setattr(
        exports_router,
        "get_memory_instance",
        lambda: SimpleNamespace(vector_store=store),
    )

    app = FastAPI()
    app.include_router(exports_router.router)
    app.dependency_overrides[exports_router.require_project_read] = lambda: None
    app.dependency_overrides[exports_router.require_project_write] = lambda: None
    app.dependency_overrides[exports_router.get_db] = lambda: object()

    return {
        "client": TestClient(app),
        "jobs": jobs,
        "store": store,
    }


def _create_and_get(client, payload, project_id="project-a"):
    headers = {"X-Project-ID": project_id}
    created = client.post("/memory-exports", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    fetched = client.get(f"/memory-exports/{created.json()['id']}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    return fetched.json()


def test_export_fetches_more_than_one_thousand_memories(export_app):
    export_app["store"].rows = [_row(index, user_id="bulk-user") for index in range(1005)]

    job = _create_and_get(export_app["client"], {"filters": {"user_id": "bulk-user"}})

    assert job["result"]["total"] == 1005
    assert job["result"]["memories"][0]["id"] == "0"
    assert job["result"]["memories"][-1]["id"] == "1004"
    assert export_app["store"].calls == [({"project_id": "project-a"}, None)]


def test_metadata_filters_compose_with_and_or_and_sibling_conditions(export_app):
    export_app["store"].rows = [
        _row(1, user_id="u1", app_id="crm", tier="gold", channel="email", profile={"region": "eu"}),
        _row(2, user_id="u2", app_id="crm", tier="silver", channel="email", profile={"region": "eu"}),
        _row(3, user_id="u3", app_id="crm", tier="gold", channel="push", profile={"region": "eu"}),
        _row(4, user_id="u4", app_id="other", tier="gold", channel="email", profile={"region": "eu"}),
        _row(5, user_id="u5", app_id="crm", tier="gold", channel="email", profile={"region": "us"}),
    ]
    filters = {
        "AND": [
            {"metadata": {"tier": "gold", "profile": {"region": "eu"}}},
            {
                "OR": [
                    {"metadata": {"channel": "email"}},
                    {"user_id": "u3"},
                ]
            },
        ],
        "app_id": "crm",
    }

    job = _create_and_get(export_app["client"], {"filters": filters})

    assert [memory["id"] for memory in job["result"]["memories"]] == ["1", "3"]


def test_date_range_parses_timezones_and_includes_the_entire_end_date(export_app):
    export_app["store"].rows = [
        _row("before", created_at="2025-01-31T23:59:59Z"),
        _row("start", created_at="2025-02-01T00:00:00Z"),
        _row("offset", created_at="2025-02-02T23:30:00-05:00"),
        _row("end", created_at="2025-02-03T23:59:59.999999Z"),
        _row("after", created_at="2025-02-04T00:00:00Z"),
        _row("invalid", created_at="not-a-date"),
    ]

    job = _create_and_get(
        export_app["client"],
        {"date_range": {"start": "2025-02-01", "end": "2025-02-03"}},
    )

    assert [memory["id"] for memory in job["result"]["memories"]] == ["start", "offset", "end"]

    invalid = export_app["client"].post(
        "/memory-exports",
        headers={"X-Project-ID": "project-a"},
        json={"date_range": {"start_date": "2025-02-04", "end_date": "2025-02-03"}},
    )
    assert invalid.status_code == 422


def test_schema_is_validated_and_recursively_projects_fields(export_app):
    export_app["store"].rows = [
        _row(
            1,
            user_id="u1",
            source="calendar",
            tags=[{"name": "work", "private": True}],
            categories=["work", "planning", "work"],
            secret="do-not-export",
        )
    ]
    schema = {
        "$defs": {"Tag": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "title": "ProjectedMemory",
        "type": "object",
        "properties": {
            "memory": {"type": "string"},
            "categories": {"type": "array", "items": {"type": "string"}},
            "metadata": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"name": {"type": "string"}}},
                    },
                },
            },
        },
        "required": ["memory"],
    }

    job = _create_and_get(export_app["client"], {"pydantic_schema": schema})

    assert job["result"]["memories"] == [
        {
            "memory": "memory-1",
            "categories": ["work", "planning"],
            "metadata": {"source": "calendar", "tags": [{"name": "work"}]},
        }
    ]
    assert job["pydantic_schema"] == schema


def test_schema_string_must_be_json_and_is_never_executed(export_app, tmp_path):
    marker = tmp_path / "executed"
    python_code = f'__import__("pathlib").Path({str(marker)!r}).write_text("bad")'

    response = export_app["client"].post(
        "/memory-exports",
        headers={"X-Project-ID": "project-a"},
        json={"pydantic_schema": python_code},
    )

    assert response.status_code == 422
    assert not marker.exists()
    assert export_app["jobs"] == []

    projected = _create_and_get(
        export_app["client"],
        {"pydantic_schema": json.dumps({"memory": "string", "user_id": "string"})},
    )
    assert projected["result"]["memories"] == []


def test_memory_results_and_export_jobs_are_isolated_by_project(export_app):
    export_app["store"].honor_filters = False
    export_app["store"].rows = [
        _row("a", project_id="project-a"),
        _row("b", project_id="project-b"),
        _row("legacy", project_id=None),
        _row("default", project_id="default-project"),
    ]

    project_a = _create_and_get(export_app["client"], {}, "project-a")
    project_b = _create_and_get(export_app["client"], {}, "project-b")
    default_project = _create_and_get(export_app["client"], {}, "default-project")

    assert [memory["id"] for memory in project_a["result"]["memories"]] == ["a"]
    assert [memory["id"] for memory in project_b["result"]["memories"]] == ["b"]
    assert [memory["id"] for memory in default_project["result"]["memories"]] == ["legacy", "default"]

    other_project_get = export_app["client"].get(
        f"/memory-exports/{project_a['id']}",
        headers={"X-Project-ID": "project-b"},
    )
    assert other_project_get.status_code == 404

    listed_a = export_app["client"].get("/memory-exports", headers={"X-Project-ID": "project-a"})
    listed_b = export_app["client"].get("/memory-exports", headers={"X-Project-ID": "project-b"})
    assert [job["id"] for job in listed_a.json()] == [project_a["id"]]
    assert [job["id"] for job in listed_b.json()] == [project_b["id"]]
    assert all("result" not in job for job in listed_a.json() + listed_b.json())


def test_export_jobs_support_project_scoped_search_and_pagination(export_app):
    export_app["jobs"][:] = [
        {
            "id": f"export-{index:02d}",
            "project_id": "project-a" if index < 13 else "project-b",
            "status": "completed",
            "entity": {"AND": [{"user_id": f"user-{index:02d}"}]},
            "result": {"total": index, "memories": []},
        }
        for index in range(15)
    ]

    page = export_app["client"].get(
        "/memory-exports?page=2&page_size=5",
        headers={"X-Project-ID": "project-a"},
    )

    assert page.status_code == 200
    assert page.json() == {
        "items": [
            {
                "id": f"export-{index:02d}",
                "project_id": "project-a",
                "status": "completed",
                "entity": {"AND": [{"user_id": f"user-{index:02d}"}]},
            }
            for index in range(5, 10)
        ],
        "total": 13,
        "page": 2,
        "page_size": 5,
        "total_pages": 3,
        "has_next": True,
        "has_previous": True,
    }

    by_entity = export_app["client"].get(
        "/memory-exports?page=1&page_size=10&search=USER-12",
        headers={"X-Project-ID": "project-a"},
    )
    assert [job["id"] for job in by_entity.json()["items"]] == ["export-12"]
    assert "result" not in by_entity.json()["items"][0]

    by_id = export_app["client"].get(
        "/memory-exports?page=1&page_size=10&search=EXPORT-03",
        headers={"X-Project-ID": "project-a"},
    )
    assert [job["id"] for job in by_id.json()["items"]] == ["export-03"]


def test_qdrant_style_scroll_consumes_every_page():
    rows = [_row(index) for index in range(1005)]

    class _ScrollClient:
        def __init__(self):
            self.offsets = []

        def scroll(self, *, offset=None, limit, **_kwargs):
            self.offsets.append(offset)
            start = offset or 0
            batch = rows[start : start + limit]
            next_offset = start + limit if start + limit < len(rows) else None
            return batch, next_offset

    class _ScrollStore:
        collection_name = "memories"

        def __init__(self):
            self.client = _ScrollClient()

        def _create_filter(self, filters):
            return filters

    store = _ScrollStore()

    fetched = exports_router._list_all_rows(store, {"project_id": "project-a"})

    assert len(fetched) == 1005
    assert store.client.offsets == [None, 1000]
