# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import uuid
from datetime import datetime, timezone

from auth import (
    API_KEY_SCOPES,
    generate_api_key,
    invalidate_api_key_auth_cache,
    require_dashboard_project_write,
)
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Request
from models import APIKey, User
from project_scope import get_project_id
from pydantic import BaseModel, ConfigDict, Field, field_validator
from schemas import MessageResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class CreateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=255)
    project_id: str | None = None
    scopes: list[str] = Field(default_factory=lambda: list(API_KEY_SCOPES))
    expires_at: datetime | None = None

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        label = value.strip()
        if not label:
            raise ValueError("API key label cannot be empty.")
        if any(ord(character) < 32 or ord(character) == 127 for character in label):
            raise ValueError("API key label cannot contain control characters.")
        return label

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("API key scopes cannot contain duplicates.")
        unknown = [scope for scope in value if scope not in API_KEY_SCOPES]
        if unknown:
            raise ValueError(f"Unsupported API key scope: {unknown[0]}")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("API key expiration must include a timezone.")
        expires_at = value.astimezone(timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("API key expiration must be in the future.")
        return expires_at


class CreateKeyResponse(BaseModel):
    id: str
    key: str
    label: str
    key_prefix: str
    project_id: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime


class KeyListItem(BaseModel):
    id: str
    label: str
    key_prefix: str
    project_id: str
    scopes: list[str] | None
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[KeyListItem])
def list_keys(
    request: Request,
    _user: User = Depends(require_dashboard_project_write),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    keys = (
        db.execute(
            select(APIKey)
            .where(APIKey.project_id == project_id, APIKey.revoked_at.is_(None))
            .order_by(APIKey.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        KeyListItem(
            id=str(k.id),
            label=k.label,
            key_prefix=k.key_prefix,
            project_id=k.project_id,
            scopes=None if k.scopes is None else list(k.scopes),
            expires_at=_utc(k.expires_at),
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.post("", response_model=CreateKeyResponse, status_code=201)
def create_key(
    body: CreateKeyRequest,
    request: Request,
    user: User = Depends(require_dashboard_project_write),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    if body.project_id and body.project_id != project_id:
        raise HTTPException(status_code=403, detail="Cannot create an API key for another project.")
    full_key, prefix, key_hash = generate_api_key()
    api_key = APIKey(
        key_prefix=prefix,
        key_hash=key_hash,
        label=body.label,
        project_id=project_id,
        scopes=list(body.scopes),
        expires_at=body.expires_at,
        created_by=user.id,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return CreateKeyResponse(
        id=str(api_key.id),
        key=full_key,
        label=api_key.label,
        key_prefix=prefix,
        project_id=api_key.project_id,
        scopes=list(api_key.scopes or []),
        expires_at=_utc(api_key.expires_at),
        created_at=api_key.created_at,
    )


@router.delete("/{key_id}", response_model=MessageResponse)
def revoke_key(
    key_id: str,
    request: Request,
    _user: User = Depends(require_dashboard_project_write),
    db: Session = Depends(get_db),
):
    try:
        key_uuid = uuid.UUID(key_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="API key not found.")
    api_key = db.get(APIKey, key_uuid)
    if api_key is None or api_key.project_id != get_project_id(request):
        raise HTTPException(status_code=404, detail="API key not found.")
    if api_key.revoked_at is not None:
        raise HTTPException(status_code=400, detail="API key is already revoked.")

    api_key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_api_key_auth_cache(api_key.id)
    return MessageResponse(message="API key revoked.")
