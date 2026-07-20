from server.workspace import (
    DEFAULT_PROJECT_ID,
    delete_project,
    member_role,
    normalize_workspace,
    organization_access_role,
    organization_role,
    remove_member,
    role_allows_manage,
    role_allows_read,
    role_allows_write,
    upsert_member,
    visible_workspace,
)


def _workspace():
    return {
        "organizations": [
            {"id": "org_a", "name": "Org A"},
            {"id": "org_b", "name": "Org B"},
        ],
        "active_organization_id": "org_a",
        "active_project_id": DEFAULT_PROJECT_ID,
        "projects": [
            {"id": DEFAULT_PROJECT_ID, "name": "Default", "organization_id": "org_a"},
            {"id": "project_b", "name": "Project B", "organization_id": "org_b"},
        ],
        "members": [],
    }


def test_delete_active_project_prefers_remaining_project_in_same_organization():
    data = _workspace()
    data["projects"].extend(
        [
            {"id": "project_a_1", "name": "Project A1", "organization_id": "org_a"},
            {"id": "project_a_2", "name": "Project A2", "organization_id": "org_a"},
        ]
    )
    data["active_project_id"] = "project_a_2"

    result = delete_project(data, "project_a_2")

    assert result["active_project_id"] == DEFAULT_PROJECT_ID
    assert result["active_organization_id"] == "org_a"
    assert result["organization"] == {"id": "org_a", "name": "Org A"}


def test_delete_last_active_project_in_organization_syncs_fallback_organization():
    data = _workspace()
    data["active_organization_id"] = "org_b"
    data["active_project_id"] = "project_b"

    result = delete_project(data, "project_b")

    assert result["active_project_id"] == DEFAULT_PROJECT_ID
    assert result["active_organization_id"] == "org_a"
    assert result["organization"] == {"id": "org_a", "name": "Org A"}


def test_legacy_roles_normalize_to_cloud_roles():
    data = _workspace()
    data["members"] = [
        {"email": "reader@example.com", "role": "Member", "status": "active", "project_id": DEFAULT_PROJECT_ID},
        {"email": "owner@example.com", "role": "Admin", "status": "active", "organization_id": "org_b"},
    ]

    normalized = normalize_workspace(data)

    assert normalized["members"][0]["role"] == "READER"
    assert normalized["members"][1]["role"] == "OWNER"
    assert role_allows_read("READER")
    assert not role_allows_write("READER")
    assert role_allows_write("OWNER")
    assert role_allows_manage("OWNER")
    assert role_allows_read("EDITOR")
    assert role_allows_write("EDITOR")
    assert not role_allows_manage("EDITOR")


def test_writer_alias_normalizes_to_editor():
    data = _workspace()
    data["members"] = [
        {
            "email": "editor@example.com",
            "role": "Writer",
            "status": "active",
            "project_id": DEFAULT_PROJECT_ID,
        }
    ]

    normalized = normalize_workspace(data)

    assert normalized["members"][0]["role"] == "EDITOR"


def test_project_and_organization_memberships_do_not_bleed_across_orgs():
    data = _workspace()
    data = upsert_member(
        data,
        email="reader@example.com",
        role="READER",
        status="active",
        project_id=DEFAULT_PROJECT_ID,
        organization_id="org_a",
    )
    data = upsert_member(
        data,
        email="owner@example.com",
        role="OWNER",
        status="active",
        organization_id="org_b",
    )

    assert member_role(data, "reader@example.com", DEFAULT_PROJECT_ID) == "READER"
    assert member_role(data, "reader@example.com", "project_b") is None
    assert organization_role(data, "reader@example.com", "org_a") is None
    assert organization_access_role(data, "reader@example.com", "org_a") == "READER"

    assert member_role(data, "owner@example.com", "project_b") == "OWNER"
    assert member_role(data, "owner@example.com", DEFAULT_PROJECT_ID) is None
    assert organization_role(data, "owner@example.com", "org_b") == "OWNER"


def test_remove_member_is_scoped_to_the_requested_org_or_project():
    data = _workspace()
    data = upsert_member(data, email="same@example.com", role="READER", status="active", organization_id="org_a")
    data = upsert_member(data, email="same@example.com", role="OWNER", status="active", organization_id="org_b")

    data = remove_member(data, email="same@example.com", organization_id="org_a")

    assert organization_role(data, "same@example.com", "org_a") is None
    assert organization_role(data, "same@example.com", "org_b") == "OWNER"


def test_visible_workspace_exposes_managed_members_only():
    data = _workspace()
    data = upsert_member(data, email="owner@example.com", role="OWNER", status="active", project_id=DEFAULT_PROJECT_ID)
    data = upsert_member(
        data, email="teammate@example.com", role="READER", status="active", project_id=DEFAULT_PROJECT_ID
    )
    data = upsert_member(data, email="hidden@example.com", role="READER", status="active", project_id="project_b")

    visible = visible_workspace(data, "owner@example.com", is_owner=False)

    emails = {member["email"] for member in visible["members"]}
    assert "owner@example.com" in emails
    assert "teammate@example.com" in emails
    assert "hidden@example.com" not in emails
