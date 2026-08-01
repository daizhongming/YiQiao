import os
import sys
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from db import get_db  # noqa: E402
from models import User  # noqa: E402
from routers import settings as settings_router  # noqa: E402
from workspace import (  # noqa: E402
    DEFAULT_WORKSPACE_SETTINGS,
    normalize_workspace,
    replace_member_email,
    upsert_member,
)


def _workspace():
    return {
        "organizations": [
            {"id": "org_a", "name": "Org A"},
            {"id": "org_b", "name": "Org B"},
        ],
        "active_organization_id": "org_a",
        "active_project_id": "project_a",
        "projects": [
            {"id": "project_a", "name": "Project A", "description": "", "organization_id": "org_a"},
            {"id": "project_b", "name": "Project B", "description": "", "organization_id": "org_b"},
        ],
        "members": [],
    }


def _client(
    monkeypatch,
    workspace,
    *,
    email="user@example.com",
    role="user",
    auth_type="bearer",
    project_id=None,
    db=None,
):
    state = {"workspace": normalize_workspace(workspace)}

    def get_json(_db, _key, _default):
        return deepcopy(state["workspace"])

    def set_json(_db, _key, value):
        state["workspace"] = normalize_workspace(value)
        return deepcopy(state["workspace"])

    def verify_auth(request: Request):
        request.state.auth_type = auth_type
        if project_id:
            request.state.project_id = project_id
        if auth_type == "admin_api_key":
            return None
        return User(id=uuid.uuid4(), name="User", email=email, password_hash="x", role=role)

    monkeypatch.setattr(settings_router, "get_json", get_json)
    monkeypatch.setattr(settings_router, "set_json", set_json)

    app = FastAPI()

    @app.middleware("http")
    async def capture_request_state(request: Request, call_next):
        response = await call_next(request)
        state["suppress_request_log"] = getattr(request.state, "suppress_request_log", False)
        return response

    app.include_router(settings_router.router)
    app.include_router(settings_router.cloud_router)
    app.dependency_overrides[settings_router.verify_auth] = verify_auth
    test_db = db if db is not None else object()
    app.dependency_overrides[get_db] = lambda: test_db
    return TestClient(app, raise_server_exceptions=False), state


def test_reader_can_read_own_project_but_cannot_write_or_invite(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="reader@example.com",
        role="READER",
        status="active",
        project_id="project_a",
    )
    client, _state = _client(monkeypatch, workspace, email="reader@example.com")

    read = client.get("/api/v1/orgs/organizations/org_a/projects/project_a/")
    write = client.patch("/api/v1/orgs/organizations/org_a/projects/project_a/", json={"name": "Nope"})
    invite = client.post(
        "/settings/workspace/members",
        json={"email": "new@example.com", "role": "READER", "project_id": "project_a"},
    )

    assert read.status_code == 200
    assert write.status_code == 403
    assert invite.status_code == 403


def test_editor_can_read_project_but_cannot_manage_settings_or_members(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="editor@example.com",
        role="EDITOR",
        status="active",
        project_id="project_a",
    )
    client, _state = _client(monkeypatch, workspace, email="editor@example.com")

    read = client.get("/api/v1/orgs/organizations/org_a/projects/project_a/")
    write = client.patch(
        "/api/v1/orgs/organizations/org_a/projects/project_a/",
        json={"name": "Nope"},
    )
    invite = client.post(
        "/api/v1/orgs/organizations/org_a/projects/project_a/members/",
        json={"email": "new@example.com", "role": "READER"},
    )

    assert read.status_code == 200
    assert write.status_code == 403
    assert invite.status_code == 403


def test_project_owner_can_update_only_their_project(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, email="owner@example.com")

    own = client.patch(
        "/api/v1/orgs/organizations/org_a/projects/project_a/",
        json={"name": "Renamed A", "description": "owned"},
    )
    other = client.patch("/api/v1/orgs/organizations/org_b/projects/project_b/", json={"name": "Nope"})
    other_org = client.get("/api/v1/orgs/organizations/org_b/")

    assert own.status_code == 200
    assert own.json()["name"] == "Renamed A"
    assert other.status_code == 403
    assert other_org.status_code == 403
    assert (
        next(project for project in state["workspace"]["projects"] if project["id"] == "project_a")["name"]
        == "Renamed A"
    )
    assert (
        next(project for project in state["workspace"]["projects"] if project["id"] == "project_b")["name"]
        == "Project B"
    )


