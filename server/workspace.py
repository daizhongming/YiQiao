from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

WORKSPACE_KEY = "workspace_settings"
DEFAULT_ORG_ID = "org_default"
DEFAULT_PROJECT_ID = "default-project"

DEFAULT_WORKSPACE_SETTINGS: dict[str, Any] = {
    "organization": {"id": DEFAULT_ORG_ID, "name": "Default organization"},
    "organizations": [{"id": DEFAULT_ORG_ID, "name": "Default organization"}],
    "active_organization_id": DEFAULT_ORG_ID,
    "active_project_id": DEFAULT_PROJECT_ID,
    "projects": [
        {
            "id": DEFAULT_PROJECT_ID,
            "name": DEFAULT_PROJECT_ID,
            "description": "",
            "is_default": True,
            "organization_id": DEFAULT_ORG_ID,
        }
    ],
    "members": [],
    "extraction": {
        "multilingual": True,
        "use_case": "",
        "memory_depth": "Essential Insights",
        "include": "",
        "exclude": "",
        "custom_instructions": "",
    },
    "categories": [],
    "retention": {"memory_decay": True, "expiration_date": None},
    "playground": {
        "custom_instructions": "",
        "categories": [],
        "includes_prompt": "",
        "excludes_prompt": "",
        "force_add_only": False,
        "reranking": False,
        "temperature": 0.1,
        "threshold": 0.2,
        "max_tokens": 2048,
        "top_k": 10,
        "top_p": 1.0,
    },
}

ROLE_ALIASES = {
    "owner": "OWNER",
    "admin": "OWNER",
    "editor": "EDITOR",
    "edit": "EDITOR",
    "writer": "EDITOR",
    "member": "READER",
    "reader": "READER",
    "read": "READER",
}
READ_ROLES = {"owner", "admin", "editor", "edit", "member", "reader", "writer"}
WRITE_ROLES = {"owner", "admin", "editor", "edit", "writer"}
MANAGE_ROLES = {"owner", "admin"}


def _new_scoped_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clean_name(value: str | None, fallback: str) -> str:
    name = str(value or "").strip()
    return name or fallback


def _slug(value: str | None, fallback: str) -> str:
    text = _clean_name(value, fallback).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def normalize_member_role(role: str | None, default: str = "READER") -> str:
    if not role:
        return default
    return ROLE_ALIASES.get(str(role).strip().lower(), default)


def _role_rank(role: str | None) -> int:
    if role_allows_manage(role):
        return 3
    if role_allows_write(role):
        return 2
    if role_allows_read(role):
        return 1
    return 0


def _strongest_role(left: str | None, right: str | None) -> str | None:
    return left if _role_rank(left) >= _role_rank(right) else right


