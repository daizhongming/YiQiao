# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Real-app acceptance coverage for Public Service Connector OAuth 1.0."""

from __future__ import annotations

import json
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import auth  # noqa: E402
import connector_protocol as protocol  # noqa: E402
import oauth_service  # noqa: E402
from db import get_db  # noqa: E402
from models import (  # noqa: E402
    APIKey,
    Base,
    OAuthApplication,
    OAuthAuditEvent,
    OAuthGrant,
    OAuthRefreshToken,
    Settings,
    User,
)
from workspace import (  # noqa: E402
    DEFAULT_ORG_ID,
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
)

ISSUER = "http://127.0.0.1:3101"
OAUTH_SECRET = "oauth-test-hmac-secret-with-at-least-32-bytes"
CLIENTS = (("desktop-client", "Desktop Client"), ("automation-client", "Automation Client"))


class _QuotaGuard:
    def __init__(self, *_args, **_kwargs):
        pass

    def release(self):
        pass


@pytest.fixture
def oauth_app(tmp_path, monkeypatch):
    import server.main as server_main

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'oauth.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    workspace = deepcopy(DEFAULT_WORKSPACE_SETTINGS)
    workspace["members"] = [
        {
            "email": "editor@example.com",
            "role": "EDITOR",
            "status": "active",
            "project_id": DEFAULT_PROJECT_ID,
            "organization_id": DEFAULT_ORG_ID,
        }
    ]
    with sessions() as db:
        db.add_all(
            [
                User(
                    id=admin_id,
                    name="Admin",
                    email="admin@example.com",
                    password_hash="unused",
                    role="admin",
                ),
                User(
                    id=editor_id,
                    name="Editor",
                    email="editor@example.com",
                    password_hash="unused",
                    role="member",
                ),
                Settings(key=WORKSPACE_KEY, value=json.dumps(workspace)),
                *[
                    OAuthApplication(
                        client_id=client_id,
                        display_name=display_name,
                        client_type="public",
                        allowed_audiences=[protocol.AUDIENCE],
                        allowed_scopes=list(protocol.SUPPORTED_SCOPES),
                        status="active",
                        operator_metadata={"owner": "integration-test"},
                    )
                    for client_id, display_name in CLIENTS
                ],
            ]
        )
        db.commit()

    monkeypatch.setenv("OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OAUTH_USER_CODE_HMAC_SECRET", OAUTH_SECRET)
    monkeypatch.setenv("OAUTH_AUDIT_HMAC_SECRET", OAUTH_SECRET)
    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "JWT_SECRET", "oauth-test-jwt-secret-with-at-least-32-bytes")
    auth.invalidate_api_key_auth_cache()

    def override_get_db():
        with sessions() as db:
            yield db

    previous_overrides = dict(server_main.app.dependency_overrides)
    server_main.app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(server_main, "SessionLocal", sessions)
    monkeypatch.setattr(server_main, "_persist_request_log", MagicMock())
    monkeypatch.setattr(server_main, "_enforce_memory_storage_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server_main, "_capture_import_storage_quota_snapshot", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(server_main, "ImportStorageQuotaGuard", _QuotaGuard)
    monkeypatch.setattr(
        server_main,
        "_store_memory",
        lambda *_args, **_kwargs: {"results": [{"id": "memory-1", "event": "ADD"}]},
    )
    memory = MagicMock()
    memory.search.return_value = {"results": []}
    monkeypatch.setattr(server_main, "get_memory_instance", lambda: memory)
    monkeypatch.setattr(server_main, "graph_related_memories", lambda *_args, **_kwargs: [])

    def current_workspace():
        with sessions() as db:
            row = db.get(Settings, WORKSPACE_KEY)
            return json.loads(row.value) if row else deepcopy(DEFAULT_WORKSPACE_SETTINGS)

    monkeypatch.setattr(server_main, "_workspace_settings", current_workspace)

    client = TestClient(server_main.app)
    context = SimpleNamespace(
        client=client,
        sessions=sessions,
        admin_id=admin_id,
        editor_id=editor_id,
        admin_token=auth.create_access_token(str(admin_id), "admin"),
        editor_token=auth.create_access_token(str(editor_id), "member"),
        request_log=server_main._persist_request_log,
    )
    try:
        yield context
    finally:
        client.close()
        server_main.app.dependency_overrides.clear()
        server_main.app.dependency_overrides.update(previous_overrides)
        auth.invalidate_api_key_auth_cache()
        engine.dispose()


def _dashboard_headers(token: str, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Project-ID": project_id}


def _oauth_headers(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def _device_request(client: TestClient, client_id: str, scopes: str = "memory:read memory:write") -> tuple[dict, str]:
    verifier = "device-verifier-value-that-is-long-enough-for-pkce-S256"
    response = client.post(
        "/oauth/device_authorization",
        data={
            "client_id": client_id,
            "scope": scopes,
            "audience": protocol.AUDIENCE,
            "code_challenge": protocol.pkce_s256(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), verifier


def _authorize(
    oauth_app,
    client_id: str,
    *,
    requested_scopes: str = "memory:read memory:write",
    approved_scopes: list[str] | None = None,
    dashboard_token: str | None = None,
) -> tuple[dict, dict]:
    started, verifier = _device_request(oauth_app.client, client_id, requested_scopes)
    dashboard_token = dashboard_token or oauth_app.admin_token
    lookup = oauth_app.client.post(
        "/oauth/device-requests/lookup",
        headers=_dashboard_headers(dashboard_token),
        json={"user_code": started["user_code"]},
    )
    assert lookup.status_code == 200, lookup.text
    approval_body = {"project_id": DEFAULT_PROJECT_ID}
    if approved_scopes is not None:
        approval_body["approved_scopes"] = approved_scopes
    approval = oauth_app.client.post(
        f"/oauth/device-requests/{lookup.json()['id']}/approve",
        headers=_dashboard_headers(dashboard_token),
        json=approval_body,
    )
    assert approval.status_code == 200, approval.text
    exchange = oauth_app.client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": started["device_code"],
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert exchange.status_code == 200, exchange.text
    return started, exchange.json()


def _refresh(client: TestClient, client_id: str, refresh_token: str):
    return client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
    )


def _set_workspace(oauth_app, update):
    with oauth_app.sessions() as db:
        row = db.get(Settings, WORKSPACE_KEY)
        workspace = json.loads(row.value)
        update(workspace)
        row.value = json.dumps(workspace)
        db.commit()


def _error_code(response) -> str:
    return response.json()["detail"]["code"]


def test_discovery_health_and_application_dtos_are_safe(oauth_app):
    metadata = oauth_app.client.get("/.well-known/oauth-authorization-server")
    capabilities = oauth_app.client.get("/.well-known/service-capabilities")
    health = oauth_app.client.get("/oauth/health")
    assert metadata.status_code == capabilities.status_code == health.status_code == 200
    assert metadata.json()["issuer"] == capabilities.json()["issuer"] == ISSUER
    assert metadata.json()["device_authorization_endpoint"] == f"{ISSUER}/oauth/device_authorization"
    assert capabilities.json()["health_endpoint"] == f"{ISSUER}/oauth/health"
    assert capabilities.json()["memory_api"]["ping_endpoint"] == f"{ISSUER}/v1/ping/"
    assert capabilities.json()["project_selection"]["required"] is True
    for response in (metadata, capabilities, health):
        assert response.headers["cache-control"] == "no-store, no-cache"

    applications = oauth_app.client.get(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert applications.status_code == 200
    assert applications.json()["can_register"] is True
    serialized = applications.text.lower()
    assert "token_hash" not in serialized
    assert "code_hash" not in serialized
    assert "password" not in serialized

    with oauth_app.sessions() as db:
        application = db.get(OAuthApplication, CLIENTS[0][0])
        application.operator_metadata = {"accessToken": "legacy-value-that-must-not-be-returned"}
        db.commit()
    filtered = oauth_app.client.get(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert "legacy-value-that-must-not-be-returned" not in filtered.text
    matching = next(item for item in filtered.json()["items"] if item["client_id"] == CLIENTS[0][0])
    assert matching["operator_metadata"] == {}

    invalid_metadata = oauth_app.client.post(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
        json={
            "client_id": "unsafe-client",
            "display_name": "Unsafe",
            "operator_metadata": {"access_token": "must-not-be-stored"},
        },
    )
    assert invalid_metadata.status_code == 400
    assert "must-not-be-stored" not in invalid_metadata.text
    invalid_client_id = oauth_app.client.post(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
        json={"client_id": "invalid client id", "display_name": "Invalid"},
    )
    assert invalid_client_id.status_code == 400


def test_two_public_clients_use_the_same_lifecycle(oauth_app):
    for client_id, _display_name in CLIENTS:
        _started, credential = _authorize(oauth_app, client_id)
        headers = _oauth_headers(credential["access_token"])
        assert oauth_app.client.get("/v1/ping/", headers=headers).status_code == 200
        assert oauth_app.client.post("/search", headers=headers, json={"query": "connector"}).status_code == 200
        assert (
            oauth_app.client.post(
                "/memories",
                headers=headers,
                json={"messages": [{"role": "user", "content": "remember this"}], "user_id": "owner"},
            ).status_code
            == 200
        )
        assert _error_code(oauth_app.client.get("/memories", headers=headers)) == "insufficient_scope"
        assert (
            _error_code(oauth_app.client.post("/v3/memories/search/", headers=headers, json={})) == "insufficient_scope"
        )

        with oauth_app.sessions() as db:
            grant = db.scalar(
                select(OAuthGrant).where(
                    OAuthGrant.client_id == client_id,
                    OAuthGrant.status == "active",
                )
            )
            refresh = db.scalar(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.grant_id == grant.id,
                    OAuthRefreshToken.status == "active",
                )
            )
            assert credential["access_token"] not in grant.access_token_hash
            assert credential["refresh_token"] not in refresh.token_hash

        refreshed = _refresh(oauth_app.client, client_id, credential["refresh_token"])
        assert refreshed.status_code == 200, refreshed.text
        rotated = refreshed.json()
        assert rotated["refresh_token"] != credential["refresh_token"]
        assert oauth_app.client.get("/v1/ping/", headers=headers).status_code == 401

        revoked = oauth_app.client.post(
            "/oauth/revoke",
            data={
                "token": rotated["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": client_id,
            },
        )
        assert revoked.status_code == 200
        assert revoked.content == b""
        assert oauth_app.client.get("/v1/ping/", headers=_oauth_headers(rotated["access_token"])).status_code == 401


def test_scope_reduction_and_project_override_validation(oauth_app):
    _started, credential = _authorize(
        oauth_app,
        CLIENTS[0][0],
        approved_scopes=[protocol.MEMORY_READ_SCOPE],
    )
    headers = _oauth_headers(credential["access_token"])
    assert oauth_app.client.post("/search", headers=headers, json={"query": "ok"}).status_code == 200
    assert (
        oauth_app.client.post(
            "/search",
            headers=_oauth_headers(credential["access_token"], **{"X-Project-ID": DEFAULT_PROJECT_ID}),
            json={"query": "ok"},
        ).status_code
        == 200
    )
    assert (
        oauth_app.client.post(
            f"/search?project_id={DEFAULT_PROJECT_ID}", headers=headers, json={"query": "ok"}
        ).status_code
        == 200
    )
    assert (
        oauth_app.client.post(
            "/search",
            headers=headers,
            json={"query": "ok", "filters": {"project_id": DEFAULT_PROJECT_ID}},
        ).status_code
        == 200
    )

    write = oauth_app.client.post(
        "/memories",
        headers=headers,
        json={"messages": [{"role": "user", "content": "no"}], "user_id": "owner"},
    )
    assert write.status_code == 403
    assert _error_code(write) == "insufficient_scope"
    for response in (
        oauth_app.client.post(
            "/search",
            headers=_oauth_headers(credential["access_token"], **{"X-Project-ID": "other-project"}),
            json={"query": "no"},
        ),
        oauth_app.client.post("/search?project_id=other-project", headers=headers, json={"query": "no"}),
        oauth_app.client.post(
            "/search",
            headers=headers,
            json={"query": "no", "filters": {"project_id": "other-project"}},
        ),
    ):
        assert response.status_code == 403
        assert _error_code(response) == "project_scope_mismatch"


def test_device_polling_rejection_and_strict_form_errors(oauth_app):
    started, verifier = _device_request(oauth_app.client, CLIENTS[0][0])
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": started["device_code"],
        "client_id": CLIENTS[0][0],
        "code_verifier": verifier,
    }
    pending = oauth_app.client.post("/oauth/token", data=form)
    too_fast = oauth_app.client.post("/oauth/token", data=form)
    assert pending.status_code == too_fast.status_code == 400
    assert pending.json()["error"] == "authorization_pending"
    assert too_fast.json()["error"] == "slow_down"

    rejected_started, _rejected_verifier = _device_request(oauth_app.client, CLIENTS[0][0])
    lookup = oauth_app.client.post(
        "/oauth/device-requests/lookup",
        headers=_dashboard_headers(oauth_app.admin_token),
        json={"user_code": rejected_started["user_code"]},
    )
    rejected = oauth_app.client.post(
        f"/oauth/device-requests/{lookup.json()['id']}/reject",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert rejected.status_code == 200
    denied = oauth_app.client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": rejected_started["device_code"],
            "client_id": CLIENTS[0][0],
            "code_verifier": _rejected_verifier,
        },
    )
    assert denied.json()["error"] == "access_denied"

    wrong_type = oauth_app.client.post("/oauth/token", json=form)
    assert wrong_type.status_code == 415
    assert wrong_type.json()["protocol_version"] == protocol.PROTOCOL_VERSION
    assert wrong_type.headers["cache-control"] == "no-store, no-cache"

    duplicate = oauth_app.client.post(
        "/oauth/token",
        content=(
            "grant_type=refresh_token&client_id=desktop-client&client_id=desktop-client&refresh_token=yqor_invalid"
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    malformed = oauth_app.client.post(
        "/oauth/token",
        content="grant_type=%ZZ&unsupported=true",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert duplicate.status_code == malformed.status_code == 400
    assert duplicate.json()["error"] == malformed.json()["error"] == "invalid_request"


def test_refresh_replay_and_rfc7009_revocation(oauth_app):
    _started, credential = _authorize(oauth_app, CLIENTS[0][0])
    rotated_response = _refresh(oauth_app.client, CLIENTS[0][0], credential["refresh_token"])
    assert rotated_response.status_code == 200
    rotated = rotated_response.json()

    replay = _refresh(oauth_app.client, CLIENTS[0][0], credential["refresh_token"])
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert oauth_app.client.get("/v1/ping/", headers=_oauth_headers(rotated["access_token"])).status_code == 401
    with oauth_app.sessions() as db:
        grant = db.scalar(select(OAuthGrant).where(OAuthGrant.client_id == CLIENTS[0][0]))
        assert grant.status == "revoked"
        assert {token.status for token in grant.refresh_tokens} == {"revoked"}

    _started, next_credential = _authorize(oauth_app, CLIENTS[1][0])
    access_revocation = oauth_app.client.post(
        "/oauth/revoke",
        data={
            "token": next_credential["access_token"],
            "token_type_hint": "access_token",
            "client_id": CLIENTS[1][0],
        },
    )
    assert access_revocation.status_code == 200
    assert oauth_app.client.get("/v1/ping/", headers=_oauth_headers(next_credential["access_token"])).status_code == 401
    assert _refresh(oauth_app.client, CLIENTS[1][0], next_credential["refresh_token"]).status_code == 200

    unknown = oauth_app.client.post(
        "/oauth/revoke",
        data={"token": "yqoa_" + "x" * 43, "client_id": CLIENTS[1][0]},
    )
    assert unknown.status_code == 200
    assert unknown.content == b""


def test_last_used_updates_only_after_success_and_role_is_rechecked(oauth_app):
    _started, credential = _authorize(
        oauth_app,
        CLIENTS[0][0],
        dashboard_token=oauth_app.editor_token,
    )
    with oauth_app.sessions() as db:
        grant = db.scalar(select(OAuthGrant).where(OAuthGrant.client_id == CLIENTS[0][0]))
        application = db.get(OAuthApplication, CLIENTS[0][0])
        grant_id = grant.id
        application_last_used = application.last_used_at
        assert grant.last_used_at is None

    failed = oauth_app.client.post(
        "/memories",
        headers=_oauth_headers(credential["access_token"]),
        json={"messages": []},
    )
    assert failed.status_code == 400
    with oauth_app.sessions() as db:
        grant = db.get(OAuthGrant, grant_id)
        application = db.get(OAuthApplication, CLIENTS[0][0])
        assert grant.last_used_at is None
        assert application.last_used_at == application_last_used

    succeeded = oauth_app.client.post(
        "/memories",
        headers=_oauth_headers(credential["access_token"]),
        json={"messages": [{"role": "user", "content": "ok"}], "user_id": "editor"},
    )
    assert succeeded.status_code == 200
    with oauth_app.sessions() as db:
        assert db.get(OAuthGrant, grant_id).last_used_at is not None
        assert db.get(OAuthApplication, CLIENTS[0][0]).last_used_at != application_last_used

    def make_reader(workspace):
        workspace["members"][0]["role"] = "READER"

    _set_workspace(oauth_app, make_reader)
    denied_write = oauth_app.client.post(
        "/memories",
        headers=_oauth_headers(credential["access_token"]),
        json={"messages": [{"role": "user", "content": "no"}], "user_id": "editor"},
    )
    assert denied_write.status_code == 403
    assert denied_write.json()["detail"]["code"] == "access_denied"
    assert (
        oauth_app.client.post(
            "/search", headers=_oauth_headers(credential["access_token"]), json={"query": "still readable"}
        ).status_code
        == 200
    )

    def remove_bound_project(workspace):
        workspace["projects"] = [
            {
                "id": "remaining-project",
                "name": "Remaining project",
                "description": "",
                "organization_id": DEFAULT_ORG_ID,
            }
        ]
        workspace["active_project_id"] = "remaining-project"

    _set_workspace(oauth_app, remove_bound_project)
    removed_project = oauth_app.client.get(
        "/v1/ping/",
        headers=_oauth_headers(credential["access_token"]),
    )
    assert removed_project.status_code == 403
    assert _error_code(removed_project) == "access_denied"


def test_missing_audit_hmac_returns_stable_resource_error(oauth_app, monkeypatch):
    _started, credential = _authorize(oauth_app, CLIENTS[0][0])
    monkeypatch.delenv("OAUTH_AUDIT_HMAC_SECRET")
    response = oauth_app.client.get(
        "/v1/ping/",
        headers=_oauth_headers(credential["access_token"]),
    )
    assert response.status_code == 503
    assert _error_code(response) == "oauth_service_unavailable"
    assert response.json()["detail"]["request_id"]
    assert response.headers["cache-control"] == "no-store, no-cache"


def test_management_visibility_and_revocation(oauth_app):
    _started, _credential = _authorize(
        oauth_app,
        CLIENTS[0][0],
        dashboard_token=oauth_app.editor_token,
    )
    editor_grants = oauth_app.client.get(
        "/oauth/grants",
        headers=_dashboard_headers(oauth_app.editor_token),
    )
    assert editor_grants.status_code == 200
    assert editor_grants.json()["can_manage_project"] is False
    assert "owner_email" not in editor_grants.json()["items"][0]

    admin_grants = oauth_app.client.get(
        "/oauth/grants",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert admin_grants.status_code == 200
    assert admin_grants.json()["can_manage_project"] is True
    item = admin_grants.json()["items"][0]
    assert item["owner_email"] == "editor@example.com"
    serialized = admin_grants.text.lower()
    assert "access_token_hash" not in serialized
    assert "refresh_token" not in serialized

    revoked = oauth_app.client.post(
        f"/oauth/grants/{item['id']}/revoke",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


def test_nonstandard_api_keys_fail_closed_and_jwt_still_works(oauth_app):
    standard_key, standard_prefix, standard_hash = auth.generate_api_key()
    unknown_key, unknown_prefix, unknown_hash = auth.generate_api_key()
    with oauth_app.sessions() as db:
        db.add_all(
            [
                APIKey(
                    key_prefix=standard_prefix,
                    key_hash=standard_hash,
                    label="Standard",
                    key_type="standard",
                    project_id=DEFAULT_PROJECT_ID,
                    created_by=oauth_app.admin_id,
                ),
                APIKey(
                    key_prefix=unknown_prefix,
                    key_hash=unknown_hash,
                    label="Unknown",
                    key_type="legacy_connector",
                    project_id=DEFAULT_PROJECT_ID,
                    created_by=oauth_app.admin_id,
                ),
            ]
        )
        db.commit()

    assert oauth_app.client.get("/v1/ping/", headers={"X-API-Key": standard_key}).status_code == 200
    unknown = oauth_app.client.get("/v1/ping/", headers={"X-API-Key": unknown_key})
    assert unknown.status_code == 401
    assert _error_code(unknown) == "unsupported_key_type"
    assert (
        oauth_app.client.get("/v1/ping/", headers={"Authorization": f"Bearer {oauth_app.admin_token}"}).status_code
        == 200
    )


def test_oauth_requests_are_not_generically_logged_and_are_rate_limited(oauth_app, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 2)
    issued = [_device_request(oauth_app.client, CLIENTS[0][0])[0] for _index in range(2)]
    limited = oauth_app.client.post(
        "/oauth/device_authorization",
        data={
            "client_id": CLIENTS[0][0],
            "scope": "memory:read",
            "audience": protocol.AUDIENCE,
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["error"] == "temporarily_unavailable"
    assert limited.headers["cache-control"] == "no-store, no-cache"
    assert oauth_app.request_log.call_count == 0

    with oauth_app.sessions() as db:
        stored = json.dumps(
            [
                {
                    "remote": event.remote_ip_hash,
                    "agent": event.user_agent_hash,
                    "rate": event.rate_limit_key_hash,
                    "metadata": event.event_metadata,
                }
                for event in db.scalars(select(OAuthAuditEvent)).all()
            ]
        )
    for response in issued:
        assert response["device_code"] not in stored
        assert response["user_code"] not in stored
    assert "testclient" not in stored
    assert "testclient" not in stored.lower()
