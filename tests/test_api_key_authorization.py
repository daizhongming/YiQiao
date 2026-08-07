import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

import auth  # noqa: E402
from auth import (  # noqa: E402
    API_KEY_SCOPES,
    require_project_read,
    require_project_write,
)
from db import get_db  # noqa: E402
from models import APIKey, Base, Settings, User  # noqa: E402
from routers import api_keys as api_keys_router  # noqa: E402
from routers import auth as account_router  # noqa: E402
from routers import settings as settings_router  # noqa: E402
from routers import usage as usage_router  # noqa: E402
from routers import webhooks as webhooks_router  # noqa: E402
from workspace import WORKSPACE_KEY  # noqa: E402

PROJECT_ID = "project-a"
OTHER_PROJECT_ID = "project-b"


def _workspace() -> dict:
    return {
        "organization": {"id": "org-a", "name": "Org A"},
        "organizations": [{"id": "org-a", "name": "Org A"}],
        "active_organization_id": "org-a",
        "active_project_id": PROJECT_ID,
        "projects": [
            {"id": PROJECT_ID, "name": "Project A", "organization_id": "org-a"},
            {"id": OTHER_PROJECT_ID, "name": "Project B", "organization_id": "org-a"},
        ],
        "members": [
            {
                "email": "reader@example.com",
                "role": "READER",
                "status": "active",
                "project_id": PROJECT_ID,
                "organization_id": "org-a",
            }
        ],
    }


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "JWT_SECRET", "api-key-authorization-test-secret-at-least-32-bytes")
    auth.invalidate_api_key_auth_cache()

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    admin_id = uuid.uuid4()
    reader_id = uuid.uuid4()
    with sessions() as db:
        db.add_all(
            [
                User(id=admin_id, name="Admin", email="admin@example.com", password_hash="x", role="admin"),
                User(id=reader_id, name="Reader", email="reader@example.com", password_hash="x", role="user"),
                Settings(key=WORKSPACE_KEY, value=json.dumps(_workspace())),
            ]
        )
        db.commit()

    app = FastAPI()

    @app.get("/probe/read")
    def read_probe(_auth=Depends(require_project_read)):
        return {"allowed": True}

    @app.post("/probe/write")
    def write_probe(_auth=Depends(require_project_write)):
        return {"allowed": True}

    app.include_router(api_keys_router.router)
    app.include_router(account_router.router)
    app.include_router(settings_router.router)
    app.include_router(settings_router.cloud_router)
    app.include_router(usage_router.router)
    app.include_router(webhooks_router.compat_router)

    def override_get_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            sessions=sessions,
            admin_id=admin_id,
            reader_id=reader_id,
        )

    auth.invalidate_api_key_auth_cache()
    engine.dispose()


def _issue_key(
    auth_app,
    scopes: list[str] | None,
    *,
    expires_at: datetime | None = None,
    owner_id=None,
) -> tuple[str, object]:
    full_key, prefix, key_hash = auth.generate_api_key()
    with auth_app.sessions() as db:
        key = APIKey(
            key_prefix=prefix,
            key_hash=key_hash,
            label="Test key",
            project_id=PROJECT_ID,
            scopes=scopes,
            expires_at=expires_at,
            created_by=owner_id or auth_app.admin_id,
        )
        db.add(key)
        db.commit()
        key_id = key.id
    return full_key, key_id


def _api_key_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def _jwt_headers(user_id, role: str = "admin") -> dict[str, str]:
    token = auth.create_access_token(str(user_id), role)
    return {
        "Authorization": f"Bearer {token}",
        "X-Project-ID": PROJECT_ID,
    }


@pytest.mark.parametrize(
    ("scopes", "read_allowed", "write_allowed"),
    [
        (None, True, True),
        ([], False, False),
        (["memory:read"], True, False),
        (["memory:write"], False, True),
        (["memory:read", "memory:write"], True, True),
    ],
    ids=["legacy-null", "empty", "read-only", "write-only", "read-write"],
)
def test_api_key_scope_matrix(auth_app, scopes, read_allowed, write_allowed):
    key, _key_id = _issue_key(auth_app, scopes)
    headers = _api_key_headers(key)

    read = auth_app.client.get("/probe/read", headers=headers)
    write = auth_app.client.post("/probe/write", headers=headers)

    assert read.status_code == (200 if read_allowed else 403), read.text
    assert write.status_code == (200 if write_allowed else 403), write.text
    for response, allowed in ((read, read_allowed), (write, write_allowed)):
        if not allowed:
            assert response.json()["detail"]["code"] == "insufficient_scope"


