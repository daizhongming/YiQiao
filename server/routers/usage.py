from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from auth import verify_auth
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import APIKey, QuotaPolicy, RequestLog, User
from project_scope import get_project_id
from pydantic import BaseModel, Field
from server_state import ProviderConfigurationRequiredError, get_memory_instance
from settings_store import get_json
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session
from usage_service import (
    applicable_policies,
    policy_usage,
    request_log_operation_clause,
    request_scope_context,
    validate_policy_fields,
)
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    member_role,
    normalize_workspace,
    organization_access_role,
    organization_role,
    role_allows_manage,
    role_allows_read,
)

router = APIRouter(prefix="/usage", tags=["usage"])


class PolicyInput(BaseModel):
    metric: Literal["api_requests", "memory_writes", "memory_searches", "stored_memories"]
    period: Literal["minute", "day", "month", "total"]
    limit_value: int = Field(gt=0)
    mode: Literal["monitor", "soft", "hard"] = "monitor"
    warning_threshold: float = Field(default=0.8, gt=0, le=1)


class PolicySetRequest(BaseModel):
    scope_type: Literal["organization", "project", "api_key", "member"]
    scope_id: str
    project_id: str = ""
    policies: list[PolicyInput] = Field(default_factory=list)


def _workspace(db: Session) -> dict[str, Any]:
    return normalize_workspace(get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS))


def _is_global_admin(request: Request, user: User | None) -> bool:
    return getattr(request.state, "auth_type", "none") in {"admin_api_key", "disabled"} or bool(
        user and user.role == "admin" and getattr(request.state, "auth_type", "none") != "api_key"
    )


def _deny_project_api_key_control_plane(request: Request) -> None:
    if getattr(request.state, "auth_type", "none") == "api_key":
        raise HTTPException(status_code=403, detail="Project API keys cannot access usage settings.")


def _project_access(
    settings: dict[str, Any], project_id: str, request: Request, user: User | None, *, write: bool
) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = member_role(settings, user.email if user else None, project_id)
    allowed = role_allows_manage(role) if write else role_allows_read(role)
    if not allowed:
        raise HTTPException(status_code=403, detail="Project access denied.")


def _organization_access(
    settings: dict[str, Any], org_id: str, request: Request, user: User | None, *, write: bool
) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = (
        organization_role(settings, user.email if user else None, org_id)
        if write
        else organization_access_role(settings, user.email if user else None, org_id)
    )
    allowed = role_allows_manage(role) if write else role_allows_read(role)
    if not allowed:
        raise HTTPException(status_code=403, detail="Organization access denied.")


def _normalize_policy_scope(body: PolicySetRequest, active_project_id: str) -> tuple[str, str, str]:
    scope_id = body.scope_id.strip().lower() if body.scope_type == "member" else body.scope_id.strip()
    if not scope_id:
        raise HTTPException(status_code=422, detail="Quota scope ID is required.")
    project_id = (body.project_id or active_project_id) if body.scope_type in {"project", "api_key", "member"} else ""
    if body.scope_type == "project":
        scope_id = project_id
    return body.scope_type, scope_id, project_id


def _ensure_scope_manageable(
    scope_type: str,
    scope_id: str,
    project_id: str,
    request: Request,
    user: User | None,
    db: Session,
) -> None:
    settings = _workspace(db)
    if scope_type == "organization":
        _organization_access(settings, scope_id, request, user, write=True)
        return
    if project_id != get_project_id(request):
        raise HTTPException(status_code=403, detail="Select the project before managing its limits.")
    _project_access(settings, project_id, request, user, write=True)
    if scope_type == "api_key":
        try:
            key_id = uuid.UUID(scope_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="API key not found.")
        key = db.get(APIKey, key_id)
        if key is None or key.project_id != project_id or key.revoked_at is not None:
            raise HTTPException(status_code=404, detail="API key not found.")
    elif scope_type == "member":
        known_emails = {
            str(member.get("email") or "").lower()
            for member in settings.get("members", [])
            if member.get("project_id") == project_id
            or (
                not member.get("project_id")
                and member.get("organization_id") == (find_project(settings, project_id) or {}).get("organization_id")
            )
        }
        if scope_id.lower() not in known_emails:
            raise HTTPException(status_code=404, detail="Project member not found.")


