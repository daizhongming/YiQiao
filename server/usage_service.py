from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from fastapi import HTTPException, Request
from models import QuotaPolicy, RequestLog, User
from project_scope import get_project_id
from settings_store import get_json
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    normalize_workspace,
)

SCOPE_TYPES = {"organization", "project", "api_key", "member"}
QUOTA_METRICS = {"api_requests", "memory_writes", "memory_searches", "stored_memories"}
PERIODS = {"minute", "day", "month", "total"}
MODES = {"monitor", "soft", "hard"}
REQUEST_METRICS = {"api_requests", "memory_writes", "memory_searches"}
MANAGEMENT_PATH_PREFIXES = ("/usage", "/auth", "/settings", "/api-keys")
LEGACY_MEMORY_QUERY_PATHS = ("/memories/query", "/memories/query/")


def classify_operation(method: str, path: str) -> str:
    method = method.upper()
    normalized = path.rstrip("/") or "/"
    if method == "POST" and normalized == "/memories/query":
        return "memory_read"
    if method in {"POST", "PUT", "PATCH"} and (
        normalized == "/memories" or normalized.startswith("/memories/") or normalized == "/v3/memories/add"
    ):
        return "memory_write"
    if method == "POST" and normalized in {"/search", "/v3/memories/search"}:
        return "memory_search"
    if method in {"GET", "POST"} and normalized in {"/memories", "/v3/memories"}:
        return "memory_read"
    return "api_request"


def metric_for_operation(operation: str) -> str | None:
    return {
        "memory_write": "memory_writes",
        "memory_search": "memory_searches",
    }.get(operation)


def request_log_operation_clause(operation: str) -> Any:
    """Match a request log's effective operation without rewriting audit rows."""
    legacy_memory_query = and_(
        RequestLog.operation == "memory_write",
        RequestLog.method == "POST",
        RequestLog.path.in_(LEGACY_MEMORY_QUERY_PATHS),
    )
    if operation == "memory_write":
        return and_(RequestLog.operation == operation, ~legacy_memory_query)
    if operation == "memory_read":
        return or_(RequestLog.operation == operation, legacy_memory_query)
    return RequestLog.operation == operation


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if period == "minute":
        return current.replace(second=0, microsecond=0)
    if period == "day":
        return current.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def validate_policy_fields(
    *,
    scope_type: str,
    metric: str,
    period: str,
    limit_value: int,
    mode: str,
    warning_threshold: float,
) -> None:
    if scope_type not in SCOPE_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported quota scope.")
    if metric not in QUOTA_METRICS:
        raise HTTPException(status_code=422, detail="Unsupported quota metric.")
    if period not in PERIODS:
        raise HTTPException(status_code=422, detail="Unsupported quota period.")
    if metric == "stored_memories" and period != "total":
        raise HTTPException(status_code=422, detail="Stored memories only supports the total period.")
    if metric != "stored_memories" and period == "total":
        raise HTTPException(status_code=422, detail="Request quotas require a time period.")
    if metric == "stored_memories" and scope_type not in {"organization", "project"}:
        raise HTTPException(status_code=422, detail="Storage limits are supported for organizations and projects only.")
    if limit_value <= 0:
        raise HTTPException(status_code=422, detail="Quota limit must be greater than zero.")
    if mode not in MODES:
        raise HTTPException(status_code=422, detail="Unsupported quota mode.")
    if not 0 < warning_threshold <= 1:
        raise HTTPException(status_code=422, detail="Warning threshold must be between 0 and 1.")


def request_scope_context(request: Request, user: User | None, db: Session) -> dict[str, Any]:
    project_id = get_project_id(request)
    workspace = normalize_workspace(get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS))
    project = find_project(workspace, project_id) or {}
    organization_id = str(project.get("organization_id") or workspace.get("active_organization_id") or "org_default")
    api_key_id = str(getattr(request.state, "api_key_id", "") or "")
    member_email = str(getattr(request.state, "actor_email", "") or (user.email if user else "")).lower()
    return {
        "workspace": workspace,
        "organization_id": organization_id,
        "project_id": project_id,
        "api_key_id": api_key_id,
        "member_email": member_email,
    }


def applicable_scope_keys(context: dict[str, Any]) -> list[tuple[str, str, str]]:
    project_id = str(context["project_id"])
    keys = [
        ("organization", str(context["organization_id"]), ""),
        ("project", project_id, project_id),
    ]
    if context.get("api_key_id"):
        keys.append(("api_key", str(context["api_key_id"]), project_id))
    if context.get("member_email"):
        keys.append(("member", str(context["member_email"]).lower(), project_id))
    return keys