def test_org_owner_can_update_org_and_create_projects_in_that_org_only(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="org-owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_a",
    )
    client, state = _client(monkeypatch, workspace, email="org-owner@example.com")

    renamed = client.patch("/api/v1/orgs/organizations/org_a/", json={"name": "Renamed Org"})
    created = client.post("/api/v1/orgs/organizations/org_a/projects/", json={"name": "New Project"})
    forbidden = client.post("/api/v1/orgs/organizations/org_b/projects/", json={"name": "Nope"})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed Org"
    assert created.status_code == 200
    assert created.json()["organization_id"] == "org_a"
    assert forbidden.status_code == 403
    assert any(project["name"] == "New Project" for project in state["workspace"]["projects"])


def test_org_owner_can_delete_owned_org_and_scoped_projects(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="org-owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_b",
    )
    workspace = upsert_member(
        workspace,
        email="reader@example.com",
        role="READER",
        status="active",
        project_id="project_b",
    )
    client, state = _client(monkeypatch, workspace, email="org-owner@example.com")

    deleted = client.delete("/api/v1/orgs/organizations/org_b/")

    assert deleted.status_code == 200
    assert all(org["id"] != "org_b" for org in state["workspace"]["organizations"])
    assert all(project["organization_id"] != "org_b" for project in state["workspace"]["projects"])
    assert all(member["organization_id"] != "org_b" for member in state["workspace"]["members"])
    assert state["suppress_request_log"] is True


def test_project_resource_purge_cleans_regular_and_playground_history(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.execute = MagicMock()

    db = FakeSession()
    memory = MagicMock()
    memory.vector_store.list.side_effect = [
        ([SimpleNamespace(id="regular-memory")], 1),
        ([SimpleNamespace(id="playground-memory")], 1),
    ]
    monkeypatch.setattr(settings_router, "Session", FakeSession)
    monkeypatch.setattr(settings_router, "get_memory_instance", lambda: memory)
    monkeypatch.setattr(settings_router, "delete_graph_memories", MagicMock())
    monkeypatch.setattr(settings_router, "get_json", lambda *_args: [])
    monkeypatch.setattr(settings_router, "set_json", MagicMock())
    settings_router._purge_project_resources(db, "project-a")

    memory.vector_store.list.assert_has_calls(
        [
            call(filters={"project_id": "project-a"}, top_k=1_000_000),
            call(filters={"project_id": "project-a.__playground__"}, top_k=1_000_000),
        ]
    )
    assert memory.delete_all.call_args_list == [
        call(project_id="project-a"),
        call(project_id="project-a.__playground__"),
    ]
    assert memory.db.delete_project_data.call_args_list == [
        call("project-a", ["regular-memory"]),
        call("project-a.__playground__", ["playground-memory"]),
    ]


def test_project_owner_can_delete_only_owned_project(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, email="owner@example.com")

    other = client.delete("/api/v1/orgs/organizations/org_b/projects/project_b/")
    own = client.delete("/api/v1/orgs/organizations/org_a/projects/project_a/")

    assert other.status_code == 403
    assert own.status_code == 200
    assert all(project["id"] != "project_a" for project in state["workspace"]["projects"])
    assert any(project["id"] == "project_b" for project in state["workspace"]["projects"])
    assert state["suppress_request_log"] is True


def test_org_owner_can_invite_org_member_but_not_other_org(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="org-owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_a",
    )
    client, state = _client(monkeypatch, workspace, email="org-owner@example.com")

    invited = client.post(
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "reader@example.com", "role": "READER"},
    )
    forbidden = client.post(
        "/api/v1/orgs/organizations/org_b/members/",
        json={"email": "reader@example.com", "role": "READER"},
    )

    assert invited.status_code == 200
    assert forbidden.status_code == 403
    member = next(member for member in state["workspace"]["members"] if member["email"] == "reader@example.com")
    assert member["organization_id"] == "org_a"
    assert member["project_id"] is None
    assert member["status"] == "invited"


def test_project_owner_can_invite_project_member_but_not_org_member(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, email="owner@example.com")

    invited = client.post(
        "/api/v1/orgs/organizations/org_a/projects/project_a/members/",
        json={"email": "reader@example.com", "role": "READER"},
    )
    forbidden = client.post(
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "org-reader@example.com", "role": "READER"},
    )

    assert invited.status_code == 200
    assert forbidden.status_code == 403
    member = next(member for member in state["workspace"]["members"] if member["email"] == "reader@example.com")
    assert member["organization_id"] == "org_a"
    assert member["project_id"] == "project_a"
    assert member["status"] == "invited"


def test_org_owner_can_update_org_member_role(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="org-owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_a",
    )
    workspace = upsert_member(
        workspace,
        email="reader@example.com",
        role="READER",
        status="invited",
        organization_id="org_a",
    )
    client, state = _client(monkeypatch, workspace, email="org-owner@example.com")

    updated = client.put(
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "reader@example.com", "role": "OWNER"},
    )

    assert updated.status_code == 200
    member = next(member for member in state["workspace"]["members"] if member["email"] == "reader@example.com")
    assert member["organization_id"] == "org_a"
    assert member["project_id"] is None
    assert member["role"] == "OWNER"
    assert member["status"] == "invited"


