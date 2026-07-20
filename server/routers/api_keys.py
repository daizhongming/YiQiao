# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import uuid
from datetime import datetime, timezone

from auth import (
    generate_api_key,
    invalidate_api_key_auth_cache,
    require_auth,
    require_project_read,
    require_project_write,
)
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Request
from models import APIKey, User
from project_scope import get_project_id
from pydantic import BaseModel
from schemas import MessageResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class CreateKeyRequest(BaseModel):
    label: str
    project_id: str | None = None


class CreateKeyResponse(BaseModel):
    id: str
    key: str
    label: str
    key_prefix: str
    project_id: str
    created_at: datetime


class KeyListItem(BaseModel):
    id: str
    label: str
    key_prefix: str
    project_id: str
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[KeyListItem])
def list_keys(
    request: Request,
    _auth=Depends(require_project_read),
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    keys = (
        db.execute(
            select(APIKey)
            .where(APIKey.created_by == user.id, APIKey.project_id == project_id, APIKey.revoked_at.is_(None))
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
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.post("", response_model=CreateKeyResponse, status_code=201)
def create_key(
    body: CreateKeyRequest,
    request: Request,
    _auth=Depends(require_project_write),
    user: User = Depends(require_auth),
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
        created_at=api_key.created_at,
    )


@router.delete("/{key_id}", response_model=MessageResponse)
def revoke_key(
    key_id: str,
    request: Request,
    _auth=Depends(require_project_write),
    user: User = Depends(require_auth),
    db: Session = Depends(get_db),
):
    try:
        key_uuid = uuid.UUID(key_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="API key not found.")
    api_key = db.get(APIKey, key_uuid)
    if api_key is None or api_key.created_by != user.id or api_key.project_id != get_project_id(request):
        raise HTTPException(status_code=404, detail="API key not found.")
    if api_key.revoked_at is not None:
        raise HTTPException(status_code=400, detail="API key is already revoked.")

    api_key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    invalidate_api_key_auth_cache(api_key.id)
    return MessageResponse(message="API key revoked.")
