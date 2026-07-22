# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Prune expired Public Service OAuth state in bounded, repeatable batches."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from db import SessionLocal
from models import (
    OAuthAuditEvent,
    OAuthDeviceAuthorization,
    OAuthGrant,
    OAuthRefreshToken,
)
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

DEFAULT_BATCH_SIZE = 500
DEFAULT_AUDIT_RETENTION_DAYS = 90
DEFAULT_REFRESH_REPLAY_GRACE_SECONDS = 24 * 60 * 60
MAX_BATCH_SIZE = 10_000


def _environment_integer(
    environment: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = environment.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _delete_in_batches(
    session: Session,
    model,
    criterion,
    *,
    order_by,
    batch_size: int,
) -> int:
    deleted = 0
    while True:
        identifiers = list(
            session.scalars(select(model.id).where(criterion).order_by(order_by, model.id).limit(batch_size))
        )
        if not identifiers:
            return deleted
        result = session.execute(
            delete(model).where(model.id.in_(identifiers)),
            execution_options={"synchronize_session": False},
        )
        session.commit()
        session.expire_all()
        deleted += int(result.rowcount or 0)


def prune_oauth(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    audit_retention_days: int = DEFAULT_AUDIT_RETENTION_DAYS,
    refresh_replay_grace_seconds: int = DEFAULT_REFRESH_REPLAY_GRACE_SECONDS,
) -> dict[str, int]:
    """Delete eligible rows while retaining every refresh replay hash long enough."""

    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if audit_retention_days < 1:
        raise ValueError("audit_retention_days must be >= 1")
    if refresh_replay_grace_seconds < 0:
        raise ValueError("refresh_replay_grace_seconds must be >= 0")

    now = now or datetime.now(timezone.utc)
    grant_cutoff = now - timedelta(seconds=refresh_replay_grace_seconds)
    audit_cutoff = now - timedelta(days=audit_retention_days)

    refresh_tokens = _delete_in_batches(
        session,
        OAuthRefreshToken,
        OAuthRefreshToken.retain_until < now,
        order_by=OAuthRefreshToken.retain_until,
        batch_size=batch_size,
    )
    grants = _delete_in_batches(
        session,
        OAuthGrant,
        (OAuthGrant.refresh_expires_at < grant_cutoff)
        & ~exists(select(OAuthRefreshToken.id).where(OAuthRefreshToken.grant_id == OAuthGrant.id)),
        order_by=OAuthGrant.refresh_expires_at,
        batch_size=batch_size,
    )
    device_authorizations = _delete_in_batches(
        session,
        OAuthDeviceAuthorization,
        (OAuthDeviceAuthorization.expires_at < now)
        & ~exists(select(OAuthGrant.id).where(OAuthGrant.device_authorization_id == OAuthDeviceAuthorization.id)),
        order_by=OAuthDeviceAuthorization.expires_at,
        batch_size=batch_size,
    )
    audit_events = _delete_in_batches(
        session,
        OAuthAuditEvent,
        OAuthAuditEvent.created_at < audit_cutoff,
        order_by=OAuthAuditEvent.created_at,
        batch_size=batch_size,
    )
    return {
        "device_authorizations": device_authorizations,
        "grants": grants,
        "refresh_tokens": refresh_tokens,
        "audit_events": audit_events,
    }


def main() -> int:
    try:
        batch_size = _environment_integer(
            os.environ,
            "OAUTH_CLEANUP_BATCH_SIZE",
            DEFAULT_BATCH_SIZE,
            minimum=1,
            maximum=MAX_BATCH_SIZE,
        )
        audit_retention_days = _environment_integer(
            os.environ,
            "OAUTH_AUDIT_RETENTION_DAYS",
            DEFAULT_AUDIT_RETENTION_DAYS,
            minimum=1,
        )
        refresh_replay_grace_seconds = _environment_integer(
            os.environ,
            "OAUTH_REFRESH_REPLAY_GRACE_SECONDS",
            DEFAULT_REFRESH_REPLAY_GRACE_SECONDS,
            minimum=0,
        )
    except ValueError as exc:
        sys.stderr.write(f"{exc}.\n")
        return 2

    with SessionLocal() as session:
        counts = prune_oauth(
            session,
            batch_size=batch_size,
            audit_retention_days=audit_retention_days,
            refresh_replay_grace_seconds=refresh_replay_grace_seconds,
        )
    sys.stdout.write("oauth_cleanup " + " ".join(f"{name}={count}" for name, count in counts.items()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
