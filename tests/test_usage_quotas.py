import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from db import get_db  # noqa: E402
from models import Base, QuotaPolicy, RequestLog, Settings, User  # noqa: E402
from routers import usage as usage_router  # noqa: E402
from usage_service import (  # noqa: E402
    classify_operation,
    enforce_request_quotas,
    policy_usage,
    request_log_operation_clause,
    request_scope_context,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Settings(
            key="workspace_settings",
            value=(
                '{"organizations":[{"id":"org_a","name":"Org A"}],'
                '"active_organization_id":"org_a","active_project_id":"project_a",'
                '"projects":[{"id":"project_a","name":"Project A","organization_id":"org_a"}],'
                '"members":[]}'
            ),
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _user(db):
    user = User(id=uuid.uuid4(), name="User", email="user@example.com", password_hash="x", role="admin")
    db.add(user)
    db.commit()
    return user


def _request(method="POST", path="/memories"):
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    request.state.project_id = "project_a"
    request.state.auth_type = "bearer"
    request.state.api_key_id = None
    request.state.actor_user_id = None
    request.state.actor_email = "user@example.com"
    return request


def _policy(db, **overrides):
    values = {
        "scope_type": "project",
        "scope_id": "project_a",
        "project_id": "project_a",
        "metric": "memory_writes",
        "period": "month",
        "limit_value": 2,
        "mode": "hard",
        "warning_threshold": 0.8,
    }
    values.update(overrides)
    policy = QuotaPolicy(**values)
    db.add(policy)
    db.commit()
    return policy


def _log(
    db,
    *,
    operation="memory_write",
    method="POST",
    path="/memories",
    project_id="project_a",
    organization_id="org_a",
):
    db.add(
        RequestLog(
            method=method,
            path=path,
            status_code=200,
            latency_ms=10,
            auth_type="api_key",
            project_id=project_id,
            organization_id=organization_id,
            operation=operation,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def test_operation_classification_is_non_overlapping():
    assert classify_operation("POST", "/memories") == "memory_write"
    assert classify_operation("POST", "/memories/query") == "memory_read"
    assert classify_operation("POST", "/memories/query/") == "memory_read"
    assert classify_operation("POST", "/v3/memories/add/") == "memory_write"
    assert classify_operation("POST", "/search") == "memory_search"
    assert classify_operation("GET", "/memories") == "memory_read"
    assert classify_operation("DELETE", "/memories/abc") == "api_request"


def test_no_policy_is_unlimited(db):
    request = _request()
    enforce_request_quotas(request, _user(db), db)
    assert not hasattr(request.state, "quota_warnings")


def test_hard_project_limit_blocks_next_write(db):
    user = _user(db)
    _policy(db)
    _log(db)
    _log(db)

    with pytest.raises(HTTPException) as exc:
        enforce_request_quotas(_request(), user, db)

    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "quota_exceeded"
    assert exc.value.detail["metric"] == "memory_writes"


def test_memory_query_does_not_consume_memory_write_quota(db):
    user = _user(db)
    _policy(db)
    _log(db)
    _log(db)

    request = _request("POST", "/memories/query")
    enforce_request_quotas(request, user, db)

    assert request.state.quota_checked is True
    assert not hasattr(request.state, "quota_warnings")


def test_legacy_memory_query_log_does_not_consume_memory_write_quota(db):
    user = _user(db)
    policy = _policy(db)
    _log(db)
    _log(db, path="/memories/query", operation="memory_write")

    request = _request()
    enforce_request_quotas(request, user, db)

    assert request.state.quota_checked is True
    assert policy_usage(db, policy, request_scope_context(request, user, db)) == 1


def test_legacy_memory_query_log_matches_effective_read_operation(db):
    _log(db, path="/memories/query/", operation="memory_write")
    _log(db, method="PUT", path="/memories/query/", operation="memory_write")

    read_count = db.scalar(select(func.count(RequestLog.id)).where(request_log_operation_clause("memory_read")))
    write_count = db.scalar(select(func.count(RequestLog.id)).where(request_log_operation_clause("memory_write")))

    assert read_count == 1
    assert write_count == 1


def test_soft_limit_warns_without_blocking(db):
    user = _user(db)
    _policy(db, limit_value=10, mode="soft", warning_threshold=0.8)
    for _ in range(7):
        _log(db)
    request = _request()

    enforce_request_quotas(request, user, db)

    assert request.state.quota_warnings == ["memory_writes:7/10"]


def test_organization_limit_applies_to_project_request(db):
    user = _user(db)
    _policy(
        db,
        scope_type="organization",
        scope_id="org_a",
        project_id="",
        metric="api_requests",
        period="day",
        limit_value=1,
    )
    _log(db, operation="memory_read")

    with pytest.raises(HTTPException) as exc:
        enforce_request_quotas(_request("POST", "/search"), user, db)

    assert exc.value.detail["scope_type"] == "organization"


def test_management_routes_remain_available_after_hard_limit(db):
    user = _user(db)
    _policy(db, metric="api_requests", period="day", limit_value=1)
    _log(db)
    request = _request("GET", "/usage/summary")

    enforce_request_quotas(request, user, db)

    assert not getattr(request.state, "quota_checked", False)


def test_usage_memory_count_does_not_hide_provider_setup_requirement(monkeypatch):
    error = usage_router.ProviderConfigurationRequiredError("provider setup required")
    monkeypatch.setattr(usage_router, "get_memory_instance", lambda: (_ for _ in ()).throw(error))

    with pytest.raises(usage_router.ProviderConfigurationRequiredError, match="provider setup required"):
        usage_router._count_project_memories(["project_a"])


def test_usage_policy_routes_and_summary_use_real_events(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        user = User(id=uuid.uuid4(), name="Admin", email="admin@example.com", password_hash="x", role="admin")
        session.add(user)
        session.add(
            Settings(
                key="workspace_settings",
                value=(
                    '{"organizations":[{"id":"org_a","name":"Org A"}],'
                    '"active_organization_id":"org_a","active_project_id":"project_a",'
                    '"projects":[{"id":"project_a","name":"Project A","organization_id":"org_a"}],'
                    '"members":[]}'
                ),
            )
        )
        session.add_all(
            [
                RequestLog(
                    method="POST",
                    path="/memories",
                    status_code=200,
                    latency_ms=10,
                    auth_type="api_key",
                    project_id="project_a",
                    organization_id="org_a",
                    actor_user_id=user.id,
                    operation="memory_write",
                ),
                RequestLog(
                    method="POST",
                    path="/search",
                    status_code=200,
                    latency_ms=8,
                    auth_type="api_key",
                    project_id="project_a",
                    organization_id="org_a",
                    actor_user_id=user.id,
                    operation="memory_search",
                ),
                RequestLog(
                    method="POST",
                    path="/memories/query",
                    status_code=200,
                    latency_ms=6,
                    auth_type="api_key",
                    project_id="project_a",
                    organization_id="org_a",
                    actor_user_id=user.id,
                    operation="memory_write",
                ),
            ]
        )
        session.commit()
        user_id = user.id

    def db_override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def auth_override(request: Request, db=Depends(get_db)):
        request.state.auth_type = "bearer"
        request.state.project_id = "project_a"
        return db.get(User, user_id)

    class _Store:
        def list(self, filters=None, top_k=100):
            return [[object(), object(), object()]] if filters == {"project_id": "project_a"} else [[]]

    class _Memory:
        vector_store = _Store()

    monkeypatch.setattr(usage_router, "get_memory_instance", lambda: _Memory())
    app = FastAPI()
    app.include_router(usage_router.router)
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[usage_router.verify_auth] = auth_override
    client = TestClient(app)

    saved = client.put(
        "/usage/policies",
        json={
            "scope_type": "project",
            "scope_id": "project_a",
            "project_id": "project_a",
            "policies": [
                {
                    "metric": "memory_writes",
                    "period": "month",
                    "limit_value": 100,
                    "mode": "soft",
                    "warning_threshold": 0.8,
                }
            ],
        },
    )
    listed = client.get(
        "/usage/policies",
        params={"scope_type": "project", "scope_id": "project_a", "project_id": "project_a"},
    )
    summary = client.get("/usage/summary", params={"days": 7})
    today = datetime.now(timezone.utc).date().isoformat()
    custom_range = client.get(
        "/usage/summary",
        params={"start_date": today, "end_date": today},
    )
    invalid_range = client.get(
        "/usage/summary",
        params={"start_date": "2026-07-02", "end_date": "2026-07-01"},
    )

    assert saved.status_code == 200
    assert listed.status_code == 200
    assert listed.json()["policies"][0]["limit_value"] == 100
    assert summary.status_code == 200
    assert summary.json()["totals"] == {
        "stored_memories": 3,
        "api_requests": 3,
        "errors": 0,
        "memory_writes": 1,
        "memory_searches": 1,
    }
    assert summary.json()["effective_limits"][0]["used"] == 1
    assert summary.json()["series"][-1]["memory_writes"] == 1
    assert custom_range.status_code == 200
    assert custom_range.json()["period"] == {"days": 1, "start": today, "end": today}
    assert custom_range.json()["totals"]["api_requests"] == 3
    assert invalid_range.status_code == 422
    engine.dispose()
