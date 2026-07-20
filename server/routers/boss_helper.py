import hashlib
import hmac
import os
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from auth import (
    BOSS_HELPER_KEY_TYPE,
    generate_api_key,
    invalidate_api_key_auth_cache,
    require_dashboard_project_write,
    require_dashboard_user,
)
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from models import APIKey, BossHelperPairing, User
from project_scope import get_project_id, normalize_project_id
from pydantic import BaseModel, Field, model_validator
from settings_store import get_json
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    member_role,
    role_allows_read,
)

router = APIRouter(prefix="/integrations/boss-helper/pairing", tags=["boss-helper"])

PAIRING_SCOPE = ["memory:read", "memory:write", "ping"]
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ApprovePairingRequest(BaseModel):
    user_code: str = Field(min_length=8, max_length=16)
    project_id: str = Field(min_length=1, max_length=128)


class PairingTokenRequest(BaseModel):
    device_code: str = Field(min_length=32, max_length=256)


class RevokePairingRequest(BaseModel):
    pairing_id: uuid.UUID | None = None
    api_key_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_one_identifier(self):
        if (self.pairing_id is None) == (self.api_key_id is None):
            raise ValueError("Provide exactly one of pairing_id or api_key_id.")
        return self


class PairingStatusResponse(BaseModel):
    pairing_id: uuid.UUID
    status: str
    project_id: str | None
    scopes: list[str]
    key_prefix: str | None
    pairing_expires_at: datetime
    key_expires_at: datetime | None
    requested_at: datetime
    approved_at: datetime | None
    connected_at: datetime | None
    revoked_at: datetime | None


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _pairing_ttl_seconds() -> int:
    return _env_int("BOSS_HELPER_PAIRING_TTL_SECONDS", 600, 60, 1800)


def _key_ttl_days() -> int:
    return _env_int("BOSS_HELPER_KEY_TTL_DAYS", 90, 1, 365)


def _pairing_secret() -> bytes:
    value = (
        os.environ.get("BOSS_HELPER_PAIRING_SECRET", "").strip()
        or os.environ.get("JWT_SECRET", "").strip()
        or os.environ.get("ADMIN_API_KEY", "").strip()
    )
    if not value:
        raise HTTPException(
            status_code=503,
            detail={"code": "pairing_unavailable", "message": "Pairing is not configured."},
        )
    return value.encode("utf-8")


def _hash_device_code(device_code: str) -> str:
    return hashlib.sha256(device_code.encode("utf-8")).hexdigest()


def _normalize_user_code(user_code: str) -> str:
    return "".join(character for character in user_code.upper() if character in string.ascii_uppercase + string.digits)


def _hash_user_code(user_code: str) -> str:
    return hmac.new(_pairing_secret(), _normalize_user_code(user_code).encode("ascii"), hashlib.sha256).hexdigest()


def _new_user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _workspace(db: Session) -> dict:
    return get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)


def _pairing_by_user_code(db: Session, user_code: str, *, lock: bool = False) -> BossHelperPairing | None:
    statement = select(BossHelperPairing).where(BossHelperPairing.user_code_hash == _hash_user_code(user_code))
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _pairing_by_device_code(db: Session, device_code: str, *, lock: bool = False) -> BossHelperPairing | None:
    statement = select(BossHelperPairing).where(BossHelperPairing.device_code_hash == _hash_device_code(device_code))
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _mark_expired(pairing: BossHelperPairing, db: Session, now: datetime) -> bool:
    if pairing.status in {"pending", "approved"} and _utc(pairing.expires_at) <= now:
        pairing.status = "expired"
        db.commit()
        return True
    return pairing.status == "expired"


def _visible_status(pairing: BossHelperPairing, api_key: APIKey | None, now: datetime) -> str:
    if pairing.status == "revoked" or (api_key is not None and api_key.revoked_at is not None):
        return "revoked"
    if pairing.status == "expired":
        return "expired"
    if api_key is not None and api_key.expires_at is not None and _utc(api_key.expires_at) <= now:
        return "auth_expired"
    if pairing.status == "consumed":
        return "connected"
    return pairing.status


