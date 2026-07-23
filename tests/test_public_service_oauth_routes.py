# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Real-app acceptance coverage for Public Service Connector OAuth 1.0."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, func, select  # noqa: E402
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
    OAuthDeviceAuthorization,
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
    monkeypatch.setenv("OAUTH_ALLOW_INSECURE_LOOPBACK", "true")
    monkeypatch.setenv("OAUTH_DEVICE_CODE_SECRET", OAUTH_SECRET)
    monkeypatch.setenv("OAUTH_AUDIT_HMAC_SECRET", OAUTH_SECRET)
    monkeypatch.setenv("OAUTH_PROXY_HMAC_SECRET", OAUTH_SECRET)
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


def _signed_proxy_headers(
    method: str,
    path_and_query: str,
    remote_ip: str,
    *,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    payload = f"v1\n{timestamp_text}\n{method.upper()}\n{path_and_query}\n{remote_ip}".encode()
    signature = hmac.new(OAUTH_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return {
        "x-yiqiao-proxy-client-ip": remote_ip,
        "x-yiqiao-proxy-timestamp": timestamp_text,
        "x-yiqiao-proxy-signature": signature,
    }


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


def _lookup_device(oauth_app, user_code: str, dashboard_token: str | None = None) -> dict:
    response = oauth_app.client.post(
        "/oauth/device-requests/lookup",
        headers=_dashboard_headers(dashboard_token or oauth_app.admin_token),
        json={"user_code": user_code},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_device(
    oauth_app,
    started: dict,
    *,
    approved_scopes: list[str] | None = None,
    dashboard_token: str | None = None,
):
    lookup = _lookup_device(oauth_app, started["user_code"], dashboard_token)
    approval_body = {"project_id": DEFAULT_PROJECT_ID}
    if approved_scopes is not None:
        approval_body["approved_scopes"] = approved_scopes
    return oauth_app.client.post(
        f"/oauth/device-requests/{lookup['id']}/approve",
        headers=_dashboard_headers(dashboard_token or oauth_app.admin_token),
        json=approval_body,
    )


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
    approval = _approve_device(
        oauth_app,
        started,
        approved_scopes=approved_scopes,
        dashboard_token=dashboard_token,
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


def _set_public_rate_limits(monkeypatch, limit: int) -> None:
    for operation in tuple(oauth_service.PUBLIC_RATE_LIMITS):
        monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, operation, limit)


def test_discovery_health_and_application_dtos_are_safe(oauth_app):
    metadata = oauth_app.client.get("/.well-known/oauth-authorization-server")
    capabilities = oauth_app.client.get("/.well-known/service-capabilities")
    health = oauth_app.client.get("/api/health")
    assert metadata.status_code == capabilities.status_code == health.status_code == 200
    assert metadata.json()["issuer"] == capabilities.json()["issuer"] == ISSUER
    assert metadata.json()["device_authorization_endpoint"] == f"{ISSUER}/oauth/device_authorization"
    assert capabilities.json()["health_endpoint"] == f"{ISSUER}/api/health"
    assert capabilities.json()["memory_api"]["ping_endpoint"] == f"{ISSUER}/v1/ping/"
    assert capabilities.json()["project_selection"]["required"] is True
    assert health.json() == {"status": "ok"}
    for response in (metadata, capabilities):
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


def test_insecure_issuer_requires_explicit_loopback_opt_in(oauth_app, monkeypatch):
    monkeypatch.setenv("OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OAUTH_ALLOW_INSECURE_LOOPBACK", "false")
    configured_denied = oauth_app.client.get("/.well-known/oauth-authorization-server")
    assert configured_denied.status_code == 503
    assert configured_denied.json()["error"] == "temporarily_unavailable"

    monkeypatch.setenv("OAUTH_ALLOW_INSECURE_LOOPBACK", "true")
    configured_allowed = oauth_app.client.get("/.well-known/oauth-authorization-server")
    assert configured_allowed.status_code == 200
    assert configured_allowed.json()["issuer"] == ISSUER

    monkeypatch.setenv("OAUTH_ISSUER", "http://connector.example.com")
    nonloopback_denied = oauth_app.client.get("/.well-known/oauth-authorization-server")
    assert nonloopback_denied.status_code == 503
    assert nonloopback_denied.json()["error"] == "temporarily_unavailable"


def test_derived_issuer_uses_server_socket_not_forwarded_headers(oauth_app, monkeypatch):
    monkeypatch.delenv("OAUTH_ISSUER", raising=False)
    monkeypatch.setenv("OAUTH_ALLOW_INSECURE_LOOPBACK", "false")
    with TestClient(
        oauth_app.client.app,
        base_url="https://127.0.0.1:3200",
        client=("127.0.0.1", 50002),
    ) as loopback_client:
        denied = loopback_client.get("/.well-known/oauth-authorization-server")
        assert denied.status_code == 503

        monkeypatch.setenv("OAUTH_ALLOW_INSECURE_LOOPBACK", "true")
        derived = loopback_client.get(
            "/.well-known/oauth-authorization-server",
            headers={
                "Host": "attacker.example",
                "X-Forwarded-Host": "forwarded.example",
                "X-Forwarded-Proto": "https",
            },
        )
    assert derived.status_code == 200
    assert derived.json()["issuer"] == "http://127.0.0.1:3200"
    assert "attacker.example" not in derived.text
    assert "forwarded.example" not in derived.text


def test_unknown_clients_are_metered_without_fk_bearing_audit_persistence(oauth_app):
    unknown_client = "unknown-public-client"
    verifier = "unknown-client-verifier-that-is-long-enough-for-S256"
    responses = [
        oauth_app.client.post(
            "/oauth/device_authorization",
            data={
                "client_id": unknown_client,
                "scope": protocol.MEMORY_READ_SCOPE,
                "audience": protocol.AUDIENCE,
                "code_challenge": protocol.pkce_s256(verifier),
                "code_challenge_method": "S256",
            },
        ),
        oauth_app.client.post(
            "/oauth/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": protocol.generate_opaque_token(protocol.DEVICE_CODE_PREFIX),
                "client_id": unknown_client,
                "code_verifier": verifier,
            },
        ),
        oauth_app.client.post(
            "/oauth/revoke",
            data={
                "token": protocol.generate_opaque_token(protocol.ACCESS_TOKEN_PREFIX),
                "client_id": unknown_client,
            },
        ),
    ]
    for response in responses:
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"
        assert response.headers["cache-control"] == "no-store, no-cache"

    with oauth_app.sessions() as db:
        assert db.scalar(select(func.count(OAuthDeviceAuthorization.id))) == 0
        assert db.scalar(select(func.count(OAuthGrant.id))) == 0
        assert db.scalar(select(func.count(OAuthRefreshToken.id))) == 0
        audits = db.scalars(select(OAuthAuditEvent)).all()
        assert len(audits) == 6
        assert all(event.client_id is None for event in audits)
        assert all(event.rate_limit_key_hash for event in audits)


def test_repeated_unknown_client_is_rate_limited_without_fk_persistence(oauth_app, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 1)
    verifier = "unknown-rate-verifier-that-is-long-enough-for-S256"
    form = {
        "client_id": "unknown-rate-client",
        "scope": protocol.MEMORY_READ_SCOPE,
        "audience": protocol.AUDIENCE,
        "code_challenge": protocol.pkce_s256(verifier),
        "code_challenge_method": "S256",
    }

    first = oauth_app.client.post("/oauth/device_authorization", data=form)
    limited = oauth_app.client.post("/oauth/device_authorization", data=form)

    assert first.status_code == 401
    assert first.json()["error"] == "invalid_client"
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)
    with oauth_app.sessions() as db:
        assert db.scalar(select(func.count(OAuthDeviceAuthorization.id))) == 0
        assert all(event.client_id is None for event in db.scalars(select(OAuthAuditEvent)).all())


def test_forwarding_headers_cannot_spoof_the_direct_client_rate_bucket(oauth_app, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 1)
    verifier = "direct-peer-rate-verifier-that-is-long-enough-for-S256"

    def request(client_id: str, spoofed_ip: str):
        return oauth_app.client.post(
            "/oauth/device_authorization",
            headers={
                "X-Forwarded-For": spoofed_ip,
                "X-YiQiao-Transport-Peer": spoofed_ip,
            },
            data={
                "client_id": client_id,
                "scope": protocol.MEMORY_READ_SCOPE,
                "audience": protocol.AUDIENCE,
                "code_challenge": protocol.pkce_s256(verifier),
                "code_challenge_method": "S256",
            },
        )

    assert request("unknown-direct-one", "203.0.113.10").status_code == 401
    limited = request("unknown-direct-two", "198.51.100.20")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)


