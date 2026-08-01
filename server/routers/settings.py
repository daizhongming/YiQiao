from datetime import date
from typing import Any

from auth import verify_auth
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from models import APIKey, QuotaPolicy, RequestLog, User, Webhook
from neo4j_graph import delete_memories as delete_graph_memories
from project_scope import get_project_id, normalize_project_id
from pydantic import BaseModel, EmailStr
from server_state import get_memory_instance
from settings_store import get_json, set_json
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from workspace import (
    DEFAULT_ORG_ID,
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    create_organization,
    create_project,
    delete_organization,
    delete_project,
    find_organization,
    find_project,
    member_role,
    normalize_member_role,
    normalize_workspace,
    organization_access_role,
    organization_role,
    remove_member,
    role_allows_manage,
    role_allows_read,
    update_organization,
    update_project,
    upsert_member,
    visible_workspace,
)

router = APIRouter(prefix="/settings", tags=["settings"])
cloud_router = APIRouter(prefix="/api/v1/orgs", tags=["organizations"])


class SettingsPatch(BaseModel):
    data: dict[str, Any]


class MemberInvite(BaseModel):
    email: EmailStr
    role: str = "READER"
    project_id: str | None = None
    organization_id: str | None = None


class MemberUpdate(BaseModel):
    role: str | None = None
    status: str | None = None
    project_id: str | None = None
    organization_id: str | None = None


VALID_MEMBER_STATUSES = {"active", "invited"}


def _normalize_role(role: str | None) -> str:
    normalized = normalize_member_role(role)
    if normalized not in {"OWNER", "EDITOR", "READER"}:
        raise HTTPException(status_code=400, detail="Member role must be READER, EDITOR, or OWNER.")
    return normalized


def _normalize_status(status: str | None) -> str:
    if not status:
        return "invited"
    value = status.strip().lower()
    if value not in VALID_MEMBER_STATUSES:
        raise HTTPException(status_code=400, detail="Member status must be active or invited.")
    return value


def _ensure_project_exists(settings: dict[str, Any], project_id: str) -> None:
    workspace = normalize_workspace(settings)
    if not any(project.get("id") == project_id for project in workspace.get("projects", [])):
        raise HTTPException(status_code=404, detail="Project not found.")


def _is_global_admin(request: Request, user: User | None) -> bool:
    auth_type = getattr(request.state, "auth_type", "none")
    if auth_type in {"admin_api_key", "disabled"}:
        return True
    return auth_type == "bearer" and user is not None and user.role == "admin"


def _deny_project_api_key_control_plane(request: Request) -> None:
    if getattr(request.state, "auth_type", "none") == "api_key":
        raise HTTPException(
            status_code=403,
            detail="Project API keys cannot manage workspace settings.",
        )