def _policy_json(policy: QuotaPolicy) -> dict[str, Any]:
    return {
        "id": str(policy.id),
        "scope_type": policy.scope_type,
        "scope_id": policy.scope_id,
        "project_id": policy.project_id,
        "metric": policy.metric,
        "period": policy.period,
        "limit_value": policy.limit_value,
        "mode": policy.mode,
        "warning_threshold": policy.warning_threshold,
        "updated_at": policy.updated_at,
    }


def _scope_filters(
    scope_type: str,
    scope_id: str,
    project_id: str,
    settings: dict[str, Any],
    db: Session,
) -> tuple[list[Any], list[str]]:
    if scope_type == "organization":
        project_ids = [
            str(project.get("id"))
            for project in settings.get("projects", [])
            if project.get("organization_id") == scope_id
        ]
        return [
            or_(RequestLog.organization_id == scope_id, RequestLog.project_id.in_(project_ids or ["__none__"]))
        ], project_ids
    if scope_type == "project":
        return [RequestLog.project_id == scope_id], [scope_id]
    if scope_type == "api_key":
        try:
            key_id = uuid.UUID(scope_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="API key not found.")
        return [RequestLog.api_key_id == key_id, RequestLog.project_id == project_id], [project_id]
    actor_id = db.scalar(select(User.id).where(func.lower(User.email) == scope_id.lower()))
    if actor_id is None:
        return [RequestLog.actor_user_id.is_(None), RequestLog.actor_user_id.is_not(None)], [project_id]
    return [RequestLog.actor_user_id == actor_id, RequestLog.project_id == project_id], [project_id]


