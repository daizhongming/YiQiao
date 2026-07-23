# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

_SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import connector_protocol as protocol  # noqa: E402
import models  # noqa: E402

from scripts import prune_oauth  # noqa: E402


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_protocol_constants_and_credential_helpers():
    assert protocol.PROTOCOL_VERSION == "1.0"
    assert protocol.SERVICE_ID == "yiqiao"
    assert protocol.AUDIENCE == "yiqiao:memory-api"
    assert protocol.SUPPORTED_SCOPES == ("memory:read", "memory:write")
    assert protocol.ACCESS_TOKEN_TTL_SECONDS == 900
    assert protocol.DEVICE_CODE_TTL_SECONDS == 600
    assert protocol.REFRESH_TOKEN_TTL_SECONDS == 2_592_000

    token = protocol.generate_opaque_token(protocol.ACCESS_TOKEN_PREFIX)
    token_hash = protocol.hash_opaque_value(token)
    assert token.startswith("yqoa_")
    assert len(token_hash) == 64
    assert token not in token_hash
    assert protocol.opaque_value_matches(token, token_hash)
    assert not protocol.opaque_value_matches(token + "x", token_hash)
    assert protocol.credential_prefix(token) == token[:12]

    secret = "dedicated-test-user-code-secret"
    expected = protocol.hash_user_code("ABCD-EFGH", secret)
    assert protocol.user_code_matches("abcd efgh", expected, secret)
    assert not protocol.user_code_matches("ABCD-EFGJ", expected, secret)
    assert not protocol.user_code_matches("ABCD-EFGH!", expected, secret)
    assert not protocol.user_code_matches("ABCD-\u8bb0\u5fc6", expected, secret)

    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert protocol.pkce_s256(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert protocol.normalize_scopes("memory:write memory:read memory:write") == (
        "memory:read",
        "memory:write",
    )
    with pytest.raises(ValueError, match="unsupported scopes"):
        protocol.normalize_scopes("memory:read admin")


def test_exact_oauth_model_set_and_relationships():
    oauth_models = {
        name
        for name, value in vars(models).items()
        if name.startswith("OAuth") and isinstance(value, type) and hasattr(value, "__table__")
    }
    assert oauth_models == {
        "OAuthApplication",
        "OAuthDeviceAuthorization",
        "OAuthGrant",
        "OAuthRefreshToken",
        "OAuthAuditEvent",
    }
    assert not hasattr(models, "BossHelperPairing")
    assert {
        "oauth_applications",
        "oauth_device_authorizations",
        "oauth_grants",
        "oauth_refresh_tokens",
        "oauth_audit_events",
    }.issubset(models.Base.metadata.tables)

    assert set(sa.inspect(models.OAuthApplication).relationships.keys()) == {
        "device_authorizations",
        "grants",
        "audit_events",
    }
    assert set(sa.inspect(models.OAuthDeviceAuthorization).relationships.keys()) == {
        "application",
        "user",
        "grant",
        "audit_events",
    }
    assert set(sa.inspect(models.OAuthGrant).relationships.keys()) == {
        "application",
        "device_authorization",
        "user",
        "refresh_tokens",
        "audit_events",
    }
    assert set(sa.inspect(models.OAuthRefreshToken).relationships.keys()) == {"grant", "replaced_by"}
    assert "access_token_prefix" not in models.OAuthGrant.__table__.columns
    assert "token_prefix" not in models.OAuthRefreshToken.__table__.columns


def test_prune_oauth_is_batched_repeatable_and_preserves_replay_window():
    engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    user_id = uuid.uuid4()
    old_grant_id = uuid.uuid4()
    live_grant_id = uuid.uuid4()

    with sessions() as session:
        application = models.OAuthApplication(
            client_id="cleanup-test-client",
            display_name="Cleanup test",
            client_type="public",
            allowed_audiences=[protocol.AUDIENCE],
            allowed_scopes=list(protocol.SUPPORTED_SCOPES),
            status="active",
            operator_metadata={},
        )
        user = models.User(
            id=user_id,
            name="OAuth Tester",
            email="oauth-cleanup@example.com",
            password_hash="unused",
            role="admin",
        )
        old_device = models.OAuthDeviceAuthorization(
            id=uuid.uuid4(),
            device_code_hash=_digest("old-device"),
            user_code_hash=_digest("old-user-code"),
            client_id=application.client_id,
            audience=protocol.AUDIENCE,
            requested_scopes=list(protocol.SUPPORTED_SCOPES),
            approved_scopes=list(protocol.SUPPORTED_SCOPES),
            code_challenge="a" * 43,
            status="exchanged",
            user_id=user_id,
            project_id="old-project",
            expires_at=now - timedelta(days=3),
        )
        orphan_device = models.OAuthDeviceAuthorization(
            id=uuid.uuid4(),
            device_code_hash=_digest("orphan-device"),
            user_code_hash=_digest("orphan-user-code"),
            client_id=application.client_id,
            audience=protocol.AUDIENCE,
            requested_scopes=[protocol.MEMORY_READ_SCOPE],
            code_challenge="b" * 43,
            status="expired",
            expires_at=now - timedelta(days=2),
        )
        live_device = models.OAuthDeviceAuthorization(
            id=uuid.uuid4(),
            device_code_hash=_digest("live-device"),
            user_code_hash=_digest("live-user-code"),
            client_id=application.client_id,
            audience=protocol.AUDIENCE,
            requested_scopes=[protocol.MEMORY_READ_SCOPE],
            approved_scopes=[protocol.MEMORY_READ_SCOPE],
            code_challenge="c" * 43,
            status="exchanged",
            user_id=user_id,
            project_id="live-project",
            expires_at=now - timedelta(days=1),
        )
        old_grant = models.OAuthGrant(
            id=old_grant_id,
            device_authorization_id=old_device.id,
            client_id=application.client_id,
            user_id=user_id,
            project_id="old-project",
            audience=protocol.AUDIENCE,
            scopes=list(protocol.SUPPORTED_SCOPES),
            status="revoked",
            access_token_hash=_digest("old-access"),
            access_expires_at=now - timedelta(days=4),
            refresh_expires_at=now - timedelta(days=2),
            revoked_at=now - timedelta(days=2),
        )
        live_grant = models.OAuthGrant(
            id=live_grant_id,
            device_authorization_id=live_device.id,
            client_id=application.client_id,
            user_id=user_id,
            project_id="live-project",
            audience=protocol.AUDIENCE,
            scopes=[protocol.MEMORY_READ_SCOPE],
            status="revoked",
            access_token_hash=_digest("live-access"),
            access_expires_at=now - timedelta(hours=1),
            refresh_expires_at=now + timedelta(days=10),
            revoked_at=now,
        )
        old_refresh = models.OAuthRefreshToken(
            id=uuid.uuid4(),
            grant_id=old_grant_id,
            family_id=old_grant_id,
            token_hash=_digest("old-refresh"),
            status="rotated",
            expires_at=now - timedelta(days=3),
            rotated_at=now - timedelta(days=3),
            retain_until=now - timedelta(days=1),
        )
        retained_refresh = models.OAuthRefreshToken(
            id=uuid.uuid4(),
            grant_id=live_grant_id,
            family_id=live_grant_id,
            token_hash=_digest("retained-refresh"),
            status="rotated",
            expires_at=now - timedelta(hours=1),
            rotated_at=now - timedelta(hours=1),
            retain_until=now + timedelta(days=10, hours=1),
        )
        old_audits = [
            models.OAuthAuditEvent(
                client_id=application.client_id,
                grant_id=old_grant_id,
                user_id=user_id,
                project_id="old-project",
                event_type=f"old_event_{index}",
                outcome="success",
                event_metadata={},
                created_at=now - timedelta(days=120 + index),
            )
            for index in range(2)
        ]
        recent_audit = models.OAuthAuditEvent(
            client_id=application.client_id,
            grant_id=live_grant_id,
            user_id=user_id,
            project_id="live-project",
            event_type="recent_event",
            outcome="success",
            event_metadata={},
            created_at=now - timedelta(days=1),
        )
        old_device_id = old_device.id
        orphan_device_id = orphan_device.id
        live_device_id = live_device.id
        retained_refresh_id = retained_refresh.id
        session.add_all(
            [
                application,
                user,
                old_device,
                orphan_device,
                live_device,
                old_grant,
                live_grant,
                old_refresh,
                retained_refresh,
                *old_audits,
                recent_audit,
            ]
        )
        session.commit()

        counts = prune_oauth.prune_oauth(
            session,
            now=now,
            batch_size=1,
            audit_retention_days=90,
            refresh_replay_grace_seconds=24 * 60 * 60,
        )
        assert counts == {
            "device_authorizations": 2,
            "grants": 1,
            "refresh_tokens": 1,
            "audit_events": 2,
        }
        assert session.get(models.OAuthGrant, old_grant_id) is None
        assert session.get(models.OAuthDeviceAuthorization, old_device_id) is None
        assert session.get(models.OAuthDeviceAuthorization, orphan_device_id) is None
        assert session.get(models.OAuthGrant, live_grant_id) is not None
        assert session.get(models.OAuthDeviceAuthorization, live_device_id) is not None
        assert session.get(models.OAuthRefreshToken, retained_refresh_id) is not None
        assert session.scalar(select(sa.func.count()).select_from(models.OAuthAuditEvent)) == 1

        assert prune_oauth.prune_oauth(
            session,
            now=now,
            batch_size=1,
            audit_retention_days=90,
            refresh_replay_grace_seconds=24 * 60 * 60,
        ) == {
            "device_authorizations": 0,
            "grants": 0,
            "refresh_tokens": 0,
            "audit_events": 0,
        }
    engine.dispose()


def test_cleanup_environment_validation():
    assert prune_oauth._environment_integer({}, "VALUE", 7, minimum=1) == 7
    with pytest.raises(ValueError, match="must be >= 1"):
        prune_oauth._environment_integer({"VALUE": "0"}, "VALUE", 7, minimum=1)
    with pytest.raises(ValueError, match="refresh_replay_grace_seconds must be >= 1"):
        prune_oauth.prune_oauth(None, refresh_replay_grace_seconds=0)
    with pytest.raises(ValueError, match="must be an integer"):
        prune_oauth._environment_integer({"VALUE": "not-an-int"}, "VALUE", 7, minimum=1)
    with pytest.raises(ValueError, match="must be <= 10"):
        prune_oauth._environment_integer({"VALUE": "11"}, "VALUE", 7, minimum=1, maximum=10)
