# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Regression coverage for the isolated Dashboard acceptance mock."""

import json
import threading
from contextlib import contextmanager
from urllib.request import Request, urlopen

from scripts import dashboard_mock_api


@contextmanager
def _mock_server():
    server = dashboard_mock_api.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_mock_api.DashboardMockHandler)
    server.dashboard_origin = "http://127.0.0.1:3100"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _json_request(url: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.status, json.load(response)


def _first_project_id(organization_id: str) -> str:
    return next(
        project["id"]
        for project in dashboard_mock_api.WORKSPACE["projects"]
        if project["organization_id"] == organization_id
    )


def test_each_mock_organization_can_select_its_own_project():
    organization_ids = [organization["id"] for organization in dashboard_mock_api.WORKSPACE["organizations"]]

    assert all(
        any(project["organization_id"] == organization_id for project in dashboard_mock_api.WORKSPACE["projects"])
        for organization_id in organization_ids
    )
    assert [_first_project_id(organization_id) for organization_id in ("org_yiqiao", "org_archive", "org_yiqiao")] == [
        "default-project",
        "archive-review",
        "default-project",
    ]


def test_api_key_contract_supports_dashboard_list_create_and_revoke():
    with _mock_server() as base_url:
        status, keys = _json_request(f"{base_url}/api-keys")
        assert status == 200
        assert isinstance(keys, list)
        assert keys[0]["key_prefix"] == "yqk_preview_"
        assert keys[0]["scopes"] is None
        assert keys[0]["expires_at"] is None
        assert "key" not in keys[0]

        status, created = _json_request(
            f"{base_url}/api-keys",
            method="POST",
            payload={
                "label": "Browser matrix key",
                "scopes": ["memory:read"],
                "expires_at": "2026-08-22T12:00:00Z",
            },
        )
        assert status == 201
        assert created["label"] == "Browser matrix key"
        assert created["scopes"] == ["memory:read"]
        assert created["expires_at"] == "2026-08-22T12:00:00Z"
        assert created["key"] == "one-time-dashboard-preview-key"

        status, revoked = _json_request(f"{base_url}/api-keys/{created['id']}", method="DELETE")
        assert status == 200
        assert revoked == {"status": "revoked", "id": created["id"]}

        _, remaining = _json_request(f"{base_url}/api-keys")
        assert all(item["id"] != created["id"] for item in remaining)
