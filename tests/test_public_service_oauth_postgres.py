# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""PostgreSQL row-lock coverage for OAuth credential mutations."""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

_SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import connector_protocol as protocol  # noqa: E402
import oauth_service  # noqa: E402
from models import (  # noqa: E402
    Base,
    OAuthApplication,
    OAuthAuditEvent,
    OAuthDeviceAuthorization,
    OAuthGrant,
    OAuthRefreshToken,
    Settings,
    User,
)
from workspace import DEFAULT_WORKSPACE_SETTINGS, WORKSPACE_KEY  # noqa: E402

_POSTGRES_DSN = os.environ.get("OAUTH_TEST_DATABASE_DSN", "").strip()
pytestmark = pytest.mark.skipif(
    not _POSTGRES_DSN,
    reason="OAUTH_TEST_DATABASE_DSN is not configured for PostgreSQL concurrency tests",
)
_HMAC_SECRET = "postgres-oauth-test-hmac-secret-at-least-32-bytes"


def _sqlalchemy_dsn(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    return value


@pytest.fixture
def postgres_oauth(monkeypatch):
    dsn = _sqlalchemy_dsn(_POSTGRES_DSN)
    schema = f"oauth_test_{uuid.uuid4().hex}"
    administrative_engine = create_engine(dsn, pool_pre_ping=True)
    with administrative_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        dsn,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    tables = [
        User.__table__,
        Settings.__table__,
        OAuthApplication.__table__,
        OAuthDeviceAuthorization.__table__,
        OAuthGrant.__table__,
        OAuthRefreshToken.__table__,
        OAuthAuditEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setenv("OAUTH_DEVICE_CODE_SECRET", _HMAC_SECRET)
    monkeypatch.setenv("OAUTH_AUDIT_HMAC_SECRET", _HMAC_SECRET)

    user_id = uuid.uuid4()
    client_id = "postgres-concurrency-client"
    verifier = "postgres-concurrency-verifier-that-is-long-enough-S256"
    device_code = protocol.generate_opaque_token(protocol.DEVICE_CODE_PREFIX)
    with sessions() as db:
        db.add_all(
            [
                User(
                    id=user_id,
                    name="PostgreSQL OAuth User",
                    email="postgres-oauth@example.com",
                    password_hash="unused",
                    role="admin",
                ),
                Settings(key=WORKSPACE_KEY, value=json.dumps(DEFAULT_WORKSPACE_SETTINGS)),
                OAuthApplication(
                    client_id=client_id,
                    display_name="PostgreSQL concurrency client",
                    client_type="public",
                    allowed_audiences=[protocol.AUDIENCE],
                    allowed_scopes=list(protocol.SUPPORTED_SCOPES),
                    status="active",
                    operator_metadata={},
                ),
                OAuthDeviceAuthorization(
                    id=uuid.uuid4(),
                    device_code_hash=protocol.hash_opaque_value(device_code),
                    user_code_hash=protocol.hash_user_code("ABCD-EFGH", _HMAC_SECRET),
                    client_id=client_id,
                    audience=protocol.AUDIENCE,
                    requested_scopes=list(protocol.SUPPORTED_SCOPES),
                    approved_scopes=list(protocol.SUPPORTED_SCOPES),
                    code_challenge=protocol.pkce_s256(verifier),
                    code_challenge_method="S256",
                    status="approved",
                    user_id=user_id,
                    project_id="default-project",
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=protocol.DEVICE_CODE_TTL_SECONDS),
                    approved_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()

    fixture = {
        "sessions": sessions,
        "client_id": client_id,
        "device_code": device_code,
        "verifier": verifier,
    }
    try:
        yield fixture
    finally:
        engine.dispose()
        with administrative_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administrative_engine.dispose()


def _context(index: int) -> oauth_service.AuditContext:
    suffix = f"{index:064x}"[-64:]
    return oauth_service.AuditContext(
        request_id=f"postgres-{index}",
        remote_ip_hash=suffix,
        user_agent_hash=suffix,
    )


def _exchange(postgres_oauth):
    with postgres_oauth["sessions"]() as db:
        return oauth_service.exchange_device_code(
            db,
            device_code=postgres_oauth["device_code"],
            client_id=postgres_oauth["client_id"],
            code_verifier=postgres_oauth["verifier"],
            context=_context(1),
        )


def _create_device_authorization(postgres_oauth, index: int):
    verifier = f"postgres-device-verifier-{index:02d}-that-is-long-enough-S256"
    with postgres_oauth["sessions"]() as db:
        return oauth_service.create_device_authorization(
            db,
            client_id=postgres_oauth["client_id"],
            scope=protocol.MEMORY_READ_SCOPE,
            audience=protocol.AUDIENCE,
            code_challenge=protocol.pkce_s256(verifier),
            code_challenge_method="S256",
            issuer="https://oauth-postgres.invalid",
            context=_context(index),
        )


def _run_concurrently(*functions):
    barrier = threading.Barrier(len(functions))

    def invoke(function):
        barrier.wait(timeout=10)
        try:
            return ("success", function())
        except oauth_service.OAuthProtocolError as exc:
            return (exc.error, None)

    with ThreadPoolExecutor(max_workers=len(functions)) as executor:
        return list(executor.map(invoke, functions))


def test_concurrent_device_exchange_issues_exactly_one_grant(postgres_oauth):
    outcomes = _run_concurrently(
        lambda: _exchange(postgres_oauth),
        lambda: _exchange(postgres_oauth),
    )
    assert [outcome for outcome, _result in outcomes].count("success") == 1
    assert sorted(outcome for outcome, _result in outcomes) == ["invalid_grant", "success"]
    with postgres_oauth["sessions"]() as db:
        assert db.scalar(select(func.count(OAuthGrant.id))) == 1
        assert db.scalar(select(func.count(OAuthRefreshToken.id))) == 1
        device = db.scalar(select(OAuthDeviceAuthorization))
        assert device.status == "exchanged"


def test_concurrent_rate_limit_enforces_exact_threshold(postgres_oauth, monkeypatch):
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 1)

    def enforce():
        with postgres_oauth["sessions"]() as db:
            oauth_service.enforce_rate_limit(
                db,
                "device_authorization",
                _context(20),
                client_id=postgres_oauth["client_id"],
            )
            db.commit()

    outcomes = _run_concurrently(enforce, enforce)
    assert [outcome for outcome, _result in outcomes].count("success") == 1
    assert [outcome for outcome, _result in outcomes].count("temporarily_unavailable") == 1
    with postgres_oauth["sessions"]() as db:
        events = db.scalars(
            select(OAuthAuditEvent).where(OAuthAuditEvent.event_type == "rate_limit.device_authorization")
        ).all()
        assert sum(event.outcome == "denied" for event in events) == 1


def test_concurrent_outstanding_cap_counts_pending_and_approved(postgres_oauth, monkeypatch):
    monkeypatch.setattr(oauth_service, "MAX_OUTSTANDING_DEVICE_AUTHORIZATIONS", 2)
    monkeypatch.setitem(oauth_service.PUBLIC_RATE_LIMITS, "device_authorization", 100)
    outcomes = _run_concurrently(
        lambda: _create_device_authorization(postgres_oauth, 30),
        lambda: _create_device_authorization(postgres_oauth, 31),
    )
    assert [outcome for outcome, _result in outcomes].count("success") == 1
    assert [outcome for outcome, _result in outcomes].count("temporarily_unavailable") == 1
    with postgres_oauth["sessions"]() as db:
        outstanding = db.scalars(
            select(OAuthDeviceAuthorization).where(
                OAuthDeviceAuthorization.status.in_(("pending", "approved")),
                OAuthDeviceAuthorization.expires_at > datetime.now(timezone.utc),
            )
        ).all()
        assert len(outstanding) == 2
        assert {device.status for device in outstanding} == {"pending", "approved"}


def test_concurrent_refresh_detects_replay_and_revokes_family(postgres_oauth):
    credential = _exchange(postgres_oauth)

    def refresh(index):
        with postgres_oauth["sessions"]() as db:
            return oauth_service.refresh_access_token(
                db,
                refresh_token=credential["refresh_token"],
                client_id=postgres_oauth["client_id"],
                context=_context(index),
            )

    outcomes = _run_concurrently(lambda: refresh(2), lambda: refresh(3))
    assert [outcome for outcome, _result in outcomes].count("success") == 1
    assert [outcome for outcome, _result in outcomes].count("invalid_grant") == 1
    with postgres_oauth["sessions"]() as db:
        grant = db.scalar(select(OAuthGrant))
        assert grant.status == "revoked"
        assert set(db.scalars(select(OAuthRefreshToken.status)).all()) == {"revoked"}


def test_concurrent_refresh_and_revocation_end_with_revoked_family(postgres_oauth):
    credential = _exchange(postgres_oauth)

    def refresh():
        with postgres_oauth["sessions"]() as db:
            return oauth_service.refresh_access_token(
                db,
                refresh_token=credential["refresh_token"],
                client_id=postgres_oauth["client_id"],
                context=_context(4),
            )

    def revoke():
        with postgres_oauth["sessions"]() as db:
            oauth_service.revoke_token(
                db,
                token=credential["refresh_token"],
                token_type_hint="refresh_token",
                client_id=postgres_oauth["client_id"],
                context=_context(5),
            )

    outcomes = _run_concurrently(refresh, revoke)
    assert any(outcome == "success" for outcome, _result in outcomes)
    with postgres_oauth["sessions"]() as db:
        grant = db.scalar(select(OAuthGrant))
        assert grant.status == "revoked"
        assert "active" not in set(db.scalars(select(OAuthRefreshToken.status)).all())
