# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Core operations for Public Service Connector OAuth grants."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from connector_protocol import (
    ACCESS_TOKEN_PREFIX,
    ACCESS_TOKEN_TTL_SECONDS,
    AUDIENCE,
    DEVICE_CODE_PREFIX,
    DEVICE_CODE_TTL_SECONDS,
    MEMORY_READ_SCOPE,
    MEMORY_WRITE_SCOPE,
    PROTOCOL_VERSION,
    REFRESH_TOKEN_PREFIX,
    REFRESH_TOKEN_TTL_SECONDS,
    SERVICE_ID,
    SUPPORTED_SCOPES,
    generate_opaque_token,
    generate_user_code,
    hash_opaque_value,
    hash_user_code,
    is_valid_pkce_verifier,
    normalize_scopes,
    normalize_user_code,
    pkce_s256,
)
from errors import request_id_var
from fastapi import HTTPException, Request
from models import (
    OAuthApplication,
    OAuthAuditEvent,
    OAuthDeviceAuthorization,
    OAuthGrant,
    OAuthRefreshToken,
    User,
)
from settings_store import get_json
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    member_role,
    role_allows_manage,
    role_allows_read,
    role_allows_write,
)

DEVICE_POLL_INTERVAL_SECONDS = 5
REFRESH_REPLAY_RETENTION_SECONDS = 24 * 60 * 60
PUBLIC_RATE_WINDOW_SECONDS = 60
PUBLIC_RATE_LIMITS = {
    "device_authorization": 20,
    "token": 120,
    "revocation": 120,
    "device_lookup": 30,
    "device_approval": 30,
    "device_rejection": 30,
    "application_registration": 10,
}
MAX_OUTSTANDING_DEVICE_AUTHORIZATIONS = 10

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{3,128}$")
_PKCE_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ALLOWED_OAUTH_ERRORS = frozenset(
    {
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
    }
)
_METADATA_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SENSITIVE_METADATA_KEYS = {"authorization", "credential", "password", "secret", "token"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_PROXY_CLIENT_IP_HEADER = "x-yiqiao-proxy-client-ip"
_PROXY_TIMESTAMP_HEADER = "x-yiqiao-proxy-timestamp"
_PROXY_SIGNATURE_HEADER = "x-yiqiao-proxy-signature"
_PROXY_CONTEXT_MAX_AGE_SECONDS = 30


class OAuthProtocolError(Exception):
    def __init__(
        self,
        error: str,
        description: str,
        *,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(description)
        if error not in _ALLOWED_OAUTH_ERRORS:
            raise ValueError("OAuth error codes must be allowlisted identifiers")
        self.error = error
        self.description = description[:240]
        self.status_code = status_code
        self.headers = headers or {}


@dataclass(frozen=True)
class AuditContext:
    request_id: str | None
    remote_ip_hash: str | None
    user_agent_hash: str | None


@dataclass(frozen=True)
class ResourceAuthorization:
    user: User
    grant_id: uuid.UUID
    client_id: str
    project_id: str
    audience: str
    scopes: tuple[str, ...]
    access_token_hash: str
    audit_context: AuditContext


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _required_secret(name: str) -> bytes:
    value = os.environ.get(name, "").strip()
    if len(value.encode("utf-8")) < 32:
        raise OAuthProtocolError(
            "temporarily_unavailable",
            "OAuth service configuration is incomplete.",
            status_code=503,
        )
    return value.encode("utf-8")


def _context_digest(kind: str, value: str) -> str:
    key = _required_secret("OAUTH_AUDIT_HMAC_SECRET")
    return hmac.new(key, f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _signed_proxy_remote_ip(request: Request) -> str | None:
    header_names = (
        _PROXY_CLIENT_IP_HEADER,
        _PROXY_TIMESTAMP_HEADER,
        _PROXY_SIGNATURE_HEADER,
    )
    values = {name: request.headers.getlist(name) for name in header_names}
    if not any(values.values()):
        return None
    if any(len(items) != 1 or not items[0].strip() for items in values.values()):
        raise OAuthProtocolError("invalid_request", "The connector proxy context is invalid.")

    remote_ip = values[_PROXY_CLIENT_IP_HEADER][0].strip()
    timestamp_text = values[_PROXY_TIMESTAMP_HEADER][0].strip()
    signature = values[_PROXY_SIGNATURE_HEADER][0].strip().lower()
    try:
        normalized_ip = str(ipaddress.ip_address(remote_ip))
        timestamp = int(timestamp_text)
    except (TypeError, ValueError) as exc:
        raise OAuthProtocolError("invalid_request", "The connector proxy context is invalid.") from exc

    now = int(time.time())
    if timestamp > now + 5 or now - timestamp > _PROXY_CONTEXT_MAX_AGE_SECONDS:
        raise OAuthProtocolError("invalid_request", "The connector proxy context has expired.")
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise OAuthProtocolError("invalid_request", "The connector proxy context is invalid.")

    path_and_query = request.url.path
    if request.url.query:
        path_and_query = f"{path_and_query}?{request.url.query}"
    payload = f"v1\n{timestamp_text}\n{request.method.upper()}\n{path_and_query}\n{remote_ip}".encode()
    expected = hmac.new(_required_secret("OAUTH_PROXY_HMAC_SECRET"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise OAuthProtocolError("invalid_request", "The connector proxy context is invalid.")
    return normalized_ip


def audit_context(request: Request) -> AuditContext:
    remote_ip = _signed_proxy_remote_ip(request)
    if remote_ip is None:
        remote_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    return AuditContext(
        request_id=request_id_var.get()[:64] or None,
        remote_ip_hash=_context_digest("remote-ip", remote_ip) if remote_ip else None,
        user_agent_hash=_context_digest("user-agent", user_agent) if user_agent else None,
    )


def _user_code_secret() -> bytes:
    return _required_secret("OAUTH_DEVICE_CODE_SECRET")


def _insecure_loopback_allowed() -> bool:
    return os.environ.get("OAUTH_ALLOW_INSECURE_LOOPBACK", "").strip().lower() in _TRUE_VALUES


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower().rstrip(".") == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validated_issuer(value: str, *, allow_insecure_loopback: bool = False) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("OAUTH_ISSUER must be an origin URL without credentials, path, query, or fragment.")
    if parsed.scheme != "https" and not (allow_insecure_loopback and _is_loopback(parsed.hostname)):
        raise RuntimeError("OAUTH_ISSUER must use HTTPS unless explicit local loopback development is enabled.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError("OAUTH_ISSUER contains an invalid port.") from exc
    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def issuer_for_request(request: Request) -> str:
    allow_insecure_loopback = _insecure_loopback_allowed()
    configured = os.environ.get("OAUTH_ISSUER", "").strip()
    if configured:
        return _validated_issuer(configured, allow_insecure_loopback=allow_insecure_loopback)

    if not allow_insecure_loopback:
        raise RuntimeError("OAUTH_ISSUER is required outside explicit loopback development.")

    server = request.scope.get("server")
    if not isinstance(server, (tuple, list)) or len(server) != 2:
        raise RuntimeError("OAUTH_ISSUER is required outside loopback development.")
    host, port = str(server[0]), int(server[1])
    if not _is_loopback(host):
        raise RuntimeError("OAUTH_ISSUER is required outside loopback development.")
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    default_port = 80
    authority = rendered_host if port == default_port else f"{rendered_host}:{port}"
    return _validated_issuer(
        f"http://{authority}",
        allow_insecure_loopback=True,
    )


def authorization_server_metadata(issuer: str) -> dict[str, Any]:
    return {
        "issuer": issuer,
        "device_authorization_endpoint": f"{issuer}/oauth/device_authorization",
        "token_endpoint": f"{issuer}/oauth/token",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "grant_types_supported": [
            "urn:ietf:params:oauth:grant-type:device_code",
            "refresh_token",
        ],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "protocol_version": PROTOCOL_VERSION,
    }


def service_capabilities(issuer: str) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "service_id": SERVICE_ID,
        "issuer": issuer,
        "oauth_metadata": f"{issuer}/.well-known/oauth-authorization-server",
        "audiences": [AUDIENCE],
        "health_endpoint": f"{issuer}/api/health",
        "project_selection": {
            "required": True,
            "performed_during_authorization": True,
        },
        "memory_api": {
            "search_endpoint": f"{issuer}/search",
            "write_endpoint": f"{issuer}/memories",
            "ping_endpoint": f"{issuer}/v1/ping/",
            "scopes": {
                "read": MEMORY_READ_SCOPE,
                "write": MEMORY_WRITE_SCOPE,
            },
        },
    }


def _audit(
    db: Session,
    event_type: str,
    outcome: str,
    *,
    context: AuditContext | None = None,
    client_id: str | None = None,
    device: OAuthDeviceAuthorization | None = None,
    grant: OAuthGrant | None = None,
    user_id: uuid.UUID | None = None,
    project_id: str | None = None,
    rate_limit_key_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> OAuthAuditEvent:
    event = OAuthAuditEvent(
        client_id=client_id or (grant.client_id if grant is not None else None),
        device_authorization_id=device.id if device is not None else None,
        grant_id=grant.id if grant is not None else None,
        user_id=user_id or (grant.user_id if grant is not None else None),
        project_id=project_id or (grant.project_id if grant is not None else None),
        event_type=event_type[:64],
        outcome=outcome,
        request_id=context.request_id if context else None,
        remote_ip_hash=context.remote_ip_hash if context else None,
        user_agent_hash=context.user_agent_hash if context else None,
        rate_limit_key_hash=rate_limit_key_hash,
        event_metadata=metadata or {},
    )
    db.add(event)
    return event


def _rate_limit_identities(
    context: AuditContext,
    client_id: str | None,
    *,
    include_remote_ip: bool = True,
) -> tuple[str, ...]:
    identities = []
    if include_remote_ip and context.remote_ip_hash:
        identities.append(f"ip:{context.remote_ip_hash}")
    if client_id:
        identities.append(f"client:{client_id}")
    if not identities:
        identities.append("connection:unknown")
    return tuple(identities)


def _rate_limit_keys(operation: str, identities: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_context_digest("rate-limit", f"{operation}:{identity}") for identity in identities)


def _acquire_rate_limit_locks(db: Session, rate_keys: tuple[str, ...]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    lock_keys = sorted(
        {int.from_bytes(bytes.fromhex(rate_key)[:8], byteorder="big", signed=True) for rate_key in rate_keys}
    )
    for lock_key in lock_keys:
        db.execute(select(func.pg_advisory_xact_lock(lock_key)))


def enforce_rate_limit(
    db: Session,
    operation: str,
    context: AuditContext,
    *,
    client_id: str | None = None,
    persist_client_id: bool = True,
    include_remote_ip: bool = True,
) -> None:
    limit = PUBLIC_RATE_LIMITS[operation]
    cutoff = _now() - timedelta(seconds=PUBLIC_RATE_WINDOW_SECONDS)
    event_type = f"rate_limit.{operation}"
    identities = _rate_limit_identities(
        context,
        client_id,
        include_remote_ip=include_remote_ip,
    )
    rate_keys = _rate_limit_keys(operation, identities)
    _acquire_rate_limit_locks(db, rate_keys)
    audit_client_id = client_id if persist_client_id else None
    for rate_key in rate_keys:
        count = db.scalar(
            select(func.count(OAuthAuditEvent.id)).where(
                OAuthAuditEvent.event_type == event_type,
                OAuthAuditEvent.rate_limit_key_hash == rate_key,
                OAuthAuditEvent.created_at >= cutoff,
            )
        )
        if int(count or 0) >= limit:
            _audit(
                db,
                event_type,
                "denied",
                context=context,
                client_id=audit_client_id,
                rate_limit_key_hash=rate_key,
                metadata={"reason": "rate_limited"},
            )
            db.commit()
            raise OAuthProtocolError(
                "temporarily_unavailable",
                "Too many requests. Retry shortly.",
                status_code=429,
                headers={"Retry-After": str(PUBLIC_RATE_WINDOW_SECONDS)},
            )
    for rate_key in rate_keys:
        _audit(
            db,
            event_type,
            "success",
            context=context,
            client_id=audit_client_id,
            rate_limit_key_hash=rate_key,
        )
    db.commit()


def enforce_management_rate_limit(
    db: Session,
    operation: str,
    context: AuditContext,
    *,
    client_id: str | None = None,
    persist_client_id: bool = True,
    include_remote_ip: bool = True,
) -> None:
    try:
        enforce_rate_limit(
            db,
            operation,
            context,
            client_id=client_id,
            persist_client_id=persist_client_id,
            include_remote_ip=include_remote_ip,
        )
    except OAuthProtocolError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.description,
            headers=exc.headers,
        ) from exc


def _enforce_outstanding_device_limit(
    db: Session,
    *,
    application: OAuthApplication,
    context: AuditContext,
) -> None:
    now = _now()
    identities = _rate_limit_identities(context, application.client_id)
    rate_keys = _rate_limit_keys("device_authorization", identities)
    _acquire_rate_limit_locks(db, rate_keys)
    counts: list[tuple[str, str, int]] = []
    for identity, rate_key in zip(identities, rate_keys, strict=True):
        query = select(func.count(func.distinct(OAuthDeviceAuthorization.id))).where(
            OAuthDeviceAuthorization.status.in_(("pending", "approved")),
            OAuthDeviceAuthorization.expires_at > now,
        )
        if identity.startswith("client:"):
            query = query.where(OAuthDeviceAuthorization.client_id == application.client_id)
        else:
            query = query.join(
                OAuthAuditEvent,
                OAuthAuditEvent.device_authorization_id == OAuthDeviceAuthorization.id,
            ).where(
                OAuthAuditEvent.event_type == "device_authorization.created",
                OAuthAuditEvent.remote_ip_hash == context.remote_ip_hash,
            )
        counts.append((identity, rate_key, int(db.scalar(query) or 0)))

    for identity, rate_key, count in counts:
        if count < MAX_OUTSTANDING_DEVICE_AUTHORIZATIONS:
            continue
        _audit(
            db,
            "rate_limit.device_outstanding",
            "denied",
            context=context,
            client_id=application.client_id,
            rate_limit_key_hash=rate_key,
            metadata={"identity_kind": identity.split(":", 1)[0], "reason": "outstanding_limit"},
        )
        db.commit()
        raise OAuthProtocolError(
            "temporarily_unavailable",
            "Too many active device authorizations. Retry shortly.",
            status_code=429,
            headers={"Retry-After": str(PUBLIC_RATE_WINDOW_SECONDS)},
        )


def _application_record(db: Session, client_id: str) -> OAuthApplication | None:
    return db.scalar(
        select(OAuthApplication)
        .where(OAuthApplication.client_id == client_id)
        .execution_options(populate_existing=True)
    )


def _application(db: Session, client_id: str) -> OAuthApplication:
    application = _application_record(db, client_id)
    if application is None or application.status != "active" or application.client_type != "public":
        raise OAuthProtocolError("invalid_client", "The public client is not registered or active.", status_code=401)
    return application


def _rate_limited_application(
    db: Session,
    operation: str,
    context: AuditContext,
    client_id: str,
) -> OAuthApplication:
    known_client = (
        db.scalar(select(OAuthApplication.client_id).where(OAuthApplication.client_id == client_id)) is not None
    )
    enforce_rate_limit(
        db,
        operation,
        context,
        client_id=client_id,
        persist_client_id=known_client,
        include_remote_ip=False,
    )
    return _application(db, client_id)


def rate_limit_public_client(
    db: Session,
    operation: str,
    context: AuditContext,
    client_id: str,
) -> str:
    validated_client_id = _validated_client_id(client_id)
    _rate_limited_application(db, operation, context, validated_client_id)
    return validated_client_id


def _validated_client_id(client_id: str) -> str:
    value = client_id.strip()
    if not _CLIENT_ID_RE.fullmatch(value):
        raise OAuthProtocolError("invalid_client", "The public client identifier is invalid.", status_code=401)
    return value


def _validated_requested_scopes(application: OAuthApplication, raw_scopes: str) -> tuple[str, ...]:
    try:
        scopes = normalize_scopes(raw_scopes)
    except ValueError as exc:
        raise OAuthProtocolError("invalid_scope", "One or more requested scopes are unsupported.") from exc
    if not scopes or not set(scopes).issubset(set(application.allowed_scopes or [])):
        raise OAuthProtocolError("invalid_scope", "One or more requested scopes are not allowed for this client.")
    return scopes


def _device_response(device: OAuthDeviceAuthorization, application: OAuthApplication) -> dict[str, Any]:
    return {
        "id": str(device.id),
        "client_id": device.client_id,
        "application_name": application.display_name,
        "audience": device.audience,
        "requested_scopes": list(device.requested_scopes or []),
        "approved_scopes": list(device.approved_scopes or []),
        "status": device.status,
        "project_id": device.project_id,
        "expires_at": device.expires_at,
        "created_at": device.created_at,
    }


def create_device_authorization(
    db: Session,
    *,
    client_id: str,
    scope: str,
    audience: str,
    code_challenge: str,
    code_challenge_method: str,
    issuer: str,
    context: AuditContext,
    rate_limit_client: bool = True,
) -> dict[str, Any]:
    client_id = _validated_client_id(client_id)
    application = (
        _rate_limited_application(db, "device_authorization", context, client_id)
        if rate_limit_client
        else _application(db, client_id)
    )
    scopes = _validated_requested_scopes(application, scope)
    if audience != AUDIENCE or audience not in (application.allowed_audiences or []):
        raise OAuthProtocolError("invalid_target", "The requested audience is not allowed.")
    if code_challenge_method != "S256" or not _PKCE_CHALLENGE_RE.fullmatch(code_challenge):
        raise OAuthProtocolError("invalid_request", "A valid PKCE S256 code challenge is required.")
    _enforce_outstanding_device_limit(db, application=application, context=context)

    now = _now()
    for _attempt in range(5):
        device_code = generate_opaque_token(DEVICE_CODE_PREFIX)
        user_code = generate_user_code()
        device = OAuthDeviceAuthorization(
            device_code_hash=hash_opaque_value(device_code),
            user_code_hash=hash_user_code(user_code, _user_code_secret()),
            client_id=client_id,
            audience=audience,
            requested_scopes=list(scopes),
            code_challenge=code_challenge,
            code_challenge_method="S256",
            status="pending",
            interval_seconds=DEVICE_POLL_INTERVAL_SECONDS,
            expires_at=now + timedelta(seconds=DEVICE_CODE_TTL_SECONDS),
        )
        try:
            with db.begin_nested():
                db.add(device)
                db.flush()
            break
        except IntegrityError:
            continue
    else:
        raise OAuthProtocolError(
            "temporarily_unavailable",
            "Unable to create a device authorization.",
            status_code=503,
        )

    application.last_used_at = now
    _audit(db, "device_authorization.created", "success", context=context, client_id=client_id, device=device)
    db.commit()
    verification_uri = f"{issuer}/dashboard/connected-apps"
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": f"{verification_uri}?user_code={quote(user_code, safe='-')}",
        "expires_in": DEVICE_CODE_TTL_SECONDS,
        "interval": DEVICE_POLL_INTERVAL_SECONDS,
        "protocol_version": PROTOCOL_VERSION,
    }


def _expire_device_if_needed(db: Session, device: OAuthDeviceAuthorization, now: datetime) -> bool:
    if device.status in {"pending", "approved"} and _utc(device.expires_at) <= now:
        device.status = "expired"
        db.commit()
        return True
    return device.status == "expired" or _utc(device.expires_at) <= now


def lookup_device_request(
    db: Session,
    *,
    user_code: str,
    context: AuditContext,
) -> dict[str, Any]:
    enforce_rate_limit(db, "device_lookup", context)
    normalized = normalize_user_code(user_code)
    if not normalized:
        raise HTTPException(status_code=404, detail="Device request not found.")
    code_hash = hash_user_code(normalized, _user_code_secret())
    device = db.scalar(select(OAuthDeviceAuthorization).where(OAuthDeviceAuthorization.user_code_hash == code_hash))
    if device is None:
        raise HTTPException(status_code=404, detail="Device request not found.")
    now = _now()
    _expire_device_if_needed(db, device, now)
    application = db.get(OAuthApplication, device.client_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Device request not found.")
    _audit(db, "device_authorization.lookup", "success", context=context, device=device)
    db.commit()
    return _device_response(device, application)


def _workspace(db: Session) -> dict[str, Any]:
    return get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)


def _project_role(user: User, project_id: str, workspace: dict[str, Any]) -> str | None:
    if user.role == "admin":
        return "OWNER"
    return member_role(workspace, user.email, project_id)


def _role_allows_scopes(role: str | None, scopes: tuple[str, ...] | list[str]) -> bool:
    scope_set = set(scopes)
    if MEMORY_WRITE_SCOPE in scope_set and not role_allows_write(role):
        return False
    if MEMORY_READ_SCOPE in scope_set and not role_allows_read(role):
        return False
    return True


def approve_device_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    user: User,
    project_id: str,
    approved_scopes: list[str] | None,
    context: AuditContext,
) -> dict[str, Any]:
    initial_device = db.get(OAuthDeviceAuthorization, request_id)
    initial_client_id = initial_device.client_id if initial_device is not None else None
    enforce_management_rate_limit(
        db,
        "device_approval",
        context,
        client_id=initial_client_id,
        persist_client_id=initial_client_id is not None,
    )
    device = db.scalar(
        select(OAuthDeviceAuthorization)
        .where(OAuthDeviceAuthorization.id == request_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Device request not found.")
    now = _now()
    if _expire_device_if_needed(db, device, now):
        raise HTTPException(status_code=410, detail="Device request has expired.")
    if device.status == "denied":
        raise HTTPException(status_code=409, detail="Device request has already been rejected.")
    if device.status == "exchanged":
        raise HTTPException(status_code=409, detail="Device request has already been exchanged.")
    application = db.get(OAuthApplication, device.client_id)
    if application is None or application.status != "active" or application.client_type != "public":
        raise HTTPException(status_code=409, detail="The OAuth application is no longer active.")

    try:
        scopes = normalize_scopes(approved_scopes if approved_scopes is not None else device.requested_scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Approved scopes contain an unsupported value.") from exc
    if not scopes or not set(scopes).issubset(set(device.requested_scopes or [])):
        raise HTTPException(status_code=400, detail="Approved scopes must be a non-empty subset of requested scopes.")
    workspace = _workspace(db)
    if find_project(workspace, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not _role_allows_scopes(_project_role(user, project_id, workspace), scopes):
        raise HTTPException(status_code=403, detail="Project access does not allow the approved scopes.")

    if device.status == "approved":
        if (
            device.user_id != user.id
            or device.project_id != project_id
            or tuple(device.approved_scopes or []) != scopes
        ):
            raise HTTPException(status_code=409, detail="Device request was already approved with different access.")
    else:
        device.status = "approved"
        device.user_id = user.id
        device.project_id = project_id
        device.approved_scopes = list(scopes)
        device.approved_at = now
    _audit(
        db,
        "device_authorization.approved",
        "success",
        context=context,
        device=device,
        user_id=user.id,
        project_id=project_id,
        metadata={"scopes": list(scopes)},
    )
    db.commit()
    return _device_response(device, application)


def reject_device_request(
    db: Session,
    *,
    request_id: uuid.UUID,
    user: User,
    context: AuditContext,
) -> dict[str, Any]:
    initial_device = db.get(OAuthDeviceAuthorization, request_id)
    initial_client_id = initial_device.client_id if initial_device is not None else None
    enforce_management_rate_limit(
        db,
        "device_rejection",
        context,
        client_id=initial_client_id,
        persist_client_id=initial_client_id is not None,
    )
    device = db.scalar(
        select(OAuthDeviceAuthorization)
        .where(OAuthDeviceAuthorization.id == request_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Device request not found.")
    now = _now()
    if _expire_device_if_needed(db, device, now):
        raise HTTPException(status_code=410, detail="Device request has expired.")
    if device.status == "approved":
        raise HTTPException(status_code=409, detail="Approved device requests cannot be rejected.")
    if device.status == "exchanged":
        raise HTTPException(status_code=409, detail="Device request has already been exchanged.")
    application = db.get(OAuthApplication, device.client_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Device request not found.")
    if device.status == "pending":
        device.status = "denied"
        device.user_id = user.id
        device.denied_at = now
    _audit(
        db,
        "device_authorization.denied",
        "denied",
        context=context,
        device=device,
        user_id=user.id,
    )
    db.commit()
    return _device_response(device, application)


def _refresh_retention_seconds() -> int:
    raw = os.environ.get("OAUTH_REFRESH_REPLAY_GRACE_SECONDS", "").strip()
    if not raw:
        return REFRESH_REPLAY_RETENTION_SECONDS
    try:
        return max(1, min(int(raw), 30 * 24 * 60 * 60))
    except ValueError:
        return REFRESH_REPLAY_RETENTION_SECONDS


def _token_response(access_token: str, refresh_token: str, grant: OAuthGrant, now: datetime) -> dict[str, Any]:
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": max(0, int((_utc(grant.access_expires_at) - now).total_seconds())),
        "refresh_token": refresh_token,
        "refresh_expires_in": max(0, int((_utc(grant.refresh_expires_at) - now).total_seconds())),
        "scope": " ".join(grant.scopes or []),
        "audience": grant.audience,
        "project": grant.project_id,
        "protocol_version": PROTOCOL_VERSION,
    }


def exchange_device_code(
    db: Session,
    *,
    device_code: str,
    client_id: str,
    code_verifier: str,
    context: AuditContext,
    rate_limit_client: bool = True,
) -> dict[str, Any]:
    client_id = _validated_client_id(client_id)
    if rate_limit_client:
        _rate_limited_application(db, "token", context, client_id)
    else:
        _application(db, client_id)
    device_hash = hash_opaque_value(device_code) if device_code else ""
    device = db.scalar(
        select(OAuthDeviceAuthorization)
        .where(OAuthDeviceAuthorization.device_code_hash == device_hash)
        .with_for_update()
    )
    if device is None or device.client_id != client_id:
        raise OAuthProtocolError("invalid_grant", "The device authorization is invalid.")
    now = _now()
    if _expire_device_if_needed(db, device, now):
        raise OAuthProtocolError("expired_token", "The device authorization has expired.")
    if device.status == "pending":
        if device.last_polled_at is not None:
            next_poll = _utc(device.last_polled_at) + timedelta(seconds=device.interval_seconds)
            if now < next_poll:
                device.interval_seconds += 5
                device.last_polled_at = now
                device.poll_count += 1
                _audit(
                    db, "token.device_poll", "denied", context=context, device=device, metadata={"result": "slow_down"}
                )
                db.commit()
                raise OAuthProtocolError(
                    "slow_down",
                    "Polling is faster than the authorized interval.",
                    headers={"Retry-After": str(device.interval_seconds)},
                )
        device.last_polled_at = now
        device.poll_count += 1
        _audit(
            db,
            "token.device_poll",
            "denied",
            context=context,
            device=device,
            metadata={"result": "authorization_pending"},
        )
        db.commit()
        raise OAuthProtocolError("authorization_pending", "The user has not completed authorization.")
    if device.status == "denied":
        raise OAuthProtocolError("access_denied", "The device authorization was denied.")
    if device.status != "approved":
        raise OAuthProtocolError("invalid_grant", "The device authorization is no longer usable.")
    if not is_valid_pkce_verifier(code_verifier) or not hmac.compare_digest(
        pkce_s256(code_verifier), device.code_challenge
    ):
        _audit(db, "token.device_exchange", "denied", context=context, device=device, metadata={"reason": "pkce"})
        db.commit()
        raise OAuthProtocolError("invalid_grant", "PKCE verification failed.")

    application = _application(db, client_id)
    user = db.get(User, device.user_id) if device.user_id else None
    scopes = tuple(device.approved_scopes or [])
    workspace = _workspace(db)
    if (
        user is None
        or device.project_id is None
        or find_project(workspace, device.project_id) is None
        or not _role_allows_scopes(_project_role(user, device.project_id, workspace), scopes)
        or not set(scopes).issubset(set(application.allowed_scopes or []))
        or device.audience not in (application.allowed_audiences or [])
    ):
        device.status = "denied"
        device.denied_at = now
        _audit(
            db,
            "token.device_exchange",
            "denied",
            context=context,
            device=device,
            metadata={"reason": "entitlement"},
        )
        db.commit()
        raise OAuthProtocolError("access_denied", "The approved access is no longer available.")

    access_token = generate_opaque_token(ACCESS_TOKEN_PREFIX)
    refresh_token = generate_opaque_token(REFRESH_TOKEN_PREFIX)
    refresh_expires_at = now + timedelta(seconds=REFRESH_TOKEN_TTL_SECONDS)
    grant = OAuthGrant(
        id=uuid.uuid4(),
        device_authorization_id=device.id,
        client_id=client_id,
        user_id=user.id,
        project_id=device.project_id,
        audience=device.audience,
        scopes=list(scopes),
        status="active",
        access_token_hash=hash_opaque_value(access_token),
        access_expires_at=now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS),
        refresh_expires_at=refresh_expires_at,
    )
    refresh = OAuthRefreshToken(
        id=uuid.uuid4(),
        grant_id=grant.id,
        family_id=grant.id,
        token_hash=hash_opaque_value(refresh_token),
        status="active",
        expires_at=refresh_expires_at,
        retain_until=refresh_expires_at + timedelta(seconds=_refresh_retention_seconds()),
    )
    db.add_all([grant, refresh])
    device.status = "exchanged"
    device.exchanged_at = now
    application.last_used_at = now
    _audit(db, "token.device_exchange", "success", context=context, device=device, grant=grant)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise OAuthProtocolError("invalid_grant", "The device authorization is no longer usable.") from exc
    return _token_response(access_token, refresh_token, grant, now)


def _revoke_family_locked(db: Session, grant: OAuthGrant, now: datetime, reason: str) -> None:
    grant.status = "revoked"
    grant.revoked_at = grant.revoked_at or now
    grant.revoke_reason = reason[:64]
    db.execute(
        update(OAuthRefreshToken)
        .where(
            OAuthRefreshToken.grant_id == grant.id,
            OAuthRefreshToken.status.in_(("active", "rotated")),
        )
        .values(status="revoked", revoked_at=now)
    )


def refresh_access_token(
    db: Session,
    *,
    refresh_token: str,
    client_id: str,
    context: AuditContext,
    rate_limit_client: bool = True,
) -> dict[str, Any]:
    client_id = _validated_client_id(client_id)
    if rate_limit_client:
        _rate_limited_application(db, "token", context, client_id)
    else:
        _application(db, client_id)
    token_hash = hash_opaque_value(refresh_token) if refresh_token else ""
    token_identity = db.execute(
        select(OAuthRefreshToken.id, OAuthRefreshToken.grant_id).where(OAuthRefreshToken.token_hash == token_hash)
    ).one_or_none()
    if token_identity is None:
        raise OAuthProtocolError("invalid_grant", "The refresh token is invalid.")

    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.id == token_identity.grant_id).with_for_update())
    if grant is None or grant.client_id != client_id:
        raise OAuthProtocolError("invalid_grant", "The refresh token is invalid.")
    token = db.scalar(
        select(OAuthRefreshToken)
        .where(OAuthRefreshToken.id == token_identity.id, OAuthRefreshToken.grant_id == grant.id)
        .with_for_update()
    )
    if token is None:
        raise OAuthProtocolError("invalid_grant", "The refresh token is invalid.")
    now = _now()
    if token.status == "rotated":
        _revoke_family_locked(db, grant, now, "refresh_token_replay")
        _audit(db, "token.refresh_replay", "denied", context=context, grant=grant)
        db.commit()
        raise OAuthProtocolError("invalid_grant", "Refresh token reuse revoked the token family.")
    if token.status != "active" or grant.status != "active":
        raise OAuthProtocolError("invalid_grant", "The refresh token is no longer active.")
    if (
        _utc(token.expires_at) <= now
        or _utc(grant.refresh_expires_at) <= now
        or (token.idle_expires_at is not None and _utc(token.idle_expires_at) <= now)
    ):
        token.status = "expired"
        _revoke_family_locked(db, grant, now, "refresh_token_expired")
        _audit(db, "token.refresh", "denied", context=context, grant=grant, metadata={"reason": "expired"})
        db.commit()
        raise OAuthProtocolError("invalid_grant", "The refresh token has expired.")

    application = _application_record(db, grant.client_id)
    user = db.get(User, grant.user_id)
    workspace = _workspace(db)
    if (
        application is None
        or application.status != "active"
        or grant.audience != AUDIENCE
        or grant.audience not in (application.allowed_audiences or [])
        or not set(grant.scopes or []).issubset(set(application.allowed_scopes or []))
        or user is None
        or find_project(workspace, grant.project_id) is None
        or not _role_allows_scopes(_project_role(user, grant.project_id, workspace), grant.scopes or [])
    ):
        _revoke_family_locked(db, grant, now, "entitlement_lost")
        _audit(db, "token.refresh", "denied", context=context, grant=grant, metadata={"reason": "entitlement"})
        db.commit()
        raise OAuthProtocolError("invalid_grant", "The grant is no longer authorized.")

    next_access_token = generate_opaque_token(ACCESS_TOKEN_PREFIX)
    next_refresh_token = generate_opaque_token(REFRESH_TOKEN_PREFIX)
    replacement = OAuthRefreshToken(
        id=uuid.uuid4(),
        grant_id=grant.id,
        family_id=grant.id,
        token_hash=hash_opaque_value(next_refresh_token),
        status="active",
        expires_at=token.expires_at,
        idle_expires_at=token.idle_expires_at,
        retain_until=token.retain_until,
    )
    db.add(replacement)
    db.flush()
    token.status = "rotated"
    token.rotated_at = now
    token.last_used_at = now
    token.replaced_by_id = replacement.id
    grant.access_token_hash = hash_opaque_value(next_access_token)
    grant.access_expires_at = now + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    application.last_used_at = now
    _audit(db, "token.refresh", "success", context=context, grant=grant)
    db.commit()
    return _token_response(next_access_token, next_refresh_token, grant, now)


def revoke_token(
    db: Session,
    *,
    token: str,
    token_type_hint: str | None,
    client_id: str,
    context: AuditContext,
    rate_limit_client: bool = True,
) -> None:
    client_id = _validated_client_id(client_id)
    if rate_limit_client:
        _rate_limited_application(db, "revocation", context, client_id)
    else:
        _application(db, client_id)
    token_hash = hash_opaque_value(token) if token else ""
    now = _now()

    search_refresh_first = token_type_hint != "access_token"
    if search_refresh_first:
        refresh_identity = db.execute(
            select(OAuthRefreshToken.id, OAuthRefreshToken.grant_id).where(OAuthRefreshToken.token_hash == token_hash)
        ).one_or_none()
        if refresh_identity is not None:
            grant = db.scalar(select(OAuthGrant).where(OAuthGrant.id == refresh_identity.grant_id).with_for_update())
            if grant is not None:
                refresh = db.scalar(
                    select(OAuthRefreshToken).where(OAuthRefreshToken.id == refresh_identity.id).with_for_update()
                )
                if refresh is not None and grant.client_id == client_id:
                    _revoke_family_locked(db, grant, now, "client_refresh_revocation")
                    _audit(db, "token.revoked", "success", context=context, grant=grant, metadata={"kind": "refresh"})
                    db.commit()
                    return

    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.access_token_hash == token_hash).with_for_update())
    if grant is not None and grant.client_id == client_id:
        grant.access_token_hash = hash_opaque_value(generate_opaque_token(ACCESS_TOKEN_PREFIX))
        grant.access_expires_at = now
        _audit(db, "token.revoked", "success", context=context, grant=grant, metadata={"kind": "access"})
        db.commit()
        return

    if not search_refresh_first:
        refresh_identity = db.execute(
            select(OAuthRefreshToken.id, OAuthRefreshToken.grant_id).where(OAuthRefreshToken.token_hash == token_hash)
        ).one_or_none()
        if refresh_identity is not None:
            grant = db.scalar(select(OAuthGrant).where(OAuthGrant.id == refresh_identity.grant_id).with_for_update())
            if grant is not None:
                refresh = db.scalar(
                    select(OAuthRefreshToken).where(OAuthRefreshToken.id == refresh_identity.id).with_for_update()
                )
                if refresh is not None and grant.client_id == client_id:
                    _revoke_family_locked(db, grant, now, "client_refresh_revocation")
                    _audit(db, "token.revoked", "success", context=context, grant=grant, metadata={"kind": "refresh"})
                    db.commit()
                    return
    db.commit()


_RESOURCE_SCOPES: dict[tuple[str, str], tuple[str, ...]] = {
    ("POST", "/memories"): (MEMORY_WRITE_SCOPE,),
    ("POST", "/search"): (MEMORY_READ_SCOPE,),
    ("GET", "/v1/ping/"): SUPPORTED_SCOPES,
}


def _resource_exception(request: Request, status_code: int, code: str, description: str) -> HTTPException:
    request_id = request_id_var.get()
    headers = {"WWW-Authenticate": 'Bearer realm="yiqiao", error="invalid_token"'} if status_code == 401 else None
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": description, "request_id": request_id},
        headers=headers,
    )


async def _assert_no_project_override(request: Request, project_id: str) -> None:
    supplied: list[Any] = []
    project_header_values = request.headers.getlist("x-project-id")
    if len(project_header_values) > 1:
        raise _resource_exception(request, 403, "project_scope_mismatch", "The project header is ambiguous.")
    supplied.extend(project_header_values)
    supplied.extend(request.query_params.getlist("project_id"))
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content_subtype = content_type.rsplit("/", 1)[-1]
    parses_as_json = not content_type or (
        content_type.startswith("application/") and (content_subtype == "json" or content_subtype.endswith("+json"))
    )
    if parses_as_json:
        body = await request.body()
        if body:
            try:
                import json

                payload = json.loads(body)
            except (UnicodeDecodeError, ValueError):
                payload = None

            def visit(value: Any) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == "project_id":
                            supplied.append(item)
                        else:
                            visit(item)
                elif isinstance(value, list):
                    for item in value:
                        visit(item)

            visit(payload)
    if any(not isinstance(value, str) or value != project_id for value in supplied):
        raise _resource_exception(request, 403, "project_scope_mismatch", "The grant is bound to a different project.")


async def authorize_resource_request(
    db: Session,
    *,
    token: str,
    request: Request,
) -> ResourceAuthorization:
    required_scopes = _RESOURCE_SCOPES.get((request.method.upper(), request.url.path))
    if required_scopes is None:
        raise _resource_exception(request, 403, "insufficient_scope", "This OAuth token cannot access this route.")
    token_hash = hash_opaque_value(token)
    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.access_token_hash == token_hash))
    now = _now()
    if grant is None or grant.status != "active" or _utc(grant.access_expires_at) <= now:
        raise _resource_exception(request, 401, "invalid_token", "The access token is invalid or expired.")
    application = db.get(OAuthApplication, grant.client_id)
    if application is None or application.status != "active":
        raise _resource_exception(request, 401, "invalid_token", "The access token is no longer active.")
    scopes = tuple(grant.scopes or [])
    if grant.audience != AUDIENCE or grant.audience not in (application.allowed_audiences or []):
        raise _resource_exception(request, 401, "invalid_token", "The access token audience is invalid.")
    if not set(scopes).issubset(set(application.allowed_scopes or [])):
        raise _resource_exception(request, 401, "invalid_token", "The access token grant is no longer allowed.")
    if request.url.path == "/v1/ping/":
        scope_allowed = bool(set(scopes).intersection(required_scopes))
    else:
        scope_allowed = required_scopes[0] in scopes
    if not scope_allowed:
        raise _resource_exception(request, 403, "insufficient_scope", "The access token lacks the required scope.")

    user = db.get(User, grant.user_id)
    workspace = _workspace(db)
    project = find_project(workspace, grant.project_id)
    role = _project_role(user, grant.project_id, workspace) if user is not None else None
    if request.url.path == "/v1/ping/":
        role_allowed = any(
            (scope == MEMORY_READ_SCOPE and role_allows_read(role))
            or (scope == MEMORY_WRITE_SCOPE and role_allows_write(role))
            for scope in scopes
        )
    else:
        role_allowed = role_allows_write(role) if required_scopes[0] == MEMORY_WRITE_SCOPE else role_allows_read(role)
    if user is None or project is None or not role_allowed:
        raise _resource_exception(request, 403, "access_denied", "Project access is no longer available.")
    await _assert_no_project_override(request, grant.project_id)
    return ResourceAuthorization(
        user=user,
        grant_id=grant.id,
        client_id=grant.client_id,
        project_id=grant.project_id,
        audience=grant.audience,
        scopes=scopes,
        access_token_hash=token_hash,
        audit_context=audit_context(request),
    )


def record_resource_success(
    db: Session,
    *,
    grant_id: uuid.UUID,
    access_token_hash: str,
    context: AuditContext,
) -> None:
    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.id == grant_id).with_for_update())
    if grant is None or grant.status != "active" or not hmac.compare_digest(grant.access_token_hash, access_token_hash):
        db.rollback()
        return
    now = _now()
    grant.last_used_at = now
    application = db.get(OAuthApplication, grant.client_id)
    if application is not None:
        application.last_used_at = now
    _audit(db, "resource.access", "success", context=context, grant=grant)
    db.commit()