def test_signed_proxy_context_uses_distinct_ip_buckets_and_fails_closed(oauth_app, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 1)
    verifier = "signed-peer-rate-verifier-that-is-long-enough-for-S256"
    path = "/oauth/device_authorization"

    def request(client_id: str, headers: dict[str, str]):
        return oauth_app.client.post(
            path,
            headers=headers,
            data={
                "client_id": client_id,
                "scope": protocol.MEMORY_READ_SCOPE,
                "audience": protocol.AUDIENCE,
                "code_challenge": protocol.pkce_s256(verifier),
                "code_challenge_method": "S256",
            },
        )

    tampered = _signed_proxy_headers("POST", path, "203.0.113.30")
    tampered["x-yiqiao-proxy-signature"] = "0" * 64
    assert request("unknown-tampered", tampered).status_code == 400
    stale = _signed_proxy_headers("POST", path, "203.0.113.31", timestamp=int(time.time()) - 120)
    assert request("unknown-stale", stale).status_code == 400

    first_ip = _signed_proxy_headers("POST", path, "203.0.113.40")
    second_ip = _signed_proxy_headers("POST", path, "198.51.100.40")
    assert request("unknown-signed-one", first_ip).status_code == 401
    assert request("unknown-signed-two", first_ip).status_code == 429
    assert request("unknown-signed-three", second_ip).status_code == 401