def test_expired_and_cached_revoked_keys_are_rejected(auth_app):
    expired_key, _expired_id = _issue_key(
        auth_app,
        list(API_KEY_SCOPES),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    expired = auth_app.client.get("/probe/read", headers=_api_key_headers(expired_key))
    assert expired.status_code == 401
    assert expired.json()["detail"]["code"] == "auth_expired"

    revocable_key, revocable_id = _issue_key(auth_app, list(API_KEY_SCOPES))
    assert auth_app.client.get("/probe/read", headers=_api_key_headers(revocable_key)).status_code == 200
    with auth_app.sessions() as db:
        db.get(APIKey, revocable_id).revoked_at = datetime.now(timezone.utc)
        db.commit()

    revoked = auth_app.client.get("/probe/read", headers=_api_key_headers(revocable_key))
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "key_revoked"


def _assert_management_rejected(auth_app, headers: dict[str, str], target_key_id) -> None:
    requests = [
        ("GET", "/api-keys", None),
        ("POST", "/api-keys", {"label": "Unauthorized key"}),
        ("DELETE", f"/api-keys/{target_key_id}", None),
    ]
    for method, path, payload in requests:
        response = auth_app.client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "dashboard_login_required"


def test_key_management_rejects_project_key_admin_key_and_disabled_auth(auth_app, monkeypatch):
    project_key, target_key_id = _issue_key(auth_app, list(API_KEY_SCOPES))
    _assert_management_rejected(auth_app, _api_key_headers(project_key), target_key_id)

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "legacy-admin-key")
    _assert_management_rejected(auth_app, {"X-API-Key": "legacy-admin-key"}, target_key_id)

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    _assert_management_rejected(auth_app, {"X-Project-ID": PROJECT_ID}, target_key_id)


def test_project_key_cannot_enter_account_control_plane(auth_app):
    project_key, _key_id = _issue_key(auth_app, list(API_KEY_SCOPES))
    headers = _api_key_headers(project_key)
    requests = [
        ("GET", "/auth/me", None),
        ("PATCH", "/auth/me", {"name": "Project key mutation"}),
        ("POST", "/auth/change-password", {"current_password": "x", "new_password": "long-enough-password"}),
        ("POST", "/auth/onboarding-complete", {"use_case": "project key"}),
        ("DELETE", "/auth/me", None),
    ]

    for method, path, payload in requests:
        response = auth_app.client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "dashboard_login_required"


def test_account_routes_preserve_legacy_instance_auth(auth_app, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "legacy-admin-key")
    admin_key = auth_app.client.get("/auth/me", headers={"X-API-Key": "legacy-admin-key"})
    assert admin_key.status_code == 200, admin_key.text
    assert admin_key.json()["email"] == "admin@example.com"

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    disabled = auth_app.client.get("/auth/me")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["email"] == "admin@example.com"


@pytest.mark.parametrize(
    "scopes",
    [None, [], ["memory:read"], ["memory:write"]],
    ids=["legacy-null", "empty", "read-only", "write-only"],
)
def test_project_key_cannot_enter_workspace_or_usage_control_planes(auth_app, scopes):
    project_key, _key_id = _issue_key(auth_app, scopes)
    headers = _api_key_headers(project_key)

    workspace = auth_app.client.get("/settings/workspace", headers=headers)
    organizations = auth_app.client.get("/api/v1/orgs/organizations/", headers=headers)
    subjects = auth_app.client.get("/usage/subjects", headers=headers)
    policies = auth_app.client.put(
        "/usage/policies",
        headers=headers,
        json={
            "scope_type": "project",
            "scope_id": PROJECT_ID,
            "project_id": PROJECT_ID,
            "policies": [],
        },
    )

    assert workspace.status_code == 403, workspace.text
    assert organizations.status_code == 403, organizations.text
    assert subjects.status_code == 403, subjects.text
    assert policies.status_code == 403, policies.text


def test_usage_control_plane_preserves_legacy_instance_auth(auth_app, monkeypatch):
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "legacy-admin-key")
    admin_key = auth_app.client.get("/usage/subjects", headers={"X-API-Key": "legacy-admin-key"})
    assert admin_key.status_code == 200, admin_key.text

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "AUTH_DISABLED", True)
    disabled = auth_app.client.get("/usage/subjects")
    assert disabled.status_code == 200, disabled.text


