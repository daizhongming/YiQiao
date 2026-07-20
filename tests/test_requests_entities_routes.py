import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from db import get_db  # noqa: E402
from models import Base, RequestLog  # noqa: E402
from routers import entities as entities_router  # noqa: E402
from routers import requests as requests_router  # noqa: E402


@pytest.fixture
def activity_client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        session.add_all(
            [
                RequestLog(
                    id=uuid.uuid4(),
                    method="POST",
                    path="/memories",
                    status_code=200,
                    latency_ms=1200,
                    auth_type="api_key",
                    project_id="project_a",
                    operation="memory_write",
                    event_type="ADD",
                    user_id="alice",
                    request_payload={"user_id": "alice", "messages": [{"content": "hello"}]},
                    response_payload={"results": [{"id": "one"}, {"id": "two"}]},
                    result_count=2,
                    created_at=now - timedelta(hours=3),
                ),
                RequestLog(
                    id=uuid.uuid4(),
                    method="POST",
                    path="/search",
                    status_code=200,
                    latency_ms=42,
                    auth_type="api_key",
                    project_id="project_a",
                    operation="memory_search",
                    event_type="SEARCH",
                    user_id="alice",
                    request_payload={"query": "hello", "filters": {"user_id": "alice"}},
                    response_payload={"results": []},
                    result_count=0,
                    created_at=now - timedelta(hours=2),
                ),
                RequestLog(
                    id=uuid.uuid4(),
                    method="GET",
                    path="/memories",
                    status_code=500,
                    latency_ms=8,
                    auth_type="admin_api_key",
                    project_id="project_a",
                    operation="memory_read",
                    event_type="GET_ALL",
                    agent_id="bot",
                    request_payload={"agent_id": "bot"},
                    result_count=None,
                    created_at=now - timedelta(hours=1),
                ),
                RequestLog(
                    id=uuid.uuid4(),
                    method="POST",
                    path="/memories",
                    status_code=200,
                    latency_ms=10,
                    auth_type="api_key",
                    project_id="project_b",
                    operation="memory_write",
                    event_type="ADD",
                    user_id="alice",
                    result_count=1,
                    created_at=now,
                ),
                RequestLog(
                    id=uuid.uuid4(),
                    method="POST",
                    path="/memories",
                    status_code=200,
                    latency_ms=10,
                    auth_type="bearer",
                    project_id="project_a",
                    operation="memory_write",
                    event_type="ADD",
                    user_id="alice",
                    result_count=1,
                    created_at=now,
                ),
            ]
        )
        session.commit()

    rows = [
        SimpleNamespace(
            id="m1",
            payload={
                "project_id": "project_a",
                "user_id": "alice",
                "data": "First",
                "created_at": (now - timedelta(days=2)).isoformat(),
                "updated_at": (now - timedelta(days=1)).isoformat(),
            },
        ),
        SimpleNamespace(
            id="m2",
            payload={
                "project_id": "project_a",
                "user_id": "alice",
                "data": "Second",
                "created_at": (now - timedelta(days=1)).isoformat(),
                "updated_at": now.isoformat(),
            },
        ),
        SimpleNamespace(
            id="m3",
            payload={
                "project_id": "project_b",
                "user_id": "alice",
                "data": "Other project",
                "created_at": now.isoformat(),
            },
        ),
    ]

    class _VectorStore:
        def list(self, top_k=10_000):
            return [rows[:top_k]]

    class _Memory:
        vector_store = _VectorStore()

    monkeypatch.setattr(entities_router, "get_memory_instance", lambda: _Memory())

    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def auth_override(request: Request):
        request.state.project_id = "project_a"
        return None

    app = FastAPI()
    app.include_router(requests_router.router)
    app.include_router(entities_router.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[requests_router.require_project_read] = auth_override
    app.dependency_overrides[entities_router.require_project_read] = auth_override
    client = TestClient(app)
    try:
        yield client
    finally:
        engine.dispose()


def test_request_activity_supports_server_pagination_and_event_filters(activity_client):
    response = activity_client.get(
        "/requests",
        params={
            "page": 1,
            "page_size": 1,
            "event_type": "ADD",
            "entity_type": "user",
            "entity_id": "alice",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert len(body["series"]) == 1
    assert body["items"][0]["event_type"] == "ADD"
    assert body["items"][0]["entities"] == [{"type": "user", "id": "alice"}]
    assert body["items"][0]["has_results"] is True
    assert body["items"][0]["result_count"] == 2
    assert body["items"][0]["request_payload"]["user_id"] == "alice"


def test_request_activity_preserves_legacy_list_and_filters_results(activity_client):
    legacy = activity_client.get("/requests", params={"limit": 10})
    with_results = activity_client.get("/requests", params={"page": 1, "page_size": 10, "has_results": True})
    failed = activity_client.get("/requests", params={"page": 1, "page_size": 10, "succeeded": False})

    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert len(legacy.json()) == 3
    assert with_results.json()["total"] == 1
    assert with_results.json()["items"][0]["event_type"] == "ADD"
    assert failed.json()["total"] == 1
    assert failed.json()["items"][0]["status"] == "failed"


def test_request_activity_filters_by_time(activity_client):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = activity_client.get("/requests", params={"page": 1, "page_size": 10, "start_at": future})

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["series"] == []


def test_entity_detail_combines_memory_and_request_counts(activity_client):
    response = activity_client.get("/entities/user/alice")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "alice"
    assert body["type"] == "user"
    assert body["total_memories"] == 2
    assert body["total_requests"] == 2
    assert body["created_at"] is not None
    assert body["updated_at"] is not None


def test_entity_detail_returns_404_for_unknown_entity(activity_client):
    response = activity_client.get("/entities/user/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Entity not found."