def normalize_workspace(settings: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(settings or DEFAULT_WORKSPACE_SETTINGS)
    org = data.get("organization") or DEFAULT_WORKSPACE_SETTINGS["organization"]
    organizations = data.get("organizations")
    if not isinstance(organizations, list) or not organizations:
        organizations = [org]
    normalized_orgs = []
    seen_orgs = set()
    for item in organizations:
        if not isinstance(item, dict):
            continue
        org_id = str(item.get("id") or _new_scoped_id("org"))
        if org_id in seen_orgs:
            continue
        seen_orgs.add(org_id)
        normalized_orgs.append({"id": org_id, "name": _clean_name(item.get("name"), org_id)})
    if not normalized_orgs:
        normalized_orgs = deepcopy(DEFAULT_WORKSPACE_SETTINGS["organizations"])
    organizations = normalized_orgs
    data["organizations"] = organizations
    data["active_organization_id"] = data.get("active_organization_id") or organizations[0]["id"]
    data["organization"] = next(
        (item for item in organizations if item.get("id") == data["active_organization_id"]),
        organizations[0],
    )
    projects = data.get("projects")
    if not isinstance(projects, list) or not projects:
        projects = deepcopy(DEFAULT_WORKSPACE_SETTINGS["projects"])
    normalized_projects = []
    seen_projects = set()
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = str(project.get("id") or _slug(project.get("name"), _new_scoped_id("project")))
        if project_id in seen_projects:
            continue
        seen_projects.add(project_id)
        org_id = str(project.get("organization_id") or data["active_organization_id"])
        if not any(item.get("id") == org_id for item in organizations):
            org_id = data["active_organization_id"]
        normalized_project = dict(project)
        normalized_project["id"] = project_id
        normalized_project["name"] = _clean_name(project.get("name"), project_id)
        normalized_project["description"] = str(project.get("description") or "")
        normalized_project["organization_id"] = org_id
        normalized_projects.append(normalized_project)
    data["projects"] = normalized_projects or deepcopy(DEFAULT_WORKSPACE_SETTINGS["projects"])
    if not any(project.get("id") == data.get("active_project_id") for project in data["projects"]):
        data["active_project_id"] = data["projects"][0]["id"]
    project_orgs = {project.get("id"): project.get("organization_id") for project in data["projects"]}
    members = data.get("members") if isinstance(data.get("members"), list) else []
    normalized_members = []
    seen_members = set()
    for member in members:
        if not isinstance(member, dict):
            continue
        email = str(member.get("email") or "").strip().lower()
        if not email:
            continue
        project_id = member.get("project_id")
        project_id = str(project_id) if project_id else None
        if project_id and project_id not in project_orgs:
            continue
        org_id = member.get("organization_id") or (project_orgs.get(project_id) if project_id else None)
        org_id = str(org_id or data.get("active_organization_id") or DEFAULT_ORG_ID)
        if not any(org.get("id") == org_id for org in organizations):
            org_id = data.get("active_organization_id") or organizations[0]["id"]
        status = str(member.get("status") or "invited").lower()
        if status not in {"active", "invited"}:
            status = "invited"
        normalized = {
            "email": email,
            "role": normalize_member_role(member.get("role")),
            "status": status,
            "project_id": project_id,
            "organization_id": org_id,
        }
        key = (email, project_id, org_id)
        if key in seen_members:
            continue
        seen_members.add(key)
        normalized_members.append(normalized)
    data["members"] = normalized_members
    return data


def role_allows_read(role: str | None) -> bool:
    return (role or "").lower() in READ_ROLES


def role_allows_write(role: str | None) -> bool:
    return (role or "").lower() in WRITE_ROLES


def role_allows_manage(role: str | None) -> bool:
    return (role or "").lower() in MANAGE_ROLES


def member_role(settings: dict[str, Any], email: str | None, project_id: str) -> str | None:
    if not email:
        return None
    email_key = email.lower()
    data = normalize_workspace(settings)
    project = find_project(data, project_id)
    org_id = project.get("organization_id") if project else None
    best: str | None = None
    for member in data.get("members", []):
        if str(member.get("email", "")).lower() != email_key:
            continue
        if member.get("status", "active") != "active":
            continue
        member_project = member.get("project_id")
        member_org = member.get("organization_id")
        if member_project and member_project != project_id:
            continue
        role = member.get("role")
        if not member_project and member_org != org_id:
            continue
        if role_allows_manage(role):
            return role
        if role_allows_write(role) or role_allows_read(role):
            best = _strongest_role(best, role)
    return best


def organization_role(settings: dict[str, Any], email: str | None, org_id: str) -> str | None:
    if not email:
        return None
    data = normalize_workspace(settings)
    best: str | None = None
    for member in data.get("members", []):
        if str(member.get("email", "")).lower() != str(email).lower():
            continue
        if member.get("status", "active") != "active":
            continue
        if member.get("project_id"):
            continue
        if member.get("organization_id") != org_id:
            continue
        role = member.get("role")
        if role_allows_manage(role):
            return role
        if role_allows_write(role) or role_allows_read(role):
            best = _strongest_role(best, role)
    return best


def organization_access_role(settings: dict[str, Any], email: str | None, org_id: str) -> str | None:
    if not email:
        return None
    data = normalize_workspace(settings)
    best = organization_role(data, email, org_id)
    org_project_ids = {
        project.get("id") for project in data.get("projects", []) if project.get("organization_id") == org_id
    }
    for member in data.get("members", []):
        if str(member.get("email", "")).lower() != str(email).lower():
            continue
        if member.get("status", "active") != "active":
            continue
        member_project = member.get("project_id")
        if member_project not in org_project_ids:
            continue
        best = _strongest_role(best, member.get("role"))
    return best


def find_organization(settings: dict[str, Any], org_id: str) -> dict[str, Any] | None:
    return next(
        (org for org in normalize_workspace(settings).get("organizations", []) if org.get("id") == org_id),
        None,
    )


def find_project(settings: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    return next(
        (project for project in normalize_workspace(settings).get("projects", []) if project.get("id") == project_id),
        None,
    )


def create_organization(settings: dict[str, Any], name: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    org_id = _new_scoped_id("org")
    data["organizations"].append({"id": org_id, "name": _clean_name(name, org_id)})
    data["active_organization_id"] = org_id
    data["organization"] = data["organizations"][-1]
    return data


def update_organization(settings: dict[str, Any], org_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = normalize_workspace(settings)
    updated = False
    for org in data["organizations"]:
        if org.get("id") == org_id:
            if "name" in patch:
                org["name"] = _clean_name(patch.get("name"), org_id)
            updated = True
            break
    if updated and data.get("active_organization_id") == org_id:
        data["organization"] = next(org for org in data["organizations"] if org.get("id") == org_id)
    return data


def delete_organization(settings: dict[str, Any], org_id: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    if org_id == DEFAULT_ORG_ID:
        return data
    project_ids = {project.get("id") for project in data["projects"] if project.get("organization_id") == org_id}
    data["organizations"] = [org for org in data["organizations"] if org.get("id") != org_id]
    data["projects"] = [project for project in data["projects"] if project.get("id") not in project_ids]
    data["members"] = [
        member
        for member in data["members"]
        if member.get("organization_id") != org_id
        and (not member.get("project_id") or member.get("project_id") not in project_ids)
    ]
    if not data["organizations"]:
        data["organizations"] = deepcopy(DEFAULT_WORKSPACE_SETTINGS["organizations"])
    data["active_organization_id"] = data["organizations"][0]["id"]
    data["organization"] = data["organizations"][0]
    if not data["projects"]:
        data["projects"] = deepcopy(DEFAULT_WORKSPACE_SETTINGS["projects"])
    data["active_project_id"] = data["projects"][0]["id"]
    return data


def create_project(settings: dict[str, Any], org_id: str, name: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    project_id = _new_scoped_id("project")
    data["projects"].append(
        {
            "id": project_id,
            "name": _clean_name(name, project_id),
            "description": "",
            "organization_id": org_id,
            "extraction": deepcopy(DEFAULT_WORKSPACE_SETTINGS["extraction"]),
            "categories": deepcopy(DEFAULT_WORKSPACE_SETTINGS["categories"]),
            "retention": deepcopy(DEFAULT_WORKSPACE_SETTINGS["retention"]),
            "playground": deepcopy(DEFAULT_WORKSPACE_SETTINGS["playground"]),
        }
    )
    data["active_organization_id"] = org_id
    data["active_project_id"] = project_id
    return data


def update_project(settings: dict[str, Any], project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = normalize_workspace(settings)
    allowed = {
        "name",
        "description",
        "custom_instructions",
        "custom_categories",
        "multilingual",
        "decay",
        "extraction",
        "categories",
        "retention",
        "playground",
        "retrieval_criteria",
    }
    for project in data["projects"]:
        if project.get("id") != project_id:
            continue
        for key, value in patch.items():
            if key in allowed:
                project[key] = value
        if "name" in patch:
            project["name"] = _clean_name(patch.get("name"), project_id)
        if "description" in patch:
            project["description"] = str(patch.get("description") or "")
        break
    return data


def delete_project(settings: dict[str, Any], project_id: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    if project_id == DEFAULT_PROJECT_ID:
        return data
    deleted_project = find_project(data, project_id)
    data["projects"] = [project for project in data["projects"] if project.get("id") != project_id]
    data["members"] = [member for member in data["members"] if member.get("project_id") != project_id]
    if not data["projects"]:
        data["projects"] = deepcopy(DEFAULT_WORKSPACE_SETTINGS["projects"])
    if data.get("active_project_id") == project_id:
        deleted_org_id = deleted_project.get("organization_id") if deleted_project else None
        fallback_project = next(
            (project for project in data["projects"] if project.get("organization_id") == deleted_org_id),
            data["projects"][0],
        )
        data["active_project_id"] = fallback_project["id"]
        data["active_organization_id"] = fallback_project.get("organization_id") or DEFAULT_ORG_ID
        data["organization"] = next(
            (
                organization
                for organization in data["organizations"]
                if organization.get("id") == data["active_organization_id"]
            ),
            data["organizations"][0],
        )
    return data


def project_settings(settings: dict[str, Any], project_id: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    project = find_project(data, project_id) or {}
    return {
        "extraction": {**(data.get("extraction") or {}), **(project.get("extraction") or {})},
        "categories": project.get("categories") if "categories" in project else data.get("categories", []),
        "retention": {**(data.get("retention") or {}), **(project.get("retention") or {})},
        "playground": {**(data.get("playground") or {}), **(project.get("playground") or {})},
    }


def copy_workspace_sections_to_project(settings: dict[str, Any], project_id: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    project_patch = {
        "extraction": data.get("extraction") or {},
        "categories": data.get("categories") or [],
        "retention": data.get("retention") or {},
        "playground": data.get("playground") or {},
    }
    return update_project(data, project_id, project_patch)


def invited_member(settings: dict[str, Any], email: str) -> dict[str, Any] | None:
    email_key = email.lower()
    for member in normalize_workspace(settings).get("members", []):
        if str(member.get("email", "")).lower() == email_key and member.get("status") == "invited":
            return member
    return None


def activate_member(settings: dict[str, Any], email: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    email_key = email.lower()
    for member in data.get("members", []):
        if str(member.get("email", "")).lower() == email_key:
            member["status"] = "active"
    return data


def replace_member_email(settings: dict[str, Any], old_email: str, new_email: str) -> dict[str, Any]:
    data = normalize_workspace(settings)
    old_key = str(old_email or "").strip().lower()
    new_key = str(new_email or "").strip().lower()
    if not old_key or not new_key or old_key == new_key:
        return data
    for member in data.get("members", []):
        if str(member.get("email", "")).lower() == old_key:
            member["email"] = new_key
    return normalize_workspace(data)


def upsert_member(
    settings: dict[str, Any],
    *,
    email: str,
    role: str,
    status: str = "invited",
    project_id: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    data = normalize_workspace(settings)
    email_key = email.lower()
    if project_id:
        project = find_project(data, project_id)
        organization_id = project.get("organization_id") if project else organization_id
    organization_id = organization_id or data.get("active_organization_id") or DEFAULT_ORG_ID
    members = [
        member
        for member in data.get("members", [])
        if str(member.get("email", "")).lower() != email_key
        or member.get("project_id") != project_id
        or member.get("organization_id") != organization_id
    ]
    members.append(
        {
            "email": email_key,
            "role": normalize_member_role(role),
            "status": status,
            "project_id": project_id,
            "organization_id": organization_id,
        }
    )
    data["members"] = members
    return data


def remove_member(
    settings: dict[str, Any],
    *,
    email: str,
    project_id: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    data = normalize_workspace(settings)
    email_key = email.lower()
    if project_id and organization_id is None:
        project = find_project(data, project_id)
        organization_id = project.get("organization_id") if project else None
    organization_id = organization_id or data.get("active_organization_id") or DEFAULT_ORG_ID
    data["members"] = [
        member
        for member in data.get("members", [])
        if str(member.get("email", "")).lower() != email_key
        or member.get("project_id") != project_id
        or member.get("organization_id") != organization_id
    ]
    return data


def visible_workspace(settings: dict[str, Any], email: str | None, is_owner: bool) -> dict[str, Any]:
    data = normalize_workspace(settings)
    if is_owner:
        return data
    visible_projects = [
        project for project in data.get("projects", []) if member_role(data, email, project.get("id", "")) is not None
    ]
    visible_org_ids = {project.get("organization_id") for project in visible_projects}
    manageable_project_ids = {
        project.get("id")
        for project in visible_projects
        if role_allows_manage(member_role(data, email, project.get("id", "")))
    }
    manageable_org_ids = {
        org.get("id")
        for org in data.get("organizations", [])
        if role_allows_manage(organization_role(data, email, org.get("id", "")))
    }
    data["projects"] = visible_projects
    data["organizations"] = [org for org in data.get("organizations", []) if org.get("id") in visible_org_ids]
    data["members"] = [
        member
        for member in data.get("members", [])
        if str(member.get("email", "")).lower() == str(email or "").lower()
        or member.get("project_id") in manageable_project_ids
        or member.get("organization_id") in manageable_org_ids
    ]
    if visible_projects:
        data["active_project_id"] = visible_projects[0]["id"]
    if data["organizations"]:
        data["active_organization_id"] = data["organizations"][0]["id"]
        data["organization"] = data["organizations"][0]
    return data