def test_usage_summary_hides_a_caller_selected_cross_project_scope(auth_app):
    response = auth_app.client.get(
        f"/usage/summary?scope_type=project&scope_id={OTHER_PROJECT_ID}",
        headers=_jwt_headers(auth_app.reader_id, role="user"),
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Project not found."


@pytest.mark.parametrize("scopes", [[], ["memory:read"]], ids=["empty", "read-only"])
def test_compat_webhook_write_enforces_api_key_scope(auth_app, scopes):
    project_key, _key_id = _issue_key(auth_app, scopes)
    response = auth_app.client.post(
        f"/api/v1/webhooks/projects/{PROJECT_ID}/",
        headers=_api_key_headers(project_key),
        json={
            "name": "Scope probe",
            "url": "https://example.invalid/hook",
            "events": ["memory.added"],
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "insufficient_scope"


def test_compat_webhook_write_accepts_memory_write_scope(auth_app):
    project_key, _key_id = _issue_key(auth_app, ["memory:write"])
    response = auth_app.client.post(
        f"/api/v1/webhooks/projects/{PROJECT_ID}/",
        headers=_api_key_headers(project_key),
        json={
            "name": "Write scope",
            "url": "https://example.invalid/hook",
            "events": ["memory.added"],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["project_id"] == PROJECT_ID


def test_compat_webhook_mutations_hide_cross_project_hooks(auth_app):
    project_key, _key_id = _issue_key(auth_app, ["memory:write"])
    created = auth_app.client.post(
        f"/api/v1/webhooks/projects/{OTHER_PROJECT_ID}/",
        headers=_jwt_headers(auth_app.admin_id),
        json={
            "name": "Other project hook",
            "url": "https://example.invalid/other-hook",
            "events": ["memory.added"],
        },
    )
    assert created.status_code == 201, created.text
    hook_id = created.json()["id"]

    headers = _api_key_headers(project_key)
    updated = auth_app.client.put(
        f"/api/v1/webhooks/{hook_id}/",
        headers=headers,
        json={"name": "Cross-project update"},
    )
    deleted = auth_app.client.delete(f"/api/v1/webhooks/{hook_id}/", headers=headers)

    assert updated.status_code == 404, updated.text
    assert deleted.status_code == 404, deleted.text


def test_compat_webhook_mutations_preserve_bearer_admin_compatibility(auth_app):
    created = auth_app.client.post(
        f"/api/v1/webhooks/projects/{OTHER_PROJECT_ID}/",
        headers=_jwt_headers(auth_app.admin_id),
        json={
            "name": "Other project hook",
            "url": "https://example.invalid/other-hook",
            "events": ["memory.added"],
        },
    )
    assert created.status_code == 201, created.text
    hook_id = created.json()["id"]

    headers = _jwt_headers(auth_app.admin_id)
    updated = auth_app.client.put(
        f"/api/v1/webhooks/{hook_id}/",
        headers=headers,
        json={"name": "Updated by admin"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Updated by admin"
    assert updated.json()["project_id"] == OTHER_PROJECT_ID

    deleted = auth_app.client.delete(f"/api/v1/webhooks/{hook_id}/", headers=headers)
    assert deleted.status_code == 200, deleted.text


def test_dashboard_key_management_returns_scopes_and_expiry(auth_app):
    headers = _jwt_headers(auth_app.admin_id)
    expires_at = datetime.now(timezone.utc) + timedelta(days=2)
    created = auth_app.client.post(
        "/api-keys",
        headers=headers,
        json={
            "label": "  Read agent  ",
            "project_id": PROJECT_ID,
            "scopes": ["memory:read"],
            "expires_at": expires_at.isoformat(),
        },
    )

    assert created.status_code == 201, created.text
    created_data = created.json()
    assert created_data["label"] == "Read agent"
    assert created_data["scopes"] == ["memory:read"]
    returned_expiry = datetime.fromisoformat(created_data["expires_at"].replace("Z", "+00:00"))
    assert returned_expiry.tzinfo is not None
    assert returned_expiry == expires_at

    defaulted = auth_app.client.post("/api-keys", headers=headers, json={"label": "Default agent"})
    assert defaulted.status_code == 201, defaulted.text
    assert defaulted.json()["scopes"] == list(API_KEY_SCOPES)

    empty = auth_app.client.post(
        "/api-keys",
        headers=headers,
        json={"label": "Disabled agent", "scopes": []},
    )
    assert empty.status_code == 201, empty.text
    assert empty.json()["scopes"] == []

    _legacy_key, legacy_id = _issue_key(auth_app, None)

    listed = auth_app.client.get("/api-keys", headers=headers)
    assert listed.status_code == 200, listed.text
    items = {item["id"]: item for item in listed.json()}
    assert items[created_data["id"]]["scopes"] == ["memory:read"]
    assert items[created_data["id"]]["expires_at"] == created_data["expires_at"]
    assert items[defaulted.json()["id"]]["scopes"] == list(API_KEY_SCOPES)
    assert items[defaulted.json()["id"]]["expires_at"] is None
    assert items[empty.json()["id"]]["scopes"] == []
    assert items[str(legacy_id)]["scopes"] is None

    with auth_app.sessions() as db:
        created_row = db.get(APIKey, uuid.UUID(created_data["id"]))
        defaulted_row = db.get(APIKey, uuid.UUID(defaulted.json()["id"]))
        empty_row = db.get(APIKey, uuid.UUID(empty.json()["id"]))
        assert created_row.scopes == ["memory:read"]
        assert created_row.expires_at is not None
        assert defaulted_row.scopes == list(API_KEY_SCOPES)
        assert defaulted_row.scopes is not None
        assert empty_row.scopes == []

    revoked = auth_app.client.delete(f"/api-keys/{created_data['id']}", headers=headers)
    assert revoked.status_code == 200, revoked.text
    remaining = auth_app.client.get("/api-keys", headers=headers)
    assert {item["id"] for item in remaining.json()} == {
        defaulted.json()["id"],
        empty.json()["id"],
        str(legacy_id),
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"label": "   "},
        {"label": "Agent\u0000key"},
        {"label": "x" * 256},
        {"label": "Agent", "scopes": None},
        {"label": "Agent", "scope": ["memory:read"]},
        {"label": "Agent", "expiresAt": "2030-01-01T00:00:00Z"},
        {"label": "Agent", "scopes": ["memory:admin"]},
        {"label": "Agent", "scopes": ["memory:read", "memory:read"]},
        {"label": "Agent", "expires_at": "2030-01-01T00:00:00"},
        {"label": "Agent", "expires_at": "2020-01-01T00:00:00Z"},
    ],
)
def test_key_create_validation_rejects_invalid_fields(auth_app, payload):
    response = auth_app.client.post("/api-keys", headers=_jwt_headers(auth_app.admin_id), json=payload)
    assert response.status_code == 422, response.text


def test_key_management_enforces_project_role_and_body_project_match(auth_app):
    reader_headers = _jwt_headers(auth_app.reader_id, role="user")
    _reader_key, reader_key_id = _issue_key(auth_app, list(API_KEY_SCOPES), owner_id=auth_app.reader_id)
    assert auth_app.client.get("/api-keys", headers=reader_headers).status_code == 403
    denied = auth_app.client.post("/api-keys", headers=reader_headers, json={"label": "Reader key"})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Project access denied."
    denied_revoke = auth_app.client.delete(f"/api-keys/{reader_key_id}", headers=reader_headers)
    assert denied_revoke.status_code == 403
    assert denied_revoke.json()["detail"] == "Project access denied."

    mismatch = auth_app.client.post(
        "/api-keys",
        headers=_jwt_headers(auth_app.admin_id),
        json={"label": "Wrong project", "project_id": OTHER_PROJECT_ID},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["detail"] == "Cannot create an API key for another project."

    missing_project = auth_app.client.post(
        "/api-keys",
        headers={
            "Authorization": _jwt_headers(auth_app.admin_id)["Authorization"],
            "X-Project-ID": "missing-project",
        },
        json={"label": "Missing project"},
    )
    assert missing_project.status_code == 404
    assert missing_project.json()["detail"] == "Project not found."


def test_project_manager_can_list_and_revoke_another_creators_key(auth_app):
    _project_key, key_id = _issue_key(auth_app, list(API_KEY_SCOPES), owner_id=auth_app.reader_id)
    headers = _jwt_headers(auth_app.admin_id)

    listed = auth_app.client.get("/api-keys", headers=headers)
    assert listed.status_code == 200, listed.text
    assert str(key_id) in {item["id"] for item in listed.json()}

    revoked = auth_app.client.delete(f"/api-keys/{key_id}", headers=headers)
    assert revoked.status_code == 200, revoked.text
    remaining = auth_app.client.get("/api-keys", headers=headers)
    assert str(key_id) not in {item["id"] for item in remaining.json()}