def test_organization_member_operations_do_not_cross_organization_boundaries(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="shared@example.com",
        role="READER",
        status="active",
        organization_id="org_a",
    )
    workspace = upsert_member(
        workspace,
        email="shared@example.com",
        role="OWNER",
        status="active",
        organization_id="org_b",
    )
    client, state = _client(monkeypatch, workspace, role="admin")

    listed = client.get("/api/v1/orgs/organizations/org_a/members/")
    removed = client.request(
        "DELETE",
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "shared@example.com"},
    )

    assert listed.status_code == 200
    assert [(member["email"], member["organization_id"]) for member in listed.json()] == [
        ("shared@example.com", "org_a")
    ]
    assert removed.status_code == 200
    memberships = [member for member in state["workspace"]["members"] if member["email"] == "shared@example.com"]
    assert len(memberships) == 1
    assert memberships[0]["organization_id"] == "org_b"
    assert memberships[0]["role"] == "OWNER"


def test_existing_user_invitation_is_activated_immediately(monkeypatch):
    db = MagicMock()
    db.scalar.return_value = uuid.uuid4()
    client, state = _client(monkeypatch, _workspace(), role="admin", db=db)

    response = client.post(
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "existing@example.com", "role": "READER"},
    )

    assert response.status_code == 200
    member = next(member for member in state["workspace"]["members"] if member["email"] == "existing@example.com")
    assert member["organization_id"] == "org_a"
    assert member["status"] == "active"
    db.scalar.assert_called_once()