def test_two_public_clients_use_the_same_lifecycle(oauth_app):
    for client_id, _display_name in CLIENTS:
        started, credential = _authorize(oauth_app, client_id)
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
            device = db.get(OAuthDeviceAuthorization, grant.device_authorization_id)
            serialized_rows = json.dumps(
                {
                    "device": {column.name: getattr(device, column.name) for column in device.__table__.columns},
                    "grant": {column.name: getattr(grant, column.name) for column in grant.__table__.columns},
                    "refresh": {column.name: getattr(refresh, column.name) for column in refresh.__table__.columns},
                },
                default=str,
            )
            for secret in (
                started["device_code"],
                started["user_code"],
                credential["access_token"],
                credential["refresh_token"],
            ):
                assert secret not in serialized_rows
                assert secret[:12] not in serialized_rows

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


def test_audience_and_application_scope_intersection_fail_closed(oauth_app):
    verifier = "audience-scope-verifier-that-is-long-enough-for-S256"
    base_form = {
        "client_id": CLIENTS[0][0],
        "scope": protocol.MEMORY_READ_SCOPE,
        "audience": protocol.AUDIENCE,
        "code_challenge": protocol.pkce_s256(verifier),
        "code_challenge_method": "S256",
    }
    wrong_audience = oauth_app.client.post(
        "/oauth/device_authorization",
        data={**base_form, "audience": "yiqiao:other-api"},
    )
    assert wrong_audience.status_code == 400
    assert wrong_audience.json()["error"] == "invalid_target"

    unsupported_scope = oauth_app.client.post(
        "/oauth/device_authorization",
        data={**base_form, "scope": "memory:delete"},
    )
    assert unsupported_scope.status_code == 400
    assert unsupported_scope.json()["error"] == "invalid_scope"

    with oauth_app.sessions() as db:
        application = db.get(OAuthApplication, CLIENTS[0][0])
        application.allowed_scopes = [protocol.MEMORY_READ_SCOPE]
        db.commit()
    outside_application_scope = oauth_app.client.post(
        "/oauth/device_authorization",
        data={**base_form, "scope": protocol.MEMORY_WRITE_SCOPE},
    )
    assert outside_application_scope.status_code == 400
    assert outside_application_scope.json()["error"] == "invalid_scope"

    started, verifier = _device_request(oauth_app.client, CLIENTS[1][0])
    approval = _approve_device(oauth_app, started)
    assert approval.status_code == 200
    with oauth_app.sessions() as db:
        application = db.get(OAuthApplication, CLIENTS[1][0])
        application.allowed_scopes = [protocol.MEMORY_READ_SCOPE]
        db.commit()
    narrowed_after_approval = oauth_app.client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": started["device_code"],
            "client_id": CLIENTS[1][0],
            "code_verifier": verifier,
        },
    )
    assert narrowed_after_approval.status_code == 400
    assert narrowed_after_approval.json()["error"] == "access_denied"
    with oauth_app.sessions() as db:
        device = db.scalar(
            select(OAuthDeviceAuthorization).where(
                OAuthDeviceAuthorization.device_code_hash == protocol.hash_opaque_value(started["device_code"])
            )
        )
        assert device.status == "denied"
        assert db.scalar(select(func.count(OAuthGrant.id))) == 0


