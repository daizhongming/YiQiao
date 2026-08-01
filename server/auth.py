# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import hashlib
import os
import secrets
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import jwt
from db import get_db
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from models import APIKey, RefreshTokenJti, User
from passlib.context import CryptContext
from project_scope import PROJECT_HEADER, get_project_id, normalize_project_id
from settings_store import get_json
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    member_role,
    role_allows_read,
    role_allows_write,
)

JWT_SECRET = os.environ.get("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 30
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "").lower() in {"1", "true", "yes", "on"}
_API_KEY_CACHE_LIMIT = 1024
_api_key_cache: OrderedDict[str, uuid.UUID] = OrderedDict()
_api_key_cache_lock = threading.RLock()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def dummy_verify_password() -> None:
    """Burn the same bcrypt cycles as a real verify so login timing doesn't leak whether an email exists."""
    pwd_context.dummy_verify()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (full_key, prefix, hash)."""
    raw = secrets.token_urlsafe(32)
    full_key = f"yqsk_{raw}"
    prefix = full_key[:12]
    key_hash = pwd_context.hash(full_key)
    return full_key, prefix, key_hash


def verify_api_key_hash(plain_key: str, hashed: str) -> bool:
    return pwd_context.verify(plain_key, hashed)


def _api_key_fingerprint(plain_key: str) -> str:
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def _get_cached_api_key_id(plain_key: str) -> uuid.UUID | None:
    fingerprint = _api_key_fingerprint(plain_key)
    with _api_key_cache_lock:
        key_id = _api_key_cache.get(fingerprint)
        if key_id is not None:
            _api_key_cache.move_to_end(fingerprint)
        return key_id


def _cache_api_key(plain_key: str, key_id: uuid.UUID) -> None:
    fingerprint = _api_key_fingerprint(plain_key)
    with _api_key_cache_lock:
        _api_key_cache[fingerprint] = key_id
        _api_key_cache.move_to_end(fingerprint)
        while len(_api_key_cache) > _API_KEY_CACHE_LIMIT:
            _api_key_cache.popitem(last=False)


def invalidate_api_key_auth_cache(api_key_id: uuid.UUID | str | None = None) -> None:
    """Drop cached hash verifications after revocation or other credential changes."""
    with _api_key_cache_lock:
        if api_key_id is None:
            _api_key_cache.clear()
            return
        expected = str(api_key_id)
        stale = [fingerprint for fingerprint, key_id in _api_key_cache.items() if str(key_id) == expected]
        for fingerprint in stale:
            _api_key_cache.pop(fingerprint, None)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _credential_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _get_secret() -> str:
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="JWT_SECRET is not configured.")
    return JWT_SECRET


def create_access_token(user_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "role": role, "exp": expire, "type": "access"}
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, db: Session) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    jti = uuid.uuid4()
    db.add(RefreshTokenJti(jti=jti, user_id=uuid.UUID(user_id), expires_at=expire))
    db.commit()
    payload = {"sub": user_id, "exp": expire, "jti": str(jti), "type": "refresh"}
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def consume_refresh_jti(jti: str, db: Session) -> None:
    """Atomically mark a refresh token's jti as used. Raises 401 if missing, already used, or expired.

    The conditional UPDATE closes the read-check-write race: concurrent replays of the same
    token race on a single row, so at most one update affects a row and the rest see rowcount 0.
    """
    try:
        jti_uuid = uuid.UUID(jti)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Refresh token is no longer valid.")
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(RefreshTokenJti)
        .where(
            RefreshTokenJti.jti == jti_uuid,
            RefreshTokenJti.used_at.is_(None),
            RefreshTokenJti.expires_at > now,
        )
        .values(used_at=now)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=401, detail="Refresh token is no longer valid.")
    db.commit()


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")


bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _mark_auth_type(request: Request, auth_type: str) -> None:
    request.state.auth_type = auth_type


def _get_default_user(db: Session) -> User | None:
    return db.scalar(select(User).order_by(User.created_at.asc()))


def _resolve_user_from_jwt(token: str, db: Session) -> User:
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type.")
    try:
        user_id = uuid.UUID(str(payload.get("sub") or ""))
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token subject.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found.")
    return user


def _validate_api_key(candidate: APIKey, request: Request, db: Session) -> User:
    now = datetime.now(timezone.utc)
    if candidate.key_type != "standard":
        invalidate_api_key_auth_cache(candidate.id)
        raise _credential_error(401, "unsupported_key_type", "This API key type is not supported.")
    if candidate.revoked_at is not None:
        invalidate_api_key_auth_cache(candidate.id)
        raise _credential_error(401, "key_revoked", "API key has been revoked.")
    if candidate.expires_at is not None and _utc(candidate.expires_at) <= now:
        invalidate_api_key_auth_cache(candidate.id)
        raise _credential_error(401, "auth_expired", "API key has expired.")
    if find_project(_workspace_settings(db), candidate.project_id) is None:
        invalidate_api_key_auth_cache(candidate.id)
        raise _credential_error(401, "project_not_found", "API key project no longer exists.")

    explicit_project = request.headers.get(PROJECT_HEADER) or request.query_params.get("project_id")
    if explicit_project is not None and normalize_project_id(explicit_project) != candidate.project_id:
        raise _credential_error(403, "project_scope_mismatch", "API key is bound to a different project.")

    request.state.project_id = candidate.project_id
    request.state.api_key_id = str(candidate.id)
    request.state.api_key_type = candidate.key_type
    request.state.api_key_scopes = list(candidate.scopes or [])
    candidate.last_used_at = now
    db.commit()
    user = db.get(User, candidate.created_by)
    if user is None:
        invalidate_api_key_auth_cache(candidate.id)
        raise HTTPException(status_code=401, detail="API key owner not found.")
    return user


def _resolve_user_from_api_key(key: str, request: Request, db: Session) -> User:
    cached_id = _get_cached_api_key_id(key)
    if cached_id is not None:
        candidate = db.scalar(select(APIKey).where(APIKey.id == cached_id).execution_options(populate_existing=True))
        if candidate is not None:
            return _validate_api_key(candidate, request, db)
        invalidate_api_key_auth_cache(cached_id)

    prefix = key[:12] if len(key) >= 12 else key
    candidates = db.execute(select(APIKey).where(APIKey.key_prefix == prefix)).scalars().all()

    for candidate in candidates:
        if verify_api_key_hash(key, candidate.key_hash):
            _cache_api_key(key, candidate.id)
            return _validate_api_key(candidate, request, db)

    raise HTTPException(status_code=401, detail="Invalid API key.")


def _workspace_settings(db: Session) -> dict:
    return get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)


def _finalize_auth(request: Request, user: User | None, db: Session) -> User | None:
    request.state.actor_user_id = str(user.id) if user is not None else None
    request.state.actor_email = user.email.lower() if user is not None and user.email else None
    if getattr(request.state, "auth_type", "none") in {"admin_api_key", "disabled"}:
        return user
    from usage_service import enforce_request_quotas

    enforce_request_quotas(request, user, db)
    return user


def _project_role(user: User | None, request: Request, db: Session) -> str | None:
    auth_type = getattr(request.state, "auth_type", "none")
    if user is None or (user.role == "admin" and auth_type != "api_key"):
        return "OWNER"
    if auth_type == "api_key":
        return "OWNER"
    return member_role(_workspace_settings(db), user.email, get_project_id(request))


def _require_project_role(user: User | None, request: Request, db: Session, *, write: bool) -> User | None:
    auth_type = getattr(request.state, "auth_type", "none")
    if auth_type in {"admin_api_key", "disabled"}:
        return user
    role = _project_role(user, request, db)
    allowed = role_allows_write(role) if write else role_allows_read(role)
    if not allowed:
        raise HTTPException(status_code=403, detail="Project access denied.")
    return user


async def verify_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    x_api_key: str | None = Depends(api_key_header),
    db: Session = Depends(get_db),
) -> User | None:
    """Authenticate via JWT, X-API-Key, or legacy ADMIN_API_KEY. Returns User or None."""
    request.state.project_id = get_project_id(request)
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("token "):
        token_key = authorization.split(" ", 1)[1].strip()
        if ADMIN_API_KEY and secrets.compare_digest(token_key, ADMIN_API_KEY):
            _mark_auth_type(request, "admin_api_key")
            return _finalize_auth(request, None, db)
        if AUTH_DISABLED:
            _mark_auth_type(request, "disabled")
            return _finalize_auth(request, None, db)
        _mark_auth_type(request, "api_key")
        return _finalize_auth(request, _resolve_user_from_api_key(token_key, request, db), db)

    if credentials is not None:
        _mark_auth_type(request, "bearer")
        return _finalize_auth(request, _resolve_user_from_jwt(credentials.credentials, db), db)

    if x_api_key is not None:
        if ADMIN_API_KEY and secrets.compare_digest(x_api_key, ADMIN_API_KEY):
            _mark_auth_type(request, "admin_api_key")
            return _finalize_auth(request, None, db)
        if AUTH_DISABLED:
            _mark_auth_type(request, "disabled")
            return _finalize_auth(request, None, db)
        _mark_auth_type(request, "api_key")
        return _finalize_auth(request, _resolve_user_from_api_key(x_api_key, request, db), db)

    if AUTH_DISABLED:
        _mark_auth_type(request, "disabled")
        return _finalize_auth(request, None, db)

    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide a Bearer token or X-API-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_auth(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
) -> User:
    """Like verify_auth but guarantees a non-None User. Use for endpoints that require auth."""
    if user is None:
        if getattr(request.state, "auth_type", "none") in {"admin_api_key", "disabled"}:
            default_user = _get_default_user(db)
            if default_user is not None:
                return default_user
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


async def require_dashboard_user(
    request: Request,
    user: User | None = Depends(verify_auth),
) -> User:
    """Require an interactive Dashboard JWT, never an API key or disabled auth bypass."""
    if user is None or getattr(request.state, "auth_type", "none") != "bearer":
        raise _credential_error(403, "dashboard_login_required", "Dashboard login is required.")
    return user


async def require_dashboard_project_write(
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
) -> User:
    return _require_project_role(user, request, db, write=True)


async def require_project_read(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
) -> User | None:
    return _require_project_role(user, request, db, write=False)


async def require_project_write(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
) -> User | None:
    return _require_project_role(user, request, db, write=True)


_BOOTSTRAP_ADMIN = User(
    id=uuid.UUID(int=0),
    name="admin_api_key",
    email="",
    password_hash="",
    role="admin",
    created_at=datetime.min.replace(tzinfo=timezone.utc),
)


async def require_admin(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
) -> User:
    """Like require_auth but also enforces admin role.

    ADMIN_API_KEY and AUTH_DISABLED callers are treated as admin even when
    the users table is empty (fresh-deploy bootstrap).
    """
    auth_type = getattr(request.state, "auth_type", "none")
    if auth_type == "api_key":
        raise HTTPException(status_code=403, detail="Admin role required.")
    if user is None:
        if auth_type in {"admin_api_key", "disabled"}:
            default_user = _get_default_user(db)
            if default_user is not None:
                if default_user.role != "admin":
                    raise HTTPException(status_code=403, detail="Admin role required.")
                return default_user
            return _BOOTSTRAP_ADMIN
        raise HTTPException(status_code=401, detail="Authentication required.")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return user
