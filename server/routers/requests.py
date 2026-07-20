# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from auth import require_project_read
from db import get_db
from fastapi import APIRouter, Depends, Query, Request
from models import RequestLog
from project_scope import get_project_id
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/requests", tags=["requests"])

EventType = Literal["ADD", "SEARCH", "GET_ALL"]
EntityType = Literal["user", "agent", "app", "run"]
API_KEY_AUTH_TYPES = ("api_key", "admin_api_key")
EVENT_TO_OPERATION = {
    "ADD": "memory_write",
    "SEARCH": "memory_search",
    "GET_ALL": "memory_read",
}
ENTITY_FIELDS: dict[EntityType, str] = {
    "user": "user_id",
    "agent": "agent_id",
    "app": "app_id",
    "run": "run_id",
}


class RequestEntity(BaseModel):
    type: EntityType
    id: str


class RequestLogItem(BaseModel):
    id: uuid.UUID
    method: str
    path: str
    status_code: int
    status: Literal["succeeded", "failed"]
    latency_ms: float
    auth_type: str
    project_id: str | None = None
    operation: str
    event_type: str
    entities: list[RequestEntity]
    request_payload: Any = None
    response_payload: Any = None
    result_count: int | None = None
    has_results: bool
    created_at: datetime


class RequestTrendPoint(BaseModel):
    bucket: datetime
    count: int


class RequestLogPage(BaseModel):
    items: list[RequestLogItem]
    total: int
    page: int
    page_size: int
    series: list[RequestTrendPoint]


def _project_clause(project_id: str):
    project_filter = RequestLog.project_id == project_id
    if project_id == "default-project":
        project_filter = or_(project_filter, RequestLog.project_id.is_(None))
    return project_filter


def _event_clause(event_type: str):
    event_type = event_type.upper()
    operation = EVENT_TO_OPERATION.get(event_type)
    if operation:
        return or_(
            func.upper(RequestLog.event_type) == event_type,
            and_(RequestLog.event_type.is_(None), RequestLog.operation == operation),
        )
    return func.upper(RequestLog.event_type) == event_type


def _event_type(log: RequestLog) -> str:
    return str(
        log.event_type
        or {
            "memory_write": "ADD",
            "memory_search": "SEARCH",
            "memory_read": "GET_ALL",
        }.get(log.operation, log.method)
    ).upper()


def _serialize_log(log: RequestLog) -> RequestLogItem:
    entities = [
        RequestEntity(type=entity_type, id=str(value))
        for entity_type, field in ENTITY_FIELDS.items()
        if (value := getattr(log, field, None))
    ]
    request_payload = log.request_payload
    if request_payload is None:
        request_payload = {"method": log.method, "path": log.path}
    return RequestLogItem(
        id=log.id,
        method=log.method,
        path=log.path,
        status_code=log.status_code,
        status="succeeded" if log.status_code < 400 else "failed",
        latency_ms=log.latency_ms,
        auth_type=log.auth_type,
        project_id=log.project_id,
        operation=log.operation,
        event_type=_event_type(log),
        entities=entities,
        request_payload=request_payload,
        response_payload=log.response_payload,
        result_count=log.result_count,
        has_results=bool(log.result_count and log.result_count > 0),
        created_at=log.created_at,
    )


def _trend_series(timestamps: list[datetime]) -> list[RequestTrendPoint]:
    if not timestamps:
        return []
    normalized = [
        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        for value in timestamps
    ]
    span_seconds = (max(normalized) - min(normalized)).total_seconds()
    hourly = span_seconds <= 48 * 60 * 60
    weekly = span_seconds > 120 * 24 * 60 * 60
    buckets: dict[datetime, int] = defaultdict(int)
    for value in normalized:
        if hourly:
            bucket = value.replace(minute=0, second=0, microsecond=0)
        elif weekly:
            start_of_day = value.replace(hour=0, minute=0, second=0, microsecond=0)
            bucket = start_of_day - timedelta(days=start_of_day.weekday())
        else:
            bucket = value.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets[bucket] += 1
    return [RequestTrendPoint(bucket=bucket, count=count) for bucket, count in sorted(buckets.items())]


@router.get("", response_model=RequestLogPage | list[RequestLogItem])
def list_requests(
    request: Request,
    _auth=Depends(require_project_read),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    method: str | None = None,
    status_code: int | None = None,
    succeeded: bool | None = None,
    auth_type: str | None = None,
    path_contains: str | None = None,
    event_type: EventType | None = None,
    has_results: bool | None = None,
    entity_type: EntityType | None = None,
    entity_id: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
):
    filters: list[Any] = [
        RequestLog.auth_type.in_(API_KEY_AUTH_TYPES),
        RequestLog.operation.in_(tuple(EVENT_TO_OPERATION.values())),
        _project_clause(get_project_id(request)),
    ]
    if method:
        filters.append(RequestLog.method == method.upper())
    if status_code is not None:
        filters.append(RequestLog.status_code == status_code)
    if succeeded is not None:
        filters.append(RequestLog.status_code < 400 if succeeded else RequestLog.status_code >= 400)
    if auth_type:
        filters.append(RequestLog.auth_type == auth_type)
    if path_contains:
        filters.append(RequestLog.path.ilike(f"%{path_contains}%"))
    if event_type:
        filters.append(_event_clause(event_type))
    if has_results is not None:
        filters.append(
            RequestLog.result_count > 0
            if has_results
            else or_(RequestLog.result_count.is_(None), RequestLog.result_count <= 0)
        )
    if entity_type and entity_id:
        filters.append(getattr(RequestLog, ENTITY_FIELDS[entity_type]) == entity_id)
    if start_at:
        filters.append(RequestLog.created_at >= start_at)
    if end_at:
        filters.append(RequestLog.created_at <= end_at)

    base_stmt = select(RequestLog).where(*filters)
    if page is None:
        logs = db.execute(base_stmt.order_by(RequestLog.created_at.desc()).limit(limit)).scalars().all()
        return [_serialize_log(log) for log in logs]

    total = int(db.scalar(select(func.count(RequestLog.id)).where(*filters)) or 0)
    logs = (
        db.execute(base_stmt.order_by(RequestLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    timestamps = list(db.execute(select(RequestLog.created_at).where(*filters)).scalars().all())
    return RequestLogPage(
        items=[_serialize_log(log) for log in logs],
        total=total,
        page=page,
        page_size=page_size,
        series=_trend_series(timestamps),
    )