def test_last_active_organization_owner_cannot_be_removed(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_a",
    )
    client, state = _client(monkeypatch, workspace, role="admin")
    before = deepcopy(state["workspace"])

    response = client.request(
        "DELETE",
        "/api/v1/orgs/organizations/org_a/members/",
        json={"email": "owner@example.com"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one active organization owner is required."
    assert state["workspace"] == before


def test_last_active_project_owner_cannot_be_removed(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, role="admin")
    before = deepcopy(state["workspace"])

    response = client.delete(
        "/api/v1/orgs/organizations/org_a/projects/project_a/members/",
        params={"email": "owner@example.com"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one active project owner is required."
    assert state["workspace"] == before


def test_project_owner_can_update_project_member_role(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    workspace = upsert_member(
        workspace,
        email="reader@example.com",
        role="READER",
        status="invited",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, email="owner@example.com")

    updated = client.put(
        "/api/v1/orgs/organizations/org_a/projects/project_a/members/",
        json={"email": "reader@example.com", "role": "OWNER"},
    )

    assert updated.status_code == 200
    member = next(member for member in state["workspace"]["members"] if member["email"] == "reader@example.com")
    assert member["organization_id"] == "org_a"
    assert member["project_id"] == "project_a"
    assert member["role"] == "OWNER"
    assert member["status"] == "invited"


def test_new_project_starts_with_isolated_default_settings(monkeypatch):
    workspace = _workspace()
    workspace["extraction"] = {
        "multilingual": False,
        "use_case": "Job search",
        "memory_depth": "Comprehensive Knowledge",
        "include": "career facts",
        "exclude": "temporary data",
        "custom_instructions": "existing project instructions",
    }
    workspace["categories"] = [{"name": "career", "description": "Existing project category"}]
    client, state = _client(monkeypatch, workspace, role="admin")

    created = client.post("/api/v1/orgs/organizations/org_a/projects/", json={"name": "Isolated"})

    assert created.status_code == 200
    project = next(item for item in state["workspace"]["projects"] if item["id"] == created.json()["id"])
    assert project["extraction"] == DEFAULT_WORKSPACE_SETTINGS["extraction"]
    assert project["categories"] == []
    assert project["retention"] == DEFAULT_WORKSPACE_SETTINGS["retention"]
    assert project["playground"] == DEFAULT_WORKSPACE_SETTINGS["playground"]


def test_replace_member_email_preserves_scoped_memberships():
    workspace = upsert_member(
        _workspace(),
        email="old@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    workspace = upsert_member(
        workspace,
        email="old@example.com",
        role="READER",
        status="active",
        organization_id="org_b",
    )

    updated = replace_member_email(workspace, "old@example.com", "new@example.com")

    memberships = [member for member in updated["members"] if member["email"] == "new@example.com"]
    assert len(memberships) == 2
    assert {member["project_id"] for member in memberships} == {"project_a", None}
    assert all(member["email"] != "old@example.com" for member in updated["members"])


def test_project_api_key_cannot_access_settings_control_plane(monkeypatch):
    client, state = _client(monkeypatch, _workspace(), auth_type="api_key", project_id="project_a")
    before = deepcopy(state["workspace"])

    own = client.patch("/api/v1/orgs/organizations/org_a/projects/project_a/", json={"name": "API Key Project"})
    other = client.patch("/api/v1/orgs/organizations/org_b/projects/project_b/", json={"name": "Nope"})
    create_org = client.post("/api/v1/orgs/organizations/", json={"name": "Nope"})

    assert own.status_code == 403
    assert other.status_code == 403
    assert create_org.status_code == 403
    assert state["workspace"] == before


def test_admin_workspace_save_persists_current_project_settings(monkeypatch):
    client, state = _client(monkeypatch, _workspace(), role="admin")

    saved = client.patch(
        "/settings/workspace",
        json={
            "data": {
                "active_project_id": "project_a",
                "extraction": {
                    "multilingual": False,
                    "use_case": "Customer Support",
                    "memory_depth": "Balanced Context",
                    "include": "save customer preferences",
                    "exclude": "temporary coupons",
                    "custom_instructions": "prefer durable facts",
                },
                "categories": [{"name": "Support", "description": "Customer support context"}],
                "retention": {"memory_decay": False, "expiration_date": "2026-12-31"},
                "playground": {"custom_instructions": "answer briefly", "top_k": 3},
            }
        },
    )

    assert saved.status_code == 200
    project = next(project for project in state["workspace"]["projects"] if project["id"] == "project_a")
    assert project["extraction"]["include"] == "save customer preferences"
    assert project["categories"] == [{"name": "Support", "description": "Customer support context"}]
    assert project["retention"]["expiration_date"] == "2026-12-31"
    assert project["playground"]["top_k"] == 3


def test_project_owner_workspace_save_persists_project_settings(monkeypatch):
    workspace = upsert_member(
        _workspace(),
        email="owner@example.com",
        role="OWNER",
        status="active",
        project_id="project_a",
    )
    client, state = _client(monkeypatch, workspace, email="owner@example.com")

    saved = client.patch(
        "/settings/workspace",
        json={
            "data": {
                "active_project_id": "project_a",
                "extraction": {
                    "multilingual": True,
                    "use_case": "Education",
                    "memory_depth": "Comprehensive Knowledge",
                    "include": "learning goals",
                    "exclude": "",
                    "custom_instructions": "store preferences",
                },
                "categories": [{"name": "Learning", "description": ""}],
                "retention": {"memory_decay": True, "expiration_date": None},
                "playground": {"custom_instructions": "tutor mode", "top_k": 5},
            }
        },
    )

    assert saved.status_code == 200
    project = next(project for project in state["workspace"]["projects"] if project["id"] == "project_a")
    assert project["extraction"]["use_case"] == "Education"
    assert project["categories"] == [{"name": "Learning", "description": ""}]
    assert project["playground"]["custom_instructions"] == "tutor mode"


def test_nested_project_patch_persists_all_project_settings(monkeypatch):
    client, state = _client(monkeypatch, _workspace(), role="admin")
    payload = {
        "extraction": {
            "multilingual": False,
            "use_case": "Customer support",
            "memory_depth": "Balanced Context",
            "include": "durable preferences",
            "exclude": "temporary codes",
            "custom_instructions": "Keep account preferences",
        },
        "categories": [
            {"name": "Account", "description": "Account preferences"},
            {"name": "Billing", "description": "Billing context"},
        ],
        "retention": {"memory_decay": False, "expiration_date": "2030-12-31"},
        "playground": {
            "custom_instructions": "Answer briefly",
            "force_add_only": True,
            "reranking": True,
            "temperature": 0.4,
            "threshold": 0.7,
            "top_p": 0.8,
            "top_k": 4,
            "max_tokens": 512,
        },
    }

    response = client.patch(
        "/api/v1/orgs/organizations/org_a/projects/project_a/",
        json=payload,
    )

    assert response.status_code == 200
    project = next(project for project in state["workspace"]["projects"] if project["id"] == "project_a")
    assert project["extraction"] == payload["extraction"]
    assert project["categories"] == payload["categories"]
    assert project["retention"] == payload["retention"]
    assert project["playground"] == payload["playground"]


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        ({"extraction": {"multilingual": "yes"}}, "multilingual must be a boolean"),
        ({"categories": {"name": "Account"}}, "Categories must be an array"),
        ({"retention": {"expiration_date": "31-12-2030"}}, "expiration_date must be an ISO date"),
        ({"playground": {"top_k": 0}}, "top_k must be between 1 and 100"),
    ],
)
def test_nested_project_patch_rejects_invalid_settings_without_persisting(monkeypatch, payload, detail):
    client, state = _client(monkeypatch, _workspace(), role="admin")
    before = deepcopy(state["workspace"])

    response = client.patch(
        "/api/v1/orgs/organizations/org_a/projects/project_a/",
        json=payload,
    )

    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert state["workspace"] == before
