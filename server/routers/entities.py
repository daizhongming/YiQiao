# This file was modified in 2026 by YiQiao contributors. See NOTICE.

from collections import defaultdict
from datetime import datetime
from typing import Any, Literal, Optional

from auth import require_project_read, require_project_write
from db import get_db
from errors import upstream_error
from fastapi import APIRouter, Depends, HTTPException, Request
from models import RequestLog
from project_scope import DEFAULT_PROJECT_ID, get_project_id
from pydantic import BaseModel
from schemas import MessageResponse
from server_state import ProviderConfigurationRequiredError, get_memory_instance
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/entities", tags=["entities"])

SCAN_LIMIT = 10_000

EntityType = Literal["user", "agent", "app", "run"]
TYPE_TO_FIELD: dict[EntityType, str] = {
    "user": "user_id",
    "agent": "agent_id",
    "app": "app_id",
    "run": "run_id",
}


class Entity(BaseModel):
    id: str
    type: EntityType
    total_memories: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EntityDetail(Entity):
    total_requests: int


def _iter_payloads(project_id: str) -> list[dict[str, Any]]:
    results = get_memory_instance().vector_store.list(top_k=SCAN_LIMIT)
    rows = results[0] if results and isinstance(results, list) and isinstance(results[0], list) else results or []
    return [
        payload
        for row in rows
        if (payload := (getattr(row, "payload", None) or {})).get("project_id", DEFAULT_PROJECT_ID) == project_id
    ]


@router.get("/{entity_type}/{entity_id}", response_model=EntityDetail)
def get_entity(
    request: Request,
    entity_type: EntityType,
    entity_id: str,
    _auth=Depends(require_project_read),
    db: Session = Depends(get_db),
):
    field = TYPE_TO_FIELD[entity_type]
    total_memories = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    for payload in _iter_payloads(get_project_id(request)):
        if str(payload.get(field) or "") != entity_id:
            continue
        total_memories += 1
        created = _parse_timestamp(payload.get("created_at"))
        updated = _parse_timestamp(payload.get("updated_at")) or created
        if created and (created_at is None or created < created_at):
            created_at = created
        if updated and (updated_at is None or updated > updated_at):
            updated_at = updated

    project_id = get_project_id(request)
    project_filter = RequestLog.project_id == project_id
    if project_id == DEFAULT_PROJECT_ID:
        project_filter = or_(project_filter, RequestLog.project_id.is_(None))
    total_requests = int(
        db.scalar(
            select(func.count(RequestLog.id)).where(
                RequestLog.auth_type.in_(("api_key", "admin_api_key")),
                RequestLog.operation.in_(("memory_write", "memory_search", "memory_read")),
                project_filter,
                getattr(RequestLog, field) == entity_id,
            )
        )
        or 0
    )
    if total_memories == 0 and total_requests == 0:
        raise HTTPException(status_code=404, detail="Entity not found.")
    return EntityDetail(
        id=entity_id,
        type=entity_type,
        total_memories=total_memories,
        total_requests=total_requests,
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("", response_model=list[Entity])
def list_entities(request: Request, _auth=Depends(require_project_read)):
    buckets: dict[tuple[EntityType, str], dict[str, Any]] = defaultdict(
        lambda: {"total_memories": 0, "created_at": None, "updated_at": None}
    )

    for payload in _iter_payloads(get_project_id(request)):
        created = _parse_timestamp(payload.get("created_at"))
        updated = _parse_timestamp(payload.get("updated_at")) or created

        for entity_type, field in TYPE_TO_FIELD.items():
            value = payload.get(field)
            if not value:
                continue
            bucket = buckets[(entity_type, str(value))]
            bucket["total_memories"] += 1
            if created and (bucket["created_at"] is None or created < bucket["created_at"]):
                bucket["created_at"] = created
            if updated and (bucket["updated_at"] is None or updated > bucket["updated_at"]):
                bucket["updated_at"] = updated

    return [
        Entity(id=entity_id, type=entity_type, **data)
        for (entity_type, entity_id), data in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


@router.delete("/{entity_type}/{entity_id}", response_model=MessageResponse)
def delete_entity(request: Request, entity_type: EntityType, entity_id: str, _auth=Depends(require_project_write)):
    try:
        get_memory_instance().delete_all(
            **{TYPE_TO_FIELD[entity_type]: entity_id, "project_id": get_project_id(request)}
        )
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()
    return MessageResponse(message="Entity deleted")