def _serialize_status(pairing: BossHelperPairing, db: Session) -> PairingStatusResponse:
    now = datetime.now(timezone.utc)
    api_key = db.get(APIKey, pairing.api_key_id) if pairing.api_key_id is not None else None
    return PairingStatusResponse(
        pairing_id=pairing.id,
        status=_visible_status(pairing, api_key, now),
        project_id=pairing.project_id,
        scopes=list(api_key.scopes) if api_key is not None and api_key.scopes is not None else list(PAIRING_SCOPE),
        key_prefix=api_key.key_prefix if api_key is not None else None,
        pairing_expires_at=pairing.expires_at,
        key_expires_at=api_key.expires_at if api_key is not None else None,
        requested_at=pairing.created_at,
        approved_at=pairing.approved_at,
        connected_at=pairing.consumed_at,
        revoked_at=pairing.revoked_at or (api_key.revoked_at if api_key is not None else None),
    )


def _require_pairing_visibility(pairing: BossHelperPairing, user: User, db: Session) -> None:
    if pairing.project_id is None or user.role == "admin":
        return
    role = member_role(_workspace(db), user.email, pairing.project_id)
    if not role_allows_read(role):
        raise HTTPException(status_code=404, detail={"code": "pairing_not_found", "message": "Pairing not found."})


def _flow_response(status_code: int, status: str, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": status, "code": code, "message": message, **extra},
    )


@router.post("/start", status_code=201)
def start_pairing(db: Session = Depends(get_db)):
    ttl_seconds = _pairing_ttl_seconds()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    for _attempt in range(5):
        device_code = f"yqdc_{secrets.token_urlsafe(32)}"
        user_code = _new_user_code()
        pairing = BossHelperPairing(
            device_code_hash=_hash_device_code(device_code),
            user_code_hash=_hash_user_code(user_code),
            expires_at=expires_at,
        )
        db.add(pairing)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
    else:
        raise HTTPException(
            status_code=503,
            detail={"code": "pairing_unavailable", "message": "Could not create a pairing request."},
        )

    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000").strip().rstrip("/")
    verification_uri = f"{dashboard_url}/dashboard/integrations/boss-helper"
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": f"{verification_uri}?{urlencode({'user_code': user_code})}",
        "expires_in": ttl_seconds,
        "interval": 5,
    }