def test_approval_cannot_expand_requested_scopes(oauth_app):
    started, verifier = _device_request(
        oauth_app.client,
        CLIENTS[0][0],
        protocol.MEMORY_READ_SCOPE,
    )
    expanded = _approve_device(
        oauth_app,
        started,
        approved_scopes=[protocol.MEMORY_READ_SCOPE, protocol.MEMORY_WRITE_SCOPE],
    )
    assert expanded.status_code == 400
    assert "non-empty subset" in expanded.json()["detail"]

    approved = _approve_device(
        oauth_app,
        started,
        approved_scopes=[protocol.MEMORY_READ_SCOPE],
    )
    assert approved.status_code == 200
    exchange = oauth_app.client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": started["device_code"],
            "client_id": CLIENTS[0][0],
            "code_verifier": verifier,
        },
    )
    assert exchange.status_code == 200
    assert exchange.json()["scope"] == protocol.MEMORY_READ_SCOPE


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
    duplicate_header_response = oauth_app.client.post(
        "/search",
        headers=[
            ("Authorization", f"Bearer {credential['access_token']}"),
            ("X-Project-ID", DEFAULT_PROJECT_ID),
            ("X-Project-ID", "other-project"),
        ],
        json={"query": "no"},
    )
    structured_json_response = oauth_app.client.post(
        "/search",
        headers={**headers, "Content-Type": "application/merge-patch+json"},
        content=json.dumps({"query": "no", "filters": {"project_id": "other-project"}}),
    )
    missing_content_type_response = oauth_app.client.post(
        "/search",
        headers=headers,
        content=json.dumps({"query": "no", "filters": {"project_id": "other-project"}}),
    )
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
        duplicate_header_response,
        structured_json_response,
        missing_content_type_response,
    ):
        assert response.status_code == 403
        assert _error_code(response) == "project_scope_mismatch"