def _ensure_org_exists(settings: dict[str, Any], org_id: str) -> dict[str, Any]:
    org = find_organization(settings, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


def _ensure_project_in_org(settings: dict[str, Any], org_id: str, project_id: str) -> dict[str, Any]:
    project = find_project(settings, project_id)
    if project is None or project.get("organization_id") != org_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _ensure_org_read(settings: dict[str, Any], org_id: str, request: Request, user: User | None) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = organization_access_role(settings, user.email if user else None, org_id)
    if not role_allows_read(role):
        raise HTTPException(status_code=403, detail="Organization access denied.")


def _ensure_org_write(settings: dict[str, Any], org_id: str, request: Request, user: User | None) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = organization_role(settings, user.email if user else None, org_id)
    if not role_allows_manage(role):
        raise HTTPException(status_code=403, detail="Organization access denied.")


def _ensure_project_read(settings: dict[str, Any], project_id: str, request: Request, user: User | None) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = member_role(settings, user.email if user else None, project_id)
    if not role_allows_read(role):
        raise HTTPException(status_code=403, detail="Project access denied.")


def _ensure_project_write(settings: dict[str, Any], project_id: str, request: Request, user: User | None) -> None:
    _deny_project_api_key_control_plane(request)
    if _is_global_admin(request, user):
        return
    role = member_role(settings, user.email if user else None, project_id)
    if not role_allows_manage(role):
        raise HTTPException(status_code=403, detail="Project access denied.")


def _invitation_status(db: Session, email: str) -> str:
    if not hasattr(db, "scalar"):
        return "invited"
    existing_user_id = db.scalar(select(User.id).where(func.lower(User.email) == email.strip().lower()))
    return "active" if existing_user_id is not None else "invited"


def _ensure_owner_remains(
    settings: dict[str, Any],
    *,
    email: str,
    organization_id: str,
    project_id: str | None,
    next_role: str | None = None,
) -> None:
    if next_role == "OWNER":
        return
    email_key = email.strip().lower()
    workspace = normalize_workspace(settings)
    target = next(
        (
            member
            for member in workspace.get("members", [])
            if str(member.get("email", "")).lower() == email_key
            and member.get("organization_id") == organization_id
            and member.get("project_id") == project_id
        ),
        None,
    )
    if not target or target.get("status") != "active" or target.get("role") != "OWNER":
        return

    other_owner_exists = any(
        str(member.get("email", "")).lower() != email_key
        and member.get("status") == "active"
        and member.get("role") == "OWNER"
        and member.get("organization_id") == organization_id
        and (member.get("project_id") == project_id or (project_id is not None and member.get("project_id") is None))
        for member in workspace.get("members", [])
    )
    if not other_owner_exists:
        scope = "project" if project_id else "organization"
        raise HTTPException(
            status_code=400,
            detail=f"At least one active {scope} owner is required.",
        )


def _purge_project_resources(db: Session, project_id: str) -> None:
    if not isinstance(db, Session):
        return

    memory = get_memory_instance()
    storage_project_ids = (project_id, f"{project_id[:108]}.__playground__")
    for storage_project_id in storage_project_ids:
        listed = memory.vector_store.list(
            filters={"project_id": storage_project_id},
            top_k=1_000_000,
        )
        rows = listed[0] if isinstance(listed, (list, tuple)) and listed and isinstance(listed[0], list) else listed
        memory_ids = [str(row.id) for row in rows or [] if getattr(row, "id", None)]
        memory.delete_all(project_id=storage_project_id)
        memory.db.delete_project_data(storage_project_id, memory_ids)
        delete_graph_memories(storage_project_id, {})

    db.execute(sql_delete(APIKey).where(APIKey.project_id == project_id))
    db.execute(sql_delete(Webhook).where(Webhook.project_id == project_id))
    db.execute(sql_delete(RequestLog).where(RequestLog.project_id == project_id))
    db.execute(
        sql_delete(QuotaPolicy).where(
            or_(
                QuotaPolicy.project_id == project_id,
                (QuotaPolicy.scope_type == "project") & (QuotaPolicy.scope_id == project_id),
            )
        )
    )
    exports = get_json(db, "memory_exports", [])
    if isinstance(exports, list):
        set_json(
            db,
            "memory_exports",
            [item for item in exports if not isinstance(item, dict) or item.get("project_id") != project_id],
            commit=False,
        )


def _project_response(project: dict[str, Any]) -> dict[str, Any]:
    response = dict(project)
    response.setdefault("project_id", project.get("id"))
    response.setdefault("org_id", project.get("organization_id"))
    extraction = project.get("extraction") if isinstance(project.get("extraction"), dict) else {}
    retention = project.get("retention") if isinstance(project.get("retention"), dict) else {}
    if "custom_instructions" not in response and extraction.get("custom_instructions") is not None:
        response["custom_instructions"] = extraction.get("custom_instructions")
    if "custom_categories" not in response:
        response["custom_categories"] = project.get("categories", [])
    if "multilingual" not in response and extraction.get("multilingual") is not None:
        response["multilingual"] = extraction.get("multilingual")
    if "decay" not in response and retention.get("memory_decay") is not None:
        response["decay"] = retention.get("memory_decay")
    return response


def _project_patch_from_body(project: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    for key in ("name", "description", "custom_instructions", "custom_categories", "retrieval_criteria"):
        if key in body:
            patch[key] = body[key]

    if "multilingual" in body:
        extraction = dict(project.get("extraction") or {})
        extraction["multilingual"] = bool(body["multilingual"])
        patch["extraction"] = extraction

    if "decay" in body:
        retention = dict(project.get("retention") or {})
        retention["memory_decay"] = bool(body["decay"])
        patch["retention"] = retention

    if "custom_instructions" in body:
        extraction = dict(patch.get("extraction") or project.get("extraction") or {})
        value = body["custom_instructions"]
        extraction["custom_instructions"] = "\n".join(value) if isinstance(value, list) else str(value or "")
        patch["extraction"] = extraction

    if "custom_categories" in body:
        raw_categories = body["custom_categories"] or []
        categories = []
        for item in raw_categories if isinstance(raw_categories, list) else [raw_categories]:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    categories.append({"name": name, "description": str(item.get("description") or "")})
            else:
                name = str(item or "").strip()
                if name:
                    categories.append({"name": name, "description": ""})
        patch["categories"] = categories

    if "extraction" in body:
        incoming = body["extraction"]
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="Extraction settings must be an object.")
        extraction = dict(project.get("extraction") or {})
        if "multilingual" in incoming:
            if not isinstance(incoming["multilingual"], bool):
                raise HTTPException(status_code=400, detail="multilingual must be a boolean.")
            extraction["multilingual"] = incoming["multilingual"]
        for key in ("use_case", "memory_depth", "include", "exclude", "custom_instructions"):
            if key in incoming:
                value = incoming[key]
                if value is not None and not isinstance(value, str):
                    raise HTTPException(status_code=400, detail=f"{key} must be a string.")
                extraction[key] = str(value or "")
        patch["extraction"] = extraction

    if "categories" in body:
        raw_categories = body["categories"]
        if not isinstance(raw_categories, list):
            raise HTTPException(status_code=400, detail="Categories must be an array.")
        categories: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_categories:
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail="Each category must be an object.")
            name = str(item.get("name") or "").strip()
            if not name:
                raise HTTPException(status_code=400, detail="Each category requires a name.")
            normalized_name = name.lower()
            if normalized_name in seen:
                continue
            seen.add(normalized_name)
            categories.append(
                {
                    "name": name[:128],
                    "description": str(item.get("description") or "")[:1000],
                }
            )
        if len(categories) > 100:
            raise HTTPException(status_code=400, detail="A project can have at most 100 categories.")
        patch["categories"] = categories

    if "retention" in body:
        incoming = body["retention"]
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="Retention settings must be an object.")
        retention = dict(project.get("retention") or {})
        if "memory_decay" in incoming:
            if not isinstance(incoming["memory_decay"], bool):
                raise HTTPException(status_code=400, detail="memory_decay must be a boolean.")
            retention["memory_decay"] = incoming["memory_decay"]
        if "expiration_date" in incoming:
            expiration_date = incoming["expiration_date"]
            if expiration_date in (None, ""):
                retention["expiration_date"] = None
            elif not isinstance(expiration_date, str):
                raise HTTPException(status_code=400, detail="expiration_date must be an ISO date.")
            else:
                try:
                    date.fromisoformat(expiration_date)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="expiration_date must be an ISO date.") from exc
                retention["expiration_date"] = expiration_date
        patch["retention"] = retention

    if "playground" in body:
        incoming = body["playground"]
        if not isinstance(incoming, dict):
            raise HTTPException(status_code=400, detail="Playground settings must be an object.")
        playground = dict(project.get("playground") or {})
        if "custom_instructions" in incoming:
            value = incoming["custom_instructions"]
            if value is not None and not isinstance(value, str):
                raise HTTPException(status_code=400, detail="custom_instructions must be a string.")
            playground["custom_instructions"] = str(value or "")
        for key in ("force_add_only", "reranking"):
            if key in incoming:
                if not isinstance(incoming[key], bool):
                    raise HTTPException(status_code=400, detail=f"{key} must be a boolean.")
                playground[key] = incoming[key]
        numeric_rules = {
            "temperature": (0.0, 2.0, False),
            "threshold": (0.0, 1.0, False),
            "top_p": (0.0, 1.0, False),
            "top_k": (1, 100, True),
            "max_tokens": (1, 131_072, True),
        }
        for key, (minimum, maximum, integer_only) in numeric_rules.items():
            if key not in incoming:
                continue
            value = incoming[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise HTTPException(status_code=400, detail=f"{key} must be a number.")
            if integer_only and not isinstance(value, int):
                raise HTTPException(status_code=400, detail=f"{key} must be an integer.")
            if value < minimum or value > maximum:
                raise HTTPException(
                    status_code=400,
                    detail=f"{key} must be between {minimum} and {maximum}.",
                )
            playground[key] = value
        patch["playground"] = playground
    return patch


@router.get("/workspace")
def get_workspace_settings(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    settings = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    return visible_workspace(settings, user.email if user else None, _is_global_admin(request, user))


@router.patch("/workspace")
def update_workspace_settings(
    request: Request,
    body: SettingsPatch,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = normalize_workspace(get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS))
    project_id = normalize_project_id(str(body.data.get("active_project_id") or get_project_id(request)))
    project = find_project(current, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    _ensure_project_write(current, project_id, request, user)

    patch_body = {
        key: body.data[key] for key in ("extraction", "categories", "retention", "playground") if key in body.data
    }
    incoming_projects = body.data.get("projects")
    if isinstance(incoming_projects, list):
        incoming_project = next(
            (item for item in incoming_projects if isinstance(item, dict) and str(item.get("id")) == project_id),
            None,
        )
        if incoming_project:
            for key in ("name", "description"):
                if key in incoming_project:
                    patch_body[key] = incoming_project[key]

    next_settings = update_project(current, project_id, _project_patch_from_body(project, patch_body))
    saved = set_json(db, WORKSPACE_KEY, normalize_workspace(next_settings))
    return visible_workspace(saved, user.email if user else None, _is_global_admin(request, user))


@router.post("/workspace/members")
def invite_workspace_member(
    request: Request,
    body: MemberInvite,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    project_id = normalize_project_id(body.project_id) if body.project_id else get_project_id(request)
    _ensure_project_exists(current, project_id)
    _ensure_project_write(current, project_id, request, user)
    project = find_project(current, project_id) or {}
    next_settings = upsert_member(
        current,
        email=str(body.email),
        role=_normalize_role(body.role),
        status="invited",
        project_id=project_id,
        organization_id=str(project.get("organization_id") or body.organization_id or DEFAULT_ORG_ID),
    )
    return set_json(db, WORKSPACE_KEY, next_settings)


@router.patch("/workspace/members/{email}")
def update_workspace_member(
    request: Request,
    email: EmailStr,
    body: MemberUpdate,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    project_id = normalize_project_id(body.project_id) if body.project_id else get_project_id(request)
    _ensure_project_exists(current, project_id)
    _ensure_project_write(current, project_id, request, user)
    workspace = normalize_workspace(current)
    project = find_project(workspace, project_id) or {}
    org_id = str(project.get("organization_id") or body.organization_id or DEFAULT_ORG_ID)
    existing = next(
        (
            member
            for member in workspace.get("members", [])
            if str(member.get("email", "")).lower() == str(email).lower()
            and member.get("project_id") == project_id
            and member.get("organization_id") == org_id
        ),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    next_settings = upsert_member(
        current,
        email=str(email),
        role=_normalize_role(body.role or existing.get("role")),
        status=_normalize_status(body.status or existing.get("status")),
        project_id=project_id,
        organization_id=org_id,
    )
    return set_json(db, WORKSPACE_KEY, next_settings)


@router.delete("/workspace/members/{email}")
def delete_workspace_member(
    request: Request,
    email: EmailStr,
    project_id: str | None = Query(None),
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    if user and str(user.email).lower() == str(email).lower():
        raise HTTPException(status_code=400, detail="You cannot remove your own active workspace membership.")
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    target_project_id = normalize_project_id(project_id) if project_id else get_project_id(request)
    _ensure_project_exists(current, target_project_id)
    _ensure_project_write(current, target_project_id, request, user)
    project = find_project(current, target_project_id) or {}
    return set_json(
        db,
        WORKSPACE_KEY,
        remove_member(
            current,
            email=str(email),
            project_id=target_project_id,
            organization_id=str(project.get("organization_id") or DEFAULT_ORG_ID),
        ),
    )


@cloud_router.get("/organizations/")
def list_organizations(
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    workspace = visible_workspace(current, user.email if user else None, _is_global_admin(request, user))
    return workspace.get("organizations", [])


@cloud_router.post("/organizations/")
def create_org(
    request: Request,
    body: dict[str, Any],
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    if not _is_global_admin(request, user):
        raise HTTPException(status_code=403, detail="Admin role required.")
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    next_settings = create_organization(current, str(body.get("name") or "Organization"))
    saved = set_json(db, WORKSPACE_KEY, next_settings)
    return saved["organization"]


@cloud_router.get("/organizations/{org_id}/")
def get_org(
    org_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    org = _ensure_org_exists(current, org_id)
    _ensure_org_read(current, org_id, request, user)
    return org


@cloud_router.patch("/organizations/{org_id}/")
def update_org(
    org_id: str,
    body: dict[str, Any],
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    saved = set_json(db, WORKSPACE_KEY, update_organization(current, org_id, {"name": body.get("name")}))
    return _ensure_org_exists(saved, org_id)


@cloud_router.delete("/organizations/{org_id}/")
def delete_org(
    org_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    if org_id == DEFAULT_ORG_ID:
        raise HTTPException(status_code=400, detail="Default organization cannot be deleted.")
    project_ids = [
        str(project.get("id"))
        for project in normalize_workspace(current).get("projects", [])
        if project.get("id") and project.get("organization_id") == org_id
    ]
    for project_id in project_ids:
        _purge_project_resources(db, project_id)
    if isinstance(db, Session):
        db.execute(
            sql_delete(QuotaPolicy).where((QuotaPolicy.scope_type == "organization") & (QuotaPolicy.scope_id == org_id))
        )
        db.execute(sql_delete(RequestLog).where(RequestLog.organization_id == org_id))
    set_json(db, WORKSPACE_KEY, delete_organization(current, org_id))
    request.state.suppress_request_log = True
    return {"message": "Organization deleted"}


@cloud_router.get("/organizations/{org_id}/members/")
def get_org_members(
    org_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_read(current, org_id, request, user)
    workspace = normalize_workspace(current)
    project_ids = {project.get("id") for project in workspace["projects"] if project.get("organization_id") == org_id}
    return [
        member
        for member in workspace["members"]
        if member.get("organization_id") == org_id
        and (not member.get("project_id") or member.get("project_id") in project_ids)
    ]


@cloud_router.post("/organizations/{org_id}/members/")
def add_org_member(
    org_id: str,
    body: MemberInvite,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    return set_json(
        db,
        WORKSPACE_KEY,
        upsert_member(
            current,
            email=str(body.email),
            role=_normalize_role(body.role),
            status=_invitation_status(db, str(body.email)),
            project_id=None,
            organization_id=org_id,
        ),
    )


@cloud_router.put("/organizations/{org_id}/members/")
def update_org_member(
    org_id: str,
    body: MemberInvite,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    existing = next(
        (
            member
            for member in normalize_workspace(current).get("members", [])
            if str(member.get("email", "")).lower() == str(body.email).lower()
            and member.get("organization_id") == org_id
            and not member.get("project_id")
        ),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    next_role = _normalize_role(body.role)
    _ensure_owner_remains(
        current,
        email=str(body.email),
        organization_id=org_id,
        project_id=None,
        next_role=next_role,
    )
    return set_json(
        db,
        WORKSPACE_KEY,
        upsert_member(
            current,
            email=str(body.email),
            role=next_role,
            status=_normalize_status(existing.get("status")),
            project_id=None,
            organization_id=org_id,
        ),
    )


@cloud_router.delete("/organizations/{org_id}/members/")
def remove_org_member(
    org_id: str,
    body: dict[str, Any],
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    email = body.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="email is required.")
    if user and str(user.email).lower() == str(email).lower():
        raise HTTPException(status_code=400, detail="You cannot remove your own active organization membership.")
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    _ensure_owner_remains(
        current,
        email=str(email),
        organization_id=org_id,
        project_id=None,
    )
    return set_json(
        db, WORKSPACE_KEY, remove_member(current, email=str(email), project_id=None, organization_id=org_id)
    )


@cloud_router.get("/organizations/{org_id}/projects/")
def list_projects(
    org_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_read(current, org_id, request, user)
    workspace = visible_workspace(current, user.email if user else None, _is_global_admin(request, user))
    return [
        _project_response(project)
        for project in workspace.get("projects", [])
        if project.get("organization_id") == org_id
    ]


@cloud_router.post("/organizations/{org_id}/projects/")
def create_project_route(
    org_id: str,
    body: dict[str, Any],
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_org_exists(current, org_id)
    _ensure_org_write(current, org_id, request, user)
    next_settings = create_project(current, org_id, str(body.get("name") or "Project"))
    if body.get("description"):
        next_settings = update_project(
            next_settings, next_settings["active_project_id"], {"description": body["description"]}
        )
    saved = set_json(db, WORKSPACE_KEY, next_settings)
    project = find_project(saved, saved["active_project_id"])
    return _project_response(project or {})


@cloud_router.get("/organizations/{org_id}/projects/{project_id}/")
def get_project_route(
    org_id: str,
    project_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    project = _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_read(current, project_id, request, user)
    return _project_response(project)


@cloud_router.patch("/organizations/{org_id}/projects/{project_id}/")
def update_project_route(
    org_id: str,
    project_id: str,
    body: dict[str, Any],
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    project = _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_write(current, project_id, request, user)
    next_settings = update_project(current, project_id, _project_patch_from_body(project, body))
    saved = set_json(db, WORKSPACE_KEY, next_settings)
    return _project_response(find_project(saved, project_id) or {})


@cloud_router.delete("/organizations/{org_id}/projects/{project_id}/")
def delete_project_route(
    org_id: str,
    project_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_write(current, project_id, request, user)
    if project_id == DEFAULT_PROJECT_ID:
        raise HTTPException(status_code=400, detail="Default project cannot be deleted.")
    _purge_project_resources(db, project_id)
    set_json(db, WORKSPACE_KEY, delete_project(current, project_id))
    request.state.suppress_request_log = True
    return {"message": "Project deleted"}


@cloud_router.get("/organizations/{org_id}/projects/{project_id}/members/")
def get_project_members(
    org_id: str,
    project_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_read(current, project_id, request, user)
    workspace = normalize_workspace(current)
    return [member for member in workspace["members"] if member.get("project_id") == project_id]


@cloud_router.post("/organizations/{org_id}/projects/{project_id}/members/")
def add_project_member(
    org_id: str,
    project_id: str,
    body: MemberInvite,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_write(current, project_id, request, user)
    return set_json(
        db,
        WORKSPACE_KEY,
        upsert_member(
            current,
            email=str(body.email),
            role=_normalize_role(body.role),
            status=_invitation_status(db, str(body.email)),
            project_id=project_id,
            organization_id=org_id,
        ),
    )


@cloud_router.put("/organizations/{org_id}/projects/{project_id}/members/")
def update_project_member(
    org_id: str,
    project_id: str,
    body: MemberInvite,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_write(current, project_id, request, user)
    existing = next(
        (
            member
            for member in normalize_workspace(current).get("members", [])
            if str(member.get("email", "")).lower() == str(body.email).lower()
            and member.get("organization_id") == org_id
            and member.get("project_id") == project_id
        ),
        None,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Member not found.")
    next_role = _normalize_role(body.role)
    _ensure_owner_remains(
        current,
        email=str(body.email),
        organization_id=org_id,
        project_id=project_id,
        next_role=next_role,
    )
    return set_json(
        db,
        WORKSPACE_KEY,
        upsert_member(
            current,
            email=str(body.email),
            role=next_role,
            status=_normalize_status(existing.get("status")),
            project_id=project_id,
            organization_id=org_id,
        ),
    )


@cloud_router.delete("/organizations/{org_id}/projects/{project_id}/members/")
def delete_project_member(
    org_id: str,
    project_id: str,
    email: EmailStr,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    if user and str(user.email).lower() == str(email).lower():
        raise HTTPException(status_code=400, detail="You cannot remove your own active project membership.")
    current = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    _ensure_project_in_org(current, org_id, project_id)
    _ensure_project_write(current, project_id, request, user)
    _ensure_owner_remains(
        current,
        email=str(email),
        organization_id=org_id,
        project_id=project_id,
    )
    return set_json(
        db, WORKSPACE_KEY, remove_member(current, email=str(email), project_id=project_id, organization_id=org_id)
    )