def _can_manage_project(user: User, project_id: str, workspace: dict[str, Any]) -> bool:
    return user.role == "admin" or role_allows_manage(member_role(workspace, user.email, project_id))


def list_grants(db: Session, *, user: User, project_id: str) -> dict[str, Any]:
    workspace = _workspace(db)
    if find_project(workspace, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    can_manage = _can_manage_project(user, project_id, workspace)
    query = (
        select(OAuthGrant, OAuthApplication, User)
        .join(OAuthApplication, OAuthApplication.client_id == OAuthGrant.client_id)
        .join(User, User.id == OAuthGrant.user_id)
        .where(OAuthGrant.project_id == project_id)
        .order_by(OAuthGrant.created_at.desc())
    )
    if not can_manage:
        query = query.where(OAuthGrant.user_id == user.id)
    rows = db.execute(query).all()
    items: list[dict[str, Any]] = []
    for grant, application, owner in rows:
        item = {
            "id": str(grant.id),
            "client_id": grant.client_id,
            "application_name": application.display_name,
            "audience": grant.audience,
            "scopes": list(grant.scopes or []),
            "project_id": grant.project_id,
            "status": grant.status,
            "access_expires_at": grant.access_expires_at,
            "refresh_expires_at": grant.refresh_expires_at,
            "last_used_at": grant.last_used_at,
            "revoked_at": grant.revoked_at,
            "revoke_reason": grant.revoke_reason,
            "created_at": grant.created_at,
            "is_owner": grant.user_id == user.id,
        }
        if can_manage:
            item["owner_email"] = owner.email
        items.append(item)

    audit_query = (
        select(OAuthAuditEvent, OAuthApplication)
        .outerjoin(OAuthApplication, OAuthApplication.client_id == OAuthAuditEvent.client_id)
        .where(
            OAuthAuditEvent.project_id == project_id,
            ~OAuthAuditEvent.event_type.like("rate_limit.%"),
        )
        .order_by(OAuthAuditEvent.created_at.desc())
        .limit(100)
    )
    if not can_manage:
        audit_query = audit_query.where(OAuthAuditEvent.user_id == user.id)
    audit_events = [
        {
            "id": str(event.id),
            "event_type": event.event_type,
            "outcome": event.outcome,
            "client_id": event.client_id,
            "application_name": application.display_name if application else None,
            "grant_id": str(event.grant_id) if event.grant_id else None,
            "project_id": event.project_id,
            "metadata": event.event_metadata or {},
            "created_at": event.created_at,
        }
        for event, application in db.execute(audit_query).all()
    ]
    return {"items": items, "audit_events": audit_events, "can_manage_project": can_manage}


def revoke_grant(
    db: Session,
    *,
    grant_id: uuid.UUID,
    user: User,
    context: AuditContext,
) -> dict[str, Any]:
    grant = db.scalar(select(OAuthGrant).where(OAuthGrant.id == grant_id).with_for_update())
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found.")
    workspace = _workspace(db)
    if grant.user_id != user.id and not _can_manage_project(user, grant.project_id, workspace):
        raise HTTPException(status_code=403, detail="Grant access denied.")
    if grant.status == "active":
        _revoke_family_locked(db, grant, _now(), "dashboard_revocation")
    _audit(db, "grant.revoked", "success", context=context, grant=grant, user_id=user.id)
    db.commit()
    return {"id": str(grant.id), "status": grant.status}


def revoke_grants_by_application(
    db: Session,
    *,
    client_id: str,
    project_id: str,
    user: User,
    context: AuditContext,
) -> dict[str, Any]:
    workspace = _workspace(db)
    can_manage = _can_manage_project(user, project_id, workspace)
    query = (
        select(OAuthGrant)
        .where(
            OAuthGrant.client_id == client_id,
            OAuthGrant.project_id == project_id,
        )
        .order_by(OAuthGrant.id)
        .with_for_update()
    )
    if not can_manage:
        query = query.where(OAuthGrant.user_id == user.id)
    grants = list(db.scalars(query).all())
    now = _now()
    revoked = 0
    for grant in grants:
        if grant.status == "active":
            _revoke_family_locked(db, grant, now, "dashboard_application_revocation")
            revoked += 1
        _audit(db, "grant.revoked", "success", context=context, grant=grant, user_id=user.id)
    db.commit()
    return {"client_id": client_id, "project_id": project_id, "revoked": revoked}


def _application_response(application: OAuthApplication) -> dict[str, Any]:
    return {
        "client_id": application.client_id,
        "display_name": application.display_name,
        "client_type": application.client_type,
        "allowed_audiences": list(application.allowed_audiences or []),
        "allowed_scopes": list(application.allowed_scopes or []),
        "status": application.status,
        "operator_metadata": _safe_operator_metadata(application.operator_metadata),
        "created_at": application.created_at,
        "updated_at": application.updated_at,
        "last_used_at": application.last_used_at,
        "revoked_at": application.revoked_at,
    }


def _sanitize_operator_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        raise HTTPException(status_code=400, detail="Operator metadata is nested too deeply.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HTTPException(status_code=400, detail="Operator metadata contains an invalid number.")
        return value
    if isinstance(value, str):
        sanitized = "".join(character for character in value if ord(character) >= 32 and ord(character) != 127)
        if len(sanitized) > 512:
            raise HTTPException(status_code=400, detail="Operator metadata strings cannot exceed 512 characters.")
        return sanitized
    if isinstance(value, list):
        if len(value) > 32:
            raise HTTPException(status_code=400, detail="Operator metadata lists cannot exceed 32 items.")
        return [_sanitize_operator_metadata(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 32:
            raise HTTPException(status_code=400, detail="Operator metadata objects cannot exceed 32 fields.")
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str) or not _METADATA_KEY_RE.fullmatch(raw_key):
                raise HTTPException(status_code=400, detail="Operator metadata contains an invalid field name.")
            normalized_key = re.sub(r"[^a-z0-9]", "", raw_key.lower())
            if any(sensitive in normalized_key for sensitive in _SENSITIVE_METADATA_KEYS):
                raise HTTPException(status_code=400, detail="Operator metadata cannot contain credential fields.")
            sanitized[raw_key] = _sanitize_operator_metadata(item, depth=depth + 1)
        return sanitized
    raise HTTPException(status_code=400, detail="Operator metadata must contain JSON values only.")


def _safe_operator_metadata(value: Any) -> dict[str, Any]:
    try:
        sanitized = _sanitize_operator_metadata(value or {})
        if len(json.dumps(sanitized, ensure_ascii=False).encode("utf-8")) <= 8192:
            return sanitized
    except HTTPException:
        pass
    return {}


def list_applications(db: Session, *, user: User) -> dict[str, Any]:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    applications = db.scalars(
        select(OAuthApplication).order_by(OAuthApplication.display_name, OAuthApplication.client_id)
    ).all()
    return {"items": [_application_response(item) for item in applications], "can_register": True}


def register_application(
    db: Session,
    *,
    user: User,
    client_id: str,
    display_name: str,
    client_type: str,
    allowed_audiences: list[str],
    allowed_scopes: list[str],
    operator_metadata: dict[str, Any],
    context: AuditContext,
) -> dict[str, Any]:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    try:
        client_id = _validated_client_id(client_id)
    except OAuthProtocolError as exc:
        raise HTTPException(status_code=400, detail="Client ID contains unsupported characters.") from exc
    display_name = display_name.strip()
    if not display_name or len(display_name) > 255:
        raise HTTPException(status_code=400, detail="Display name must be between 1 and 255 characters.")
    if client_type != "public":
        raise HTTPException(status_code=400, detail="Only public Device Flow clients can be registered.")
    if set(allowed_audiences) != {AUDIENCE}:
        raise HTTPException(status_code=400, detail="The application audience must be yiqiao:memory-api.")
    try:
        scopes = normalize_scopes(allowed_scopes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Application scopes contain an unsupported value.") from exc
    if not scopes:
        raise HTTPException(status_code=400, detail="At least one application scope is required.")
    enforce_management_rate_limit(
        db,
        "application_registration",
        context,
        client_id=client_id,
        persist_client_id=False,
    )
    registration_lock = _context_digest("application-registration", client_id)
    _acquire_rate_limit_locks(db, (registration_lock,))
    if _application_record(db, client_id) is not None:
        raise HTTPException(status_code=409, detail="An application with this client ID already exists.")
    sanitized_metadata = _sanitize_operator_metadata(operator_metadata)
    if len(json.dumps(sanitized_metadata, ensure_ascii=False).encode("utf-8")) > 8192:
        raise HTTPException(status_code=400, detail="Operator metadata cannot exceed 8192 bytes.")
    application = OAuthApplication(
        client_id=client_id,
        display_name=display_name,
        client_type="public",
        allowed_audiences=[AUDIENCE],
        allowed_scopes=list(scopes),
        status="active",
        operator_metadata=sanitized_metadata,
    )
    db.add(application)
    _audit(db, "application.registered", "success", context=context, client_id=client_id, user_id=user.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An application with this client ID already exists.") from exc
    return _application_response(application)


def revoke_application(
    db: Session,
    *,
    user: User,
    client_id: str,
    context: AuditContext,
) -> dict[str, Any]:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    grants = list(
        db.scalars(
            select(OAuthGrant).where(OAuthGrant.client_id == client_id).order_by(OAuthGrant.id).with_for_update()
        ).all()
    )
    application = db.scalar(select(OAuthApplication).where(OAuthApplication.client_id == client_id).with_for_update())
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    now = _now()
    for grant in grants:
        if grant.status == "active":
            _revoke_family_locked(db, grant, now, "application_revoked")
    application.status = "revoked"
    application.revoked_at = application.revoked_at or now
    _audit(db, "application.revoked", "success", context=context, client_id=client_id, user_id=user.id)
    db.commit()
    return _application_response(application)


def revoke_project_grants(db: Session, project_id: str) -> int:
    grants = list(
        db.scalars(
            select(OAuthGrant).where(OAuthGrant.project_id == project_id).order_by(OAuthGrant.id).with_for_update()
        ).all()
    )
    now = _now()
    revoked = 0
    for grant in grants:
        if grant.status == "active":
            _revoke_family_locked(db, grant, now, "project_deleted")
            revoked += 1
        _audit(db, "grant.revoked", "success", grant=grant, metadata={"reason": "project_deleted"})
    return revoked