def test_operator_and_request_metadata_cannot_override_approved_project(oauth_app):
    metadata_project = "metadata-supplied-project"
    client_id = "metadata-project-client"
    registered = oauth_app.client.post(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
        json={
            "client_id": client_id,
            "display_name": "Metadata project client",
            "operator_metadata": {"project_id": metadata_project},
        },
    )
    assert registered.status_code == 201, registered.text

    _started, credential = _authorize(oauth_app, client_id)
    assert credential["project"] == DEFAULT_PROJECT_ID
    with oauth_app.sessions() as db:
        grant = db.scalar(select(OAuthGrant).where(OAuthGrant.client_id == client_id))
        assert grant.project_id == DEFAULT_PROJECT_ID

    same_project = oauth_app.client.post(
        "/memories",
        headers=_oauth_headers(credential["access_token"]),
        json={
            "messages": [{"role": "user", "content": "same project"}],
            "user_id": "metadata-owner",
            "metadata": {"project_id": DEFAULT_PROJECT_ID},
        },
    )
    assert same_project.status_code == 200
    override = oauth_app.client.post(
        "/memories",
        headers=_oauth_headers(credential["access_token"]),
        json={
            "messages": [{"role": "user", "content": "wrong project"}],
            "user_id": "metadata-owner",
            "metadata": {"project_id": metadata_project},
        },
    )
    assert override.status_code == 403
    assert _error_code(override) == "project_scope_mismatch"


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

    for unsupported_grant in ("client_credentials", "urn:ietf:params:oauth:grant-type:token-exchange"):
        response = oauth_app.client.post(
            "/oauth/token",
            data={"grant_type": unsupported_grant, "client_id": CLIENTS[0][0]},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"


def test_oauth_error_codes_use_an_explicit_allowlist():
    for error in (
        "access_denied",
        "authorization_pending",
        "expired_token",
        "invalid_client",
        "invalid_grant",
        "invalid_request",
        "invalid_scope",
        "invalid_target",
        "slow_down",
        "temporarily_unavailable",
        "unsupported_grant_type",
    ):
        assert oauth_service.OAuthProtocolError(error, "safe").error == error
    with pytest.raises(ValueError, match="allowlisted"):
        oauth_service.OAuthProtocolError("invented_error", "unsafe")


def test_bad_pkce_and_cross_client_credential_use_do_not_consume_credentials(oauth_app):
    started, verifier = _device_request(oauth_app.client, CLIENTS[0][0])
    assert _approve_device(oauth_app, started).status_code == 200
    exchange_form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": started["device_code"],
        "client_id": CLIENTS[0][0],
        "code_verifier": verifier,
    }

    bad_pkce = oauth_app.client.post(
        "/oauth/token",
        data={
            **exchange_form,
            "code_verifier": "wrong-verifier-value-that-is-long-enough-for-pkce-S256",
        },
    )
    assert bad_pkce.status_code == 400
    assert bad_pkce.json()["error"] == "invalid_grant"

    cross_client_exchange = oauth_app.client.post(
        "/oauth/token",
        data={**exchange_form, "client_id": CLIENTS[1][0]},
    )
    assert cross_client_exchange.status_code == 400
    assert cross_client_exchange.json()["error"] == "invalid_grant"

    exchanged = oauth_app.client.post("/oauth/token", data=exchange_form)
    assert exchanged.status_code == 200, exchanged.text
    credential = exchanged.json()
    reused_device_code = oauth_app.client.post("/oauth/token", data=exchange_form)
    assert reused_device_code.status_code == 400
    assert reused_device_code.json()["error"] == "invalid_grant"
    cross_client_refresh = _refresh(
        oauth_app.client,
        CLIENTS[1][0],
        credential["refresh_token"],
    )
    assert cross_client_refresh.status_code == 400
    assert cross_client_refresh.json()["error"] == "invalid_grant"

    cross_client_revoke = oauth_app.client.post(
        "/oauth/revoke",
        data={
            "token": credential["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": CLIENTS[1][0],
        },
    )
    assert cross_client_revoke.status_code == 200
    assert (
        oauth_app.client.get(
            "/v1/ping/",
            headers=_oauth_headers(credential["access_token"]),
        ).status_code
        == 200
    )
    assert (
        _refresh(
            oauth_app.client,
            CLIENTS[0][0],
            credential["refresh_token"],
        ).status_code
        == 200
    )


def test_failed_pkce_attempt_consumes_the_shared_rate_budget(oauth_app, monkeypatch):
    started, verifier = _device_request(oauth_app.client, CLIENTS[0][0])
    assert _approve_device(oauth_app, started).status_code == 200
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "token", 1)
    form = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": started["device_code"],
        "client_id": CLIENTS[0][0],
        "code_verifier": verifier,
    }

    failed = oauth_app.client.post(
        "/oauth/token",
        data={**form, "code_verifier": "wrong-verifier-value-that-is-long-enough-for-pkce-S256"},
    )
    limited = oauth_app.client.post("/oauth/token", data=form)

    assert failed.status_code == 400
    assert failed.json()["error"] == "invalid_grant"
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)