@router.get("/status", response_model=PairingStatusResponse)
def pairing_status(
    user_code: str = Query(min_length=8, max_length=16),
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    pairing = _pairing_by_user_code(db, user_code)
    if pairing is None:
        raise HTTPException(status_code=404, detail={"code": "pairing_not_found", "message": "Pairing not found."})
    _mark_expired(pairing, db, datetime.now(timezone.utc))
    _require_pairing_visibility(pairing, user, db)
    return _serialize_status(pairing, db)


@router.post("/approve", response_model=PairingStatusResponse)
def approve_pairing(
    body: ApprovePairingRequest,
    request: Request,
    user: User = Depends(require_dashboard_project_write),
    db: Session = Depends(get_db),
):
    project_id = normalize_project_id(body.project_id)
    if project_id != body.project_id or project_id != get_project_id(request):
        raise HTTPException(
            status_code=403,
            detail={"code": "project_scope_mismatch", "message": "Approve the selected Dashboard project only."},
        )
    if find_project(_workspace(db), project_id) is None:
        raise HTTPException(status_code=404, detail={"code": "project_not_found", "message": "Project not found."})

    pairing = _pairing_by_user_code(db, body.user_code, lock=True)
    if pairing is None:
        raise HTTPException(status_code=404, detail={"code": "pairing_not_found", "message": "Pairing not found."})
    now = datetime.now(timezone.utc)
    if _mark_expired(pairing, db, now):
        raise HTTPException(status_code=410, detail={"code": "pairing_expired", "message": "Pairing request expired."})
    if pairing.status == "revoked":
        raise HTTPException(
            status_code=410, detail={"code": "pairing_revoked", "message": "Pairing request was revoked."}
        )
    if pairing.status == "consumed":
        raise HTTPException(
            status_code=409, detail={"code": "device_code_consumed", "message": "Pairing was already consumed."}
        )
    if pairing.status == "approved":
        if pairing.project_id != project_id or pairing.approved_by != user.id:
            raise HTTPException(
                status_code=409, detail={"code": "already_approved", "message": "Pairing was already approved."}
            )
        return _serialize_status(pairing, db)

    pairing.status = "approved"
    pairing.project_id = project_id
    pairing.approved_by = user.id
    pairing.approved_at = now
    db.commit()
    db.refresh(pairing)
    return _serialize_status(pairing, db)


@router.post("/token")
def exchange_pairing_token(body: PairingTokenRequest, db: Session = Depends(get_db)):
    pairing = _pairing_by_device_code(db, body.device_code, lock=True)
    if pairing is None:
        return _flow_response(404, "invalid", "invalid_device_code", "Device code is invalid.")
    now = datetime.now(timezone.utc)
    if pairing.status == "revoked":
        return _flow_response(403, "revoked", "pairing_revoked", "Pairing request was revoked.")
    if _mark_expired(pairing, db, now):
        return _flow_response(410, "expired", "pairing_expired", "Pairing request expired.")
    if pairing.status == "pending":
        return _flow_response(
            202,
            "pending",
            "authorization_pending",
            "Waiting for Dashboard approval.",
            retry_after=5,
        )
    if pairing.status == "consumed":
        return _flow_response(409, "consumed", "device_code_consumed", "Device code was already consumed.")
    if pairing.project_id is None or find_project(_workspace(db), pairing.project_id) is None:
        pairing.status = "revoked"
        pairing.revoked_at = now
        db.commit()
        return _flow_response(410, "revoked", "project_not_found", "Approved project no longer exists.")
    if pairing.approved_by is None or db.get(User, pairing.approved_by) is None:
        pairing.status = "revoked"
        pairing.revoked_at = now
        db.commit()
        return _flow_response(410, "revoked", "approver_not_found", "Approving user no longer exists.")

    full_key, prefix, key_hash = generate_api_key()
    key_expires_at = now + timedelta(days=_key_ttl_days())
    api_key = APIKey(
        key_prefix=prefix,
        key_hash=key_hash,
        label="BossHelper",
        project_id=pairing.project_id,
        key_type=BOSS_HELPER_KEY_TYPE,
        scopes=list(PAIRING_SCOPE),
        created_by=pairing.approved_by,
        expires_at=key_expires_at,
    )
    db.add(api_key)
    db.flush()
    claim = db.execute(
        update(BossHelperPairing)
        .where(
            BossHelperPairing.id == pairing.id,
            BossHelperPairing.status == "approved",
            BossHelperPairing.api_key_id.is_(None),
        )
        .values(
            api_key_id=api_key.id,
            status="consumed",
            consumed_at=now,
            updated_at=now,
        )
    )
    if claim.rowcount != 1:
        # PostgreSQL serializes this path via FOR UPDATE. The conditional claim
        # also protects databases such as SQLite that ignore row-level locks.
        db.rollback()
        current = _pairing_by_device_code(db, body.device_code)
        if current is not None and current.status == "revoked":
            return _flow_response(403, "revoked", "pairing_revoked", "Pairing request was revoked.")
        return _flow_response(409, "consumed", "device_code_consumed", "Device code was already consumed.")
    db.commit()

    return {
        "status": "connected",
        "token_type": "api_key",
        "api_key": full_key,
        "project_id": pairing.project_id,
        "scope": list(PAIRING_SCOPE),
        "expires_at": key_expires_at,
    }


@router.post("/revoke", response_model=PairingStatusResponse)
def revoke_pairing(
    body: RevokePairingRequest,
    request: Request,
    _user: User = Depends(require_dashboard_project_write),
    db: Session = Depends(get_db),
):
    statement = select(BossHelperPairing)
    if body.pairing_id is not None:
        statement = statement.where(BossHelperPairing.id == body.pairing_id)
    else:
        statement = statement.where(BossHelperPairing.api_key_id == body.api_key_id)
    pairing = db.scalar(statement.with_for_update())
    selected_project = get_project_id(request)
    if pairing is None or (pairing.project_id is not None and pairing.project_id != selected_project):
        raise HTTPException(status_code=404, detail={"code": "pairing_not_found", "message": "Pairing not found."})

    now = datetime.now(timezone.utc)
    pairing.project_id = pairing.project_id or selected_project
    pairing.status = "revoked"
    pairing.revoked_at = pairing.revoked_at or now
    api_key = db.get(APIKey, pairing.api_key_id) if pairing.api_key_id is not None else None
    if api_key is not None and api_key.revoked_at is None:
        api_key.revoked_at = now
        invalidate_api_key_auth_cache(api_key.id)
    db.commit()
    db.refresh(pairing)
    return _serialize_status(pairing, db)