def applicable_policies(
    db: Session, context: dict[str, Any], metrics: Iterable[str] | None = None
) -> list[QuotaPolicy]:
    clauses = [
        and_(
            QuotaPolicy.scope_type == scope_type,
            QuotaPolicy.scope_id == scope_id,
            QuotaPolicy.project_id == project_id,
        )
        for scope_type, scope_id, project_id in applicable_scope_keys(context)
    ]
    if not clauses:
        return []
    stmt = select(QuotaPolicy).where(or_(*clauses))
    if metrics:
        stmt = stmt.where(QuotaPolicy.metric.in_(set(metrics)))
    return list(db.execute(stmt).scalars().all())


def _project_ids_for_organization(context: dict[str, Any], organization_id: str) -> list[str]:
    return [
        str(project.get("id"))
        for project in context["workspace"].get("projects", [])
        if project.get("id") and project.get("organization_id") == organization_id
    ]


def policy_log_filters(policy: QuotaPolicy, context: dict[str, Any], db: Session) -> list[Any]:
    filters: list[Any] = []
    if policy.scope_type == "organization":
        project_ids = _project_ids_for_organization(context, policy.scope_id)
        filters.append(
            or_(
                RequestLog.organization_id == policy.scope_id,
                and_(RequestLog.organization_id.is_(None), RequestLog.project_id.in_(project_ids or ["__none__"])),
            )
        )
    elif policy.scope_type == "project":
        filters.append(RequestLog.project_id == policy.scope_id)
    elif policy.scope_type == "api_key":
        try:
            filters.append(RequestLog.api_key_id == uuid.UUID(policy.scope_id))
        except ValueError:
            filters.append(RequestLog.api_key_id.is_(None))
            filters.append(RequestLog.api_key_id.is_not(None))
    elif policy.scope_type == "member":
        actor_id = db.scalar(select(User.id).where(func.lower(User.email) == policy.scope_id.lower()))
        if actor_id is None:
            filters.append(RequestLog.actor_user_id.is_(None))
            filters.append(RequestLog.actor_user_id.is_not(None))
        else:
            filters.append(RequestLog.actor_user_id == actor_id)
        filters.append(RequestLog.project_id == policy.project_id)
    return filters


def policy_usage(db: Session, policy: QuotaPolicy, context: dict[str, Any], now: datetime | None = None) -> int:
    if policy.metric == "stored_memories":
        return 0
    filters = policy_log_filters(policy, context, db)
    start = period_start(policy.period, now)
    if start is not None:
        filters.append(RequestLog.created_at >= start)
    if policy.metric != "api_requests":
        operation = {"memory_writes": "memory_write", "memory_searches": "memory_search"}[policy.metric]
        filters.extend([request_log_operation_clause(operation), RequestLog.status_code < 400])
    return int(db.scalar(select(func.count(RequestLog.id)).where(*filters)) or 0)


def enforce_request_quotas(request: Request, user: User | None, db: Session) -> None:
    if request.url.path.startswith(MANAGEMENT_PATH_PREFIXES):
        return
    if getattr(request.state, "quota_checked", False):
        return
    request.state.quota_checked = True
    operation = classify_operation(request.method, request.url.path)
    metrics = {"api_requests"}
    operation_metric = metric_for_operation(operation)
    if operation_metric:
        metrics.add(operation_metric)
    context = request_scope_context(request, user, db)
    request.state.organization_id = context["organization_id"]
    warnings: list[str] = []
    for policy in applicable_policies(db, context, metrics):
        used = policy_usage(db, policy, context)
        projected = used + 1
        if policy.mode == "hard" and projected > policy.limit_value:
            retry_after = 60 if policy.period == "minute" else None
            headers = {"Retry-After": str(retry_after)} if retry_after else None
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "quota_exceeded",
                    "metric": policy.metric,
                    "period": policy.period,
                    "limit": policy.limit_value,
                    "scope_type": policy.scope_type,
                    "scope_id": policy.scope_id,
                },
                headers=headers,
            )
        if policy.mode == "soft" and projected >= policy.limit_value * policy.warning_threshold:
            warnings.append(f"{policy.metric}:{used}/{policy.limit_value}")
    if warnings:
        request.state.quota_warnings = warnings


def enforce_storage_quotas(
    request: Request,
    user: User | None,
    db: Session,
    counts: dict[tuple[str, str], int],
    *,
    enforce_hard: bool = True,
) -> None:
    context = request_scope_context(request, user, db)
    for policy in applicable_policies(db, context, {"stored_memories"}):
        used = counts.get((policy.scope_type, policy.scope_id), 0)
        if enforce_hard and policy.mode == "hard" and used >= policy.limit_value:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "storage_quota_exceeded",
                    "metric": policy.metric,
                    "limit": policy.limit_value,
                    "scope_type": policy.scope_type,
                    "scope_id": policy.scope_id,
                },
            )
        if policy.mode == "soft" and used >= policy.limit_value * policy.warning_threshold:
            warnings = list(getattr(request.state, "quota_warnings", []))
            warnings.append(f"stored_memories:{used}/{policy.limit_value}")
            request.state.quota_warnings = warnings


def utc_days_ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(1, days))