def test_public_form_and_client_id_errors_consume_the_ip_rate_budget(oauth_app, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 2)
    wrong_content_type = oauth_app.client.post(
        "/oauth/device_authorization",
        json={"client_id": CLIENTS[0][0]},
    )
    invalid_client_id = oauth_app.client.post(
        "/oauth/device_authorization",
        data={
            "client_id": "invalid client id",
            "scope": protocol.MEMORY_READ_SCOPE,
            "audience": protocol.AUDIENCE,
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )
    limited = oauth_app.client.post(
        "/oauth/device_authorization",
        data={
            "client_id": CLIENTS[0][0],
            "scope": protocol.MEMORY_READ_SCOPE,
            "audience": protocol.AUDIENCE,
            "code_challenge": "a" * 43,
            "code_challenge_method": "S256",
        },
    )

    assert wrong_content_type.status_code == 415
    assert invalid_client_id.status_code == 401
    assert invalid_client_id.json()["error"] == "invalid_client"
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)


def test_device_code_secret_has_no_admin_key_fallback(oauth_app, monkeypatch):
    monkeypatch.delenv("OAUTH_DEVICE_CODE_SECRET", raising=False)
    monkeypatch.setenv("ADMIN_API_KEY", "admin-fallback-must-not-protect-device-codes")
    verifier = "missing-device-secret-verifier-long-enough-for-S256"
    authorization = oauth_app.client.post(
        "/oauth/device_authorization",
        data={
            "client_id": CLIENTS[0][0],
            "scope": protocol.MEMORY_READ_SCOPE,
            "audience": protocol.AUDIENCE,
            "code_challenge": protocol.pkce_s256(verifier),
            "code_challenge_method": "S256",
        },
    )
    lookup = oauth_app.client.post(
        "/oauth/device-requests/lookup",
        headers=_dashboard_headers(oauth_app.admin_token),
        json={"user_code": "ABCD-EFGH"},
    )

    assert authorization.status_code == 503
    assert authorization.json()["error"] == "temporarily_unavailable"
    assert lookup.status_code == 503
    assert "configuration is incomplete" in lookup.json()["detail"]
    with oauth_app.sessions() as db:
        assert db.scalar(select(func.count(OAuthDeviceAuthorization.id))) == 0


def test_expired_device_and_access_tokens_fail_closed(oauth_app):
    started, verifier = _device_request(oauth_app.client, CLIENTS[0][0])
    assert _approve_device(oauth_app, started).status_code == 200
    with oauth_app.sessions() as db:
        device = db.scalar(
            select(OAuthDeviceAuthorization).where(
                OAuthDeviceAuthorization.device_code_hash == protocol.hash_opaque_value(started["device_code"])
            )
        )
        device.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    expired_device = oauth_app.client.post(
        "/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": started["device_code"],
            "client_id": CLIENTS[0][0],
            "code_verifier": verifier,
        },
    )
    assert expired_device.status_code == 400
    assert expired_device.json()["error"] == "expired_token"
    with oauth_app.sessions() as db:
        device = db.scalar(
            select(OAuthDeviceAuthorization).where(
                OAuthDeviceAuthorization.device_code_hash == protocol.hash_opaque_value(started["device_code"])
            )
        )
        assert device.status == "expired"

    _started, credential = _authorize(oauth_app, CLIENTS[1][0])
    with oauth_app.sessions() as db:
        grant = db.scalar(select(OAuthGrant).where(OAuthGrant.client_id == CLIENTS[1][0]))
        grant.access_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    expired_access = oauth_app.client.get(
        "/v1/ping/",
        headers=_oauth_headers(credential["access_token"]),
    )
    assert expired_access.status_code == 401
    assert _error_code(expired_access) == "invalid_token"
    assert 'error="invalid_token"' in expired_access.headers["www-authenticate"]
    assert (
        _refresh(
            oauth_app.client,
            CLIENTS[1][0],
            credential["refresh_token"],
        ).status_code
        == 200
    )


@pytest.mark.parametrize("configured_grace", ["0", "-60"])
def test_refresh_replay_retention_has_positive_minimum(oauth_app, monkeypatch, configured_grace):
    monkeypatch.setenv("OAUTH_REFRESH_REPLAY_GRACE_SECONDS", configured_grace)
    _started, credential = _authorize(oauth_app, CLIENTS[0][0])
    with oauth_app.sessions() as db:
        refresh = db.scalar(
            select(OAuthRefreshToken).where(
                OAuthRefreshToken.token_hash == protocol.hash_opaque_value(credential["refresh_token"])
            )
        )
        assert refresh.retain_until > refresh.expires_at


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
    assert (
        oauth_app.client.get(
            "/v1/ping/",
            headers=_oauth_headers(credential["access_token"]),
        ).status_code
        == 200
    )
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