def _count_project_memories(project_ids: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        store = get_memory_instance().vector_store
        for project_id in project_ids:
            result = store.list(filters={"project_id": project_id}, top_k=1_000_000)
            rows = result[0] if result and isinstance(result, list) and isinstance(result[0], list) else result or []
            counts[project_id] = len(rows)
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        for project_id in project_ids:
            counts.setdefault(project_id, 0)
    return counts


def _summary_context(
    scope_type: str,
    scope_id: str,
    project_id: str,
    request: Request,
    user: User | None,
    db: Session,
) -> dict[str, Any]:
    context = request_scope_context(request, user, db)
    if scope_type == "organization":
        context["organization_id"] = scope_id
        context["project_id"] = project_id or get_project_id(request)
    elif scope_type == "project":
        context["project_id"] = scope_id
        project = find_project(context["workspace"], scope_id) or {}
        context["organization_id"] = project.get("organization_id") or context["organization_id"]
    elif scope_type == "api_key":
        context["api_key_id"] = scope_id
        context["project_id"] = project_id
    else:
        context["member_email"] = scope_id.lower()
        context["api_key_id"] = ""
        context["project_id"] = project_id
    return context


@router.get("/subjects")
def usage_subjects(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    settings = _workspace(db)
    project_id = get_project_id(request)
    _project_access(settings, project_id, request, user, write=False)
    project = find_project(settings, project_id) or {}
    organization_id = str(project.get("organization_id") or settings.get("active_organization_id") or "org_default")
    can_manage_project = _is_global_admin(request, user) or role_allows_manage(
        member_role(settings, user.email if user else None, project_id)
    )
    can_manage_organization = _is_global_admin(request, user) or role_allows_manage(
        organization_role(settings, user.email if user else None, organization_id)
    )
    members = []
    seen_members: set[str] = set()
    for member in settings.get("members", []):
        email = str(member.get("email") or "").lower()
        if not email or email in seen_members:
            continue
        if member.get("project_id") == project_id or (
            not member.get("project_id") and member.get("organization_id") == organization_id
        ):
            seen_members.add(email)
            members.append({"email": email, "role": member.get("role"), "status": member.get("status")})
    keys = []
    if can_manage_project:
        key_rows = db.execute(
            select(APIKey, User.email)
            .join(User, User.id == APIKey.created_by)
            .where(APIKey.project_id == project_id, APIKey.revoked_at.is_(None))
            .order_by(APIKey.created_at.desc())
        ).all()
        keys = [
            {"id": str(key.id), "label": key.label, "key_prefix": key.key_prefix, "created_by": email}
            for key, email in key_rows
        ]
    return {
        "organization": {
            "id": organization_id,
            "name": next(
                (o.get("name") for o in settings["organizations"] if o.get("id") == organization_id), organization_id
            ),
        },
        "project": {"id": project_id, "name": project.get("name") or project_id},
        "members": members,
        "api_keys": keys,
        "can_manage_project": can_manage_project,
        "can_manage_organization": can_manage_organization,
        "current_member_email": user.email.lower() if user and user.email else None,
    }


@router.get("/policies")
def list_policies(
    request: Request,
    scope_type: Literal["organization", "project", "api_key", "member"] = Query(...),
    scope_id: str = Query(...),
    project_id: str = Query(""),
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    body = PolicySetRequest(scope_type=scope_type, scope_id=scope_id, project_id=project_id)
    scope_type, scope_id, project_id = _normalize_policy_scope(body, get_project_id(request))
    _ensure_scope_manageable(scope_type, scope_id, project_id, request, user, db)
    policies = (
        db.execute(
            select(QuotaPolicy)
            .where(
                QuotaPolicy.scope_type == scope_type,
                QuotaPolicy.scope_id == scope_id,
                QuotaPolicy.project_id == project_id,
            )
            .order_by(QuotaPolicy.metric, QuotaPolicy.period)
        )
        .scalars()
        .all()
    )
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "project_id": project_id,
        "policies": [_policy_json(p) for p in policies],
    }


@router.put("/policies")
def replace_policies(
    body: PolicySetRequest,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    scope_type, scope_id, project_id = _normalize_policy_scope(body, get_project_id(request))
    _ensure_scope_manageable(scope_type, scope_id, project_id, request, user, db)
    seen: set[tuple[str, str]] = set()
    for policy in body.policies:
        validate_policy_fields(
            scope_type=scope_type,
            metric=policy.metric,
            period=policy.period,
            limit_value=policy.limit_value,
            mode=policy.mode,
            warning_threshold=policy.warning_threshold,
        )
        key = (policy.metric, policy.period)
        if key in seen:
            raise HTTPException(status_code=422, detail="Duplicate metric and period in quota policies.")
        seen.add(key)
    db.execute(
        delete(QuotaPolicy).where(
            QuotaPolicy.scope_type == scope_type,
            QuotaPolicy.scope_id == scope_id,
            QuotaPolicy.project_id == project_id,
        )
    )
    created = []
    for item in body.policies:
        policy = QuotaPolicy(
            scope_type=scope_type,
            scope_id=scope_id,
            project_id=project_id,
            metric=item.metric,
            period=item.period,
            limit_value=item.limit_value,
            mode=item.mode,
            warning_threshold=item.warning_threshold,
            created_by=user.id if user is not None else None,
        )
        db.add(policy)
        created.append(policy)
    db.commit()
    for policy in created:
        db.refresh(policy)
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "project_id": project_id,
        "policies": [_policy_json(p) for p in created],
    }


@router.get("/summary")
def usage_summary(
    request: Request,
    days: int = Query(30, ge=1, le=90),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    all_time: bool = Query(False),
    scope_type: Literal["organization", "project", "api_key", "member"] = Query("project"),
    scope_id: str | None = Query(None),
    project_id: str | None = Query(None),
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    settings = _workspace(db)
    active_project_id = get_project_id(request)
    active_project = find_project(settings, active_project_id) or {}
    active_org_id = str(
        active_project.get("organization_id") or settings.get("active_organization_id") or "org_default"
    )
    resolved_project_id = project_id or active_project_id
    resolved_scope_id = scope_id or (active_org_id if scope_type == "organization" else resolved_project_id)
    if scope_type == "project":
        if resolved_scope_id != resolved_project_id:
            raise HTTPException(status_code=404, detail="Project not found.")
        resolved_scope_id = resolved_project_id
    if scope_type == "organization":
        _organization_access(settings, resolved_scope_id, request, user, write=False)
    else:
        if resolved_project_id != active_project_id:
            raise HTTPException(status_code=403, detail="Select the project before viewing its usage.")
        _project_access(settings, resolved_project_id, request, user, write=False)
        if scope_type in {"api_key", "member"}:
            can_manage = _is_global_admin(request, user) or role_allows_manage(
                member_role(settings, user.email if user else None, resolved_project_id)
            )
            is_self = scope_type == "member" and user is not None and user.email.lower() == resolved_scope_id.lower()
            if not can_manage and not is_self:
                raise HTTPException(status_code=403, detail="Usage subject access denied.")

    filters, project_ids = _scope_filters(scope_type, resolved_scope_id, resolved_project_id, settings, db)
    today = datetime.now(timezone.utc).date()
    if all_time:
        earliest = db.scalar(select(func.min(RequestLog.created_at)).where(*filters))
        range_start = earliest.date() if earliest else today
        range_end = today
    elif start_date is not None or end_date is not None:
        range_end = end_date or today
        range_start = start_date or (range_end - timedelta(days=days - 1))
    else:
        range_end = today
        range_start = today - timedelta(days=days - 1)

    if range_start > range_end:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date.")
    if range_end > today:
        raise HTTPException(status_code=422, detail="end_date cannot be in the future.")

    range_days = (range_end - range_start).days + 1
    start = datetime.combine(range_start, datetime.min.time(), tzinfo=timezone.utc)
    end_exclusive = datetime.combine(range_end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    scoped = [*filters, RequestLog.created_at >= start, RequestLog.created_at < end_exclusive]
    successful_memory_write = request_log_operation_clause("memory_write") & (RequestLog.status_code < 400)
    successful_memory_search = request_log_operation_clause("memory_search") & (RequestLog.status_code < 400)
    totals_row = db.execute(
        select(
            func.count(RequestLog.id),
            func.sum(case((RequestLog.status_code >= 400, 1), else_=0)),
            func.sum(case((successful_memory_write, 1), else_=0)),
            func.sum(case((successful_memory_search, 1), else_=0)),
        ).where(*scoped)
    ).one()
    series_rows = db.execute(
        select(
            func.date(RequestLog.created_at),
            func.count(RequestLog.id),
            func.sum(case((successful_memory_write, 1), else_=0)),
            func.sum(case((successful_memory_search, 1), else_=0)),
        )
        .where(*scoped)
        .group_by(func.date(RequestLog.created_at))
        .order_by(func.date(RequestLog.created_at))
    ).all()
    by_date = {
        str(row[0]): {
            "api_requests": int(row[1] or 0),
            "memory_writes": int(row[2] or 0),
            "memory_searches": int(row[3] or 0),
        }
        for row in series_rows
    }
    series = []
    for offset in range(range_days):
        day = (start + timedelta(days=offset)).date().isoformat()
        series.append({"date": day, **by_date.get(day, {"api_requests": 0, "memory_writes": 0, "memory_searches": 0})})

    memory_counts = _count_project_memories(project_ids)
    stored_memories = sum(memory_counts.values())
    breakdown_filters = [*scoped, RequestLog.status_code < 400]
    key_rows = db.execute(
        select(RequestLog.api_key_id, func.count(RequestLog.id))
        .where(*breakdown_filters, RequestLog.api_key_id.is_not(None))
        .group_by(RequestLog.api_key_id)
        .order_by(func.count(RequestLog.id).desc())
        .limit(20)
    ).all()
    member_rows = db.execute(
        select(RequestLog.actor_user_id, func.count(RequestLog.id))
        .where(*breakdown_filters, RequestLog.actor_user_id.is_not(None))
        .group_by(RequestLog.actor_user_id)
        .order_by(func.count(RequestLog.id).desc())
        .limit(20)
    ).all()
    key_ids = [row[0] for row in key_rows]
    member_ids = [row[0] for row in member_rows]
    key_map = {
        key.id: key for key in db.execute(select(APIKey).where(APIKey.id.in_(key_ids or [uuid.UUID(int=0)]))).scalars()
    }
    member_map = {
        member.id: member
        for member in db.execute(select(User).where(User.id.in_(member_ids or [uuid.UUID(int=0)]))).scalars()
    }

    context = _summary_context(scope_type, resolved_scope_id, resolved_project_id, request, user, db)
    effective = []
    policies = applicable_policies(db, context)
    if scope_type == "organization":
        policies = [policy for policy in policies if policy.scope_type == "organization"]
    storage_counts_by_scope: dict[tuple[str, str], int] = {}
    for policy in policies:
        if policy.metric != "stored_memories":
            continue
        if policy.scope_type == "organization":
            org_project_ids = [
                str(project.get("id"))
                for project in settings.get("projects", [])
                if project.get("id") and project.get("organization_id") == policy.scope_id
            ]
            missing = [item for item in org_project_ids if item not in memory_counts]
            memory_counts.update(_count_project_memories(missing))
            storage_counts_by_scope[(policy.scope_type, policy.scope_id)] = sum(
                memory_counts.get(item, 0) for item in org_project_ids
            )
        elif policy.scope_type == "project":
            if policy.scope_id not in memory_counts:
                memory_counts.update(_count_project_memories([policy.scope_id]))
            storage_counts_by_scope[(policy.scope_type, policy.scope_id)] = memory_counts.get(policy.scope_id, 0)
    for policy in policies:
        used = (
            storage_counts_by_scope.get((policy.scope_type, policy.scope_id), 0)
            if policy.metric == "stored_memories"
            else policy_usage(db, policy, context)
        )
        effective.append(
            {**_policy_json(policy), "used": used, "percent": min(100, round((used / policy.limit_value) * 100, 1))}
        )

    can_manage = _is_global_admin(request, user) or (
        role_allows_manage(organization_role(settings, user.email if user else None, resolved_scope_id))
        if scope_type == "organization"
        else role_allows_manage(member_role(settings, user.email if user else None, resolved_project_id))
    )
    return {
        "scope": {
            "type": scope_type,
            "id": resolved_scope_id,
            "project_id": resolved_project_id,
            "organization_id": resolved_scope_id if scope_type == "organization" else active_org_id,
        },
        "period": {"days": range_days, "start": range_start, "end": range_end},
        "totals": {
            "stored_memories": stored_memories,
            "api_requests": int(totals_row[0] or 0),
            "errors": int(totals_row[1] or 0),
            "memory_writes": int(totals_row[2] or 0),
            "memory_searches": int(totals_row[3] or 0),
        },
        "series": series,
        "breakdown": {
            "api_keys": [
                {
                    "id": str(key_id),
                    "label": key_map.get(key_id).label if key_map.get(key_id) else "Deleted key",
                    "requests": int(count),
                }
                for key_id, count in key_rows
            ],
            "members": [
                {
                    "id": str(member_id),
                    "email": member_map.get(member_id).email if member_map.get(member_id) else "Deleted member",
                    "requests": int(count),
                }
                for member_id, count in member_rows
            ],
        },
        "effective_limits": effective,
        "can_manage": can_manage,
        "metering": {
            "model_tokens_available": False,
            "reason": "The configured model provider does not expose token usage to YiQiao.",
        },
    }