def test_application_registry_is_admin_only(oauth_app):
    member_headers = _dashboard_headers(oauth_app.editor_token)
    member_list = oauth_app.client.get("/oauth/applications", headers=member_headers)
    assert member_list.status_code == 403

    member_registration = oauth_app.client.post(
        "/oauth/applications",
        headers=member_headers,
        json={
            "client_id": "member-created-client",
            "display_name": "Member-created client",
        },
    )
    assert member_registration.status_code == 403
    member_revocation = oauth_app.client.post(
        f"/oauth/applications/{CLIENTS[0][0]}/revoke",
        headers=member_headers,
    )
    assert member_revocation.status_code == 403

    admin_list = oauth_app.client.get(
        "/oauth/applications",
        headers=_dashboard_headers(oauth_app.admin_token),
    )
    assert admin_list.status_code == 200
    assert admin_list.json()["can_register"] is True
    assert {item["client_id"] for item in admin_list.json()["items"]} == {
        client_id for client_id, _display_name in CLIENTS
    }


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
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)
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


@pytest.mark.parametrize("shared_identity", ["ip", "client"])
def test_device_authorization_rate_limit_uses_ip_and_client_identities(
    oauth_app,
    monkeypatch,
    shared_identity,
):
    _set_public_rate_limits(monkeypatch, 1)
    _device_request(oauth_app.client, CLIENTS[0][0], protocol.MEMORY_READ_SCOPE)

    second_client_id = CLIENTS[1][0] if shared_identity == "ip" else CLIENTS[0][0]
    if shared_identity == "ip":
        second_client = oauth_app.client
        close_second_client = False
    else:
        second_client = TestClient(
            oauth_app.client.app,
            client=("198.51.100.23", 50001),
        )
        close_second_client = True
    try:
        verifier = "rate-limit-verifier-value-that-is-long-enough-for-S256"
        limited = second_client.post(
            "/oauth/device_authorization",
            data={
                "client_id": second_client_id,
                "scope": protocol.MEMORY_READ_SCOPE,
                "audience": protocol.AUDIENCE,
                "code_challenge": protocol.pkce_s256(verifier),
                "code_challenge_method": "S256",
            },
        )
    finally:
        if close_second_client:
            second_client.close()

    assert limited.status_code == 429
    assert limited.json()["error"] == "temporarily_unavailable"
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_device_decision_routes_are_rate_limited(oauth_app, monkeypatch, decision):
    _set_public_rate_limits(monkeypatch, 1)
    started, _verifier = _device_request(
        oauth_app.client,
        CLIENTS[0][0],
        protocol.MEMORY_READ_SCOPE,
    )
    lookup = _lookup_device(oauth_app, started["user_code"])
    url = f"/oauth/device-requests/{lookup['id']}/{decision}"
    kwargs = {
        "headers": _dashboard_headers(oauth_app.admin_token),
    }
    if decision == "approve":
        kwargs["json"] = {
            "project_id": DEFAULT_PROJECT_ID,
            "approved_scopes": [protocol.MEMORY_READ_SCOPE],
        }

    first = oauth_app.client.post(url, **kwargs)
    limited = oauth_app.client.post(url, **kwargs)
    assert first.status_code == 200, first.text
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)


def test_application_registration_is_rate_limited(oauth_app, monkeypatch):
    _set_public_rate_limits(monkeypatch, 1)
    headers = _dashboard_headers(oauth_app.admin_token)
    first = oauth_app.client.post(
        "/oauth/applications",
        headers=headers,
        json={"client_id": "rate-client-one", "display_name": "Rate client one"},
    )
    limited = oauth_app.client.post(
        "/oauth/applications",
        headers=headers,
        json={"client_id": "rate-client-two", "display_name": "Rate client two"},
    )
    assert first.status_code == 201, first.text
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == str(oauth_service.PUBLIC_RATE_WINDOW_SECONDS)
