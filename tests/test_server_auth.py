# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Comprehensive E2E tests for REST API server authentication.

Tests the actual server/main.py app through FastAPI's TestClient (full ASGI
round-trip) covering:
  - Auth disabled mode (AUTH_DISABLED=true)
  - Auth enabled mode (ADMIN_API_KEY set)
  - Edge cases: empty keys, near-miss keys, timing-safe comparison, header
    casing, response headers, startup logging, and full CRUD flows through auth.
"""

import importlib
import logging
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

_AUTH_SERVER_READY = False
_ACTIVE_MONKEYPATCH = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_runtime_config_store(monkeypatch):
    """Keep app reloads independent from the external Postgres service."""
    global _ACTIVE_MONKEYPATCH

    import server_state

    monkeypatch.setattr(server_state, "_load_overrides", lambda: {})
    monkeypatch.setattr(server_state, "_save_overrides", lambda _overrides: None)
    _ACTIVE_MONKEYPATCH = monkeypatch
    yield
    _ACTIVE_MONKEYPATCH = None


@pytest.fixture
def _mock_memory():
    """Patch Memory.from_config so the server imports without a real backend."""
    mock_instance = MagicMock()
    # Set up return values so CRUD endpoints return realistic responses
    mock_instance.get.return_value = {"id": "mem-1", "memory": "test memory", "user_id": "alice"}
    mock_instance.get_all.return_value = [
        {"id": "mem-1", "memory": "test memory", "user_id": "alice"},
    ]
    mock_instance.add.return_value = {"results": [{"id": "mem-1", "event": "ADD", "memory": "test"}]}
    mock_instance.search.return_value = [{"id": "mem-1", "memory": "test", "score": 0.9}]
    mock_instance.update.return_value = {"message": "Memory updated"}
    mock_instance.history.return_value = [{"id": "mem-1", "old_memory": "a", "new_memory": "b"}]
    mock_instance.delete.return_value = None
    mock_instance.delete_all.return_value = {"message": "Memories deleted successfully!"}
    mock_instance.reset.return_value = None

    with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}):
        with patch("mem0.Memory.from_config", return_value=mock_instance):
            import server_state

            previous_instance = server_state._memory_instance
            server_state._memory_instance = mock_instance
            try:
                yield mock_instance
            finally:
                server_state._memory_instance = previous_instance


def _load_app(
    env_overrides: dict | None = None,
    *,
    unset: tuple[str, ...] = (),
    reload_app: bool = False,
):
    """Reload server/main.py with the given environment and return the FastAPI app."""
    global _AUTH_SERVER_READY

    assert _ACTIVE_MONKEYPATCH is not None

    env = {
        "ADMIN_API_KEY": "",
        "AUTH_DISABLED": "false",
        "JWT_SECRET": "test-jwt-secret-for-auth-tests",
        **(env_overrides or {}),
    }
    with patch.dict(os.environ, env, clear=False):
        for name in unset:
            os.environ.pop(name, None)

        import auth as server_auth

        # server.main imports these values directly from the top-level auth
        # module, so both modules must be refreshed when changing auth mode.
        importlib.reload(server_auth)

        if not _AUTH_SERVER_READY and "server.main" not in sys.modules:
            import server.main as server_main
        else:
            import server.main as server_main

            if reload_app or not _AUTH_SERVER_READY:
                importlib.reload(server_main)
        _AUTH_SERVER_READY = True

    import db as server_db

    fake_db = MagicMock()
    fake_db.execute.return_value.scalars.return_value.all.return_value = []
    fake_db.scalar.return_value = None
    fake_db.get.return_value = None

    def override_get_db():
        yield fake_db

    _ACTIVE_MONKEYPATCH.setitem(server_main.app.dependency_overrides, server_db.get_db, override_get_db)
    _ACTIVE_MONKEYPATCH.setattr(
        server_main,
        "_workspace_settings",
        MagicMock(return_value=server_main.DEFAULT_WORKSPACE_SETTINGS),
    )
    for name in (
        "_persist_request_log",
        "_ensure_memory_project",
        "_enforce_memory_storage_quota",
        "upsert_graph_memory",
        "delete_graph_memory",
        "delete_graph_memories",
        "queue_webhook_event",
    ):
        _ACTIVE_MONKEYPATCH.setattr(server_main, name, MagicMock())
    _ACTIVE_MONKEYPATCH.setattr(server_main, "graph_related_memories", MagicMock(return_value=[]))
    _ACTIVE_MONKEYPATCH.setattr(server_main.memories_router, "delete_memory_feedback", MagicMock())
    return server_main.app


# ---------------------------------------------------------------------------
# Auth disabled (explicit local-development opt-out)
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    """All endpoints should be freely accessible when AUTH_DISABLED is true."""

    @pytest.fixture(autouse=True)
    def _setup(self, _mock_memory):
        self.app = _load_app({"AUTH_DISABLED": "true"})
        self.client = TestClient(self.app)
        self.mock = _mock_memory

    def test_root_redirects_to_docs(self):
        resp = self.client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert "/docs" in resp.headers["location"]

    def test_get_memory_without_key(self):
        resp = self.client.get("/memories/mem-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "mem-1"

    def test_get_all_memories_without_key(self):
        resp = self.client.get("/memories", params={"user_id": "alice"})
        assert resp.status_code == 200

    def test_create_memory_without_key(self):
        resp = self.client.post(
            "/memories",
            json={
                "messages": [{"role": "user", "content": "I like pizza"}],
                "user_id": "alice",
            },
        )
        assert resp.status_code == 200

    def test_search_without_key(self):
        resp = self.client.post("/search", json={"query": "pizza", "user_id": "alice"})
        assert resp.status_code == 200

    def test_memory_operation_reports_provider_setup_required(self, monkeypatch):
        import server_state

        monkeypatch.setattr(server_state, "_memory_instance", None)
        monkeypatch.setattr(server_state, "_waiting_for_provider_credentials", True)

        resp = self.client.post("/search", json={"query": "pizza", "user_id": "alice"})

        assert resp.status_code == 503
        assert resp.json() == {
            "detail": "Model provider credentials are not configured. Complete provider setup before using memory operations."
        }

    def test_update_memory_without_key(self):
        resp = self.client.put("/memories/mem-1", json={"text": "updated"})
        assert resp.status_code == 200

    def test_history_without_key(self):
        resp = self.client.get("/memories/mem-1/history")
        assert resp.status_code == 200

    def test_delete_memory_without_key(self):
        resp = self.client.delete("/memories/mem-1")
        assert resp.status_code == 200

    def test_delete_all_without_key(self):
        resp = self.client.delete("/memories", params={"user_id": "alice"})
        assert resp.status_code == 200

    def test_reset_without_key(self):
        resp = self.client.post("/reset")
        assert resp.status_code == 200

    def test_configure_without_key(self):
        self.mock.from_config = MagicMock()
        resp = self.client.post("/configure", json={"version": "v1.1"})
        assert resp.status_code == 200

    def test_supplying_key_still_works_when_auth_disabled(self):
        """A client that sends X-API-Key should not be penalized when auth is off."""
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": "some-random-key"})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/configure"),
            ("POST", "/memories"),
            ("GET", "/memories"),
            ("GET", "/memories/test-id"),
            ("POST", "/search"),
            ("PUT", "/memories/test-id"),
            ("GET", "/memories/test-id/history"),
            ("DELETE", "/memories/test-id"),
            ("DELETE", "/memories"),
            ("POST", "/reset"),
        ],
    )
    def test_no_endpoint_returns_401_when_auth_disabled(self, method, path):
        resp = self.client.request(method, path)
        assert resp.status_code != 401, f"{method} {path} should not require auth"


# ---------------------------------------------------------------------------
# Auth enabled (ADMIN_API_KEY set)
# ---------------------------------------------------------------------------


class TestAuthEnabled:
    """All protected endpoints must enforce the API key."""

    API_KEY = "test-secret-key-12345"

    @pytest.fixture(autouse=True)
    def _setup(self, _mock_memory):
        self.app = _load_app({"ADMIN_API_KEY": self.API_KEY})
        self.client = TestClient(self.app)
        self.mock = _mock_memory

    # --- Rejection cases ---

    def test_missing_key_returns_401(self):
        resp = self.client.get("/memories/mem-1")
        assert resp.status_code == 401

    def test_missing_key_detail_mentions_header(self):
        resp = self.client.get("/memories/mem-1")
        assert "X-API-Key" in resp.json()["detail"]

    def test_wrong_key_returns_401(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_wrong_key_detail_says_invalid(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": "wrong"})
        assert "Invalid" in resp.json()["detail"]

    def test_empty_string_key_returns_401(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": ""})
        assert resp.status_code == 401

    def test_401_includes_www_authenticate_header(self):
        resp = self.client.get("/memories/mem-1")
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_near_miss_key_rejected(self):
        """Key that differs by one character should be rejected."""
        near_miss = self.API_KEY[:-1] + ("6" if self.API_KEY[-1] != "6" else "7")
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": near_miss})
        assert resp.status_code == 401

    def test_key_with_extra_whitespace_rejected(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": f" {self.API_KEY} "})
        assert resp.status_code == 401

    def test_key_prefix_rejected(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": self.API_KEY[:5]})
        assert resp.status_code == 401

    def test_key_with_different_case_rejected(self):
        resp = self.client.get("/memories/mem-1", headers={"X-API-Key": self.API_KEY.upper()})
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/configure"),
            ("POST", "/memories"),
            ("GET", "/memories"),
            ("GET", "/memories/test-id"),
            ("POST", "/search"),
            ("PUT", "/memories/test-id"),
            ("GET", "/memories/test-id/history"),
            ("DELETE", "/memories/test-id"),
            ("DELETE", "/memories"),
            ("POST", "/reset"),
        ],
    )
    def test_all_endpoints_reject_without_key(self, method, path):
        resp = self.client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} should require auth"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/configure"),
            ("POST", "/memories"),
            ("GET", "/memories"),
            ("GET", "/memories/test-id"),
            ("POST", "/search"),
            ("PUT", "/memories/test-id"),
            ("GET", "/memories/test-id/history"),
            ("DELETE", "/memories/test-id"),
            ("DELETE", "/memories"),
            ("POST", "/reset"),
        ],
    )
    def test_all_endpoints_reject_wrong_key(self, method, path):
        resp = self.client.request(method, path, headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401, f"{method} {path} should reject wrong key"

    # --- Acceptance cases ---

    def test_root_does_not_require_key(self):
        resp = self.client.get("/", follow_redirects=False)
        assert resp.status_code == 307

    def _authed(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-API-Key"] = self.API_KEY
        return self.client.request(method, path, headers=headers, **kwargs)

    def test_get_memory_with_key(self):
        resp = self._authed("GET", "/memories/mem-1")
        assert resp.status_code == 200
        assert resp.json()["id"] == "mem-1"

    def test_get_all_memories_with_key(self):
        resp = self._authed("GET", "/memories", params={"user_id": "alice"})
        assert resp.status_code == 200

    def test_create_memory_with_key(self):
        resp = self._authed(
            "POST",
            "/memories",
            json={
                "messages": [{"role": "user", "content": "I like pizza"}],
                "user_id": "alice",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_search_with_key(self):
        resp = self._authed("POST", "/search", json={"query": "pizza", "user_id": "alice"})
        assert resp.status_code == 200

    def test_update_memory_with_key(self):
        resp = self._authed("PUT", "/memories/mem-1", json={"text": "updated"})
        assert resp.status_code == 200

    def test_history_with_key(self):
        resp = self._authed("GET", "/memories/mem-1/history")
        assert resp.status_code == 200

    def test_delete_memory_with_key(self):
        resp = self._authed("DELETE", "/memories/mem-1")
        assert resp.status_code == 200

    def test_delete_all_with_key(self):
        resp = self._authed("DELETE", "/memories", params={"user_id": "alice"})
        assert resp.status_code == 200

    def test_reset_with_key(self):
        resp = self._authed("POST", "/reset")
        assert resp.status_code == 200

    def test_configure_with_key(self):
        resp = self._authed("POST", "/configure", json={"version": "v1.1"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Full CRUD flow through auth
# ---------------------------------------------------------------------------


class TestAuthenticatedCRUDFlow:
    """Verify a complete create → read → search → update → history → delete
    cycle works end-to-end through the auth layer."""

    API_KEY = "flow-test-key-99"

    @pytest.fixture(autouse=True)
    def _setup(self, _mock_memory):
        self.app = _load_app({"ADMIN_API_KEY": self.API_KEY})
        self.client = TestClient(self.app)
        self.mock = _mock_memory

    def _authed(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-API-Key"] = self.API_KEY
        return self.client.request(method, path, headers=headers, **kwargs)

    def test_full_crud_cycle(self):
        # 1. Create
        resp = self._authed(
            "POST",
            "/memories",
            json={
                "messages": [{"role": "user", "content": "I love fresh vegetable pizza"}],
                "user_id": "alice",
            },
        )
        assert resp.status_code == 200
        self.mock.add.assert_called_once()

        # 2. Read single
        resp = self._authed("GET", "/memories/mem-1")
        assert resp.status_code == 200
        self.mock.get.assert_called_once_with("mem-1")

        # 3. Read all
        resp = self._authed("GET", "/memories", params={"user_id": "alice"})
        assert resp.status_code == 200
        self.mock.get_all.assert_called_once_with(
            filters={"user_id": "alice", "project_id": "default-project"},
            show_expired=False,
        )

        # 4. Search
        resp = self._authed("POST", "/search", json={"query": "pizza", "user_id": "alice"})
        assert resp.status_code == 200
        self.mock.search.assert_called_once()

        # 5. Update
        resp = self._authed("PUT", "/memories/mem-1", json={"text": "updated content"})
        assert resp.status_code == 200
        self.mock.update.assert_called_once()

        # 6. History
        resp = self._authed("GET", "/memories/mem-1/history")
        assert resp.status_code == 200
        self.mock.history.assert_called_once_with(memory_id="mem-1")

        # 7. Delete single
        resp = self._authed("DELETE", "/memories/mem-1")
        assert resp.status_code == 200
        self.mock.delete.assert_called_once_with(memory_id="mem-1")

        # 8. Delete all
        resp = self._authed("DELETE", "/memories", params={"user_id": "alice"})
        assert resp.status_code == 200
        self.mock.delete_all.assert_called_once()

    def test_crud_flow_blocked_without_auth(self):
        """Same flow should fail at every step without the key."""
        endpoints = [
            ("POST", "/memories", {"json": {"messages": [{"role": "user", "content": "test"}], "user_id": "alice"}}),
            ("GET", "/memories/mem-1", {}),
            ("GET", "/memories", {"params": {"user_id": "alice"}}),
            ("POST", "/search", {"json": {"query": "pizza", "user_id": "alice"}}),
            ("PUT", "/memories/mem-1", {"json": {"data": "x"}}),
            ("GET", "/memories/mem-1/history", {}),
            ("DELETE", "/memories/mem-1", {}),
            ("DELETE", "/memories", {"params": {"user_id": "alice"}}),
            ("POST", "/reset", {}),
        ]
        for method, path, kwargs in endpoints:
            resp = self.client.request(method, path, **kwargs)
            assert resp.status_code == 401, f"Unauthenticated {method} {path} should be 401"
            # Verify the mock was NOT called (auth blocked before reaching handler)
        self.mock.add.assert_not_called()
        self.mock.get.assert_not_called()
        self.mock.search.assert_not_called()
        self.mock.update.assert_not_called()
        self.mock.history.assert_not_called()
        self.mock.delete.assert_not_called()
        self.mock.delete_all.assert_not_called()
        self.mock.reset.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestAuthEdgeCases:
    """Boundary conditions and unusual inputs."""

    @pytest.fixture(autouse=True)
    def _setup(self, _mock_memory):
        self.mock = _mock_memory

    def test_very_long_api_key(self):
        """Server should handle a very long key without crashing."""
        long_key = "k" * 4096
        app = _load_app({"ADMIN_API_KEY": long_key})
        client = TestClient(app)
        resp = client.get("/memories/mem-1", headers={"X-API-Key": long_key})
        assert resp.status_code == 200

    def test_special_characters_in_api_key(self):
        """Keys with special ASCII characters should work."""
        special_key = "sk-!@#$%^&*()_+-=[]{}|;:',.<>?/~`"
        app = _load_app({"ADMIN_API_KEY": special_key})
        client = TestClient(app)

        resp = client.get("/memories/mem-1", headers={"X-API-Key": special_key})
        assert resp.status_code == 200

        resp = client.get("/memories/mem-1", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401

    def test_key_env_var_not_present_at_all(self):
        """Authentication remains enabled when ADMIN_API_KEY is absent."""
        app = _load_app(unset=("ADMIN_API_KEY",))
        client = TestClient(app)
        resp = client.get("/memories/mem-1")
        assert resp.status_code == 401

    def test_switching_from_enabled_to_disabled(self):
        """Simulates a server restart with auth toggled off."""
        # First: auth enabled
        app1 = _load_app({"ADMIN_API_KEY": "secret"})
        c1 = TestClient(app1)
        assert c1.get("/memories/mem-1").status_code == 401

        # Then: auth disabled
        app2 = _load_app({"AUTH_DISABLED": "true"})
        c2 = TestClient(app2)
        assert c2.get("/memories/mem-1").status_code != 401

    def test_openapi_schema_accessible_without_key(self):
        """The /docs and /openapi.json endpoints should always be reachable."""
        app = _load_app({"ADMIN_API_KEY": "secret"})
        client = TestClient(app)

        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert schema["info"]["version"] == "0.2.0"

        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_openapi_schema_documents_auth(self):
        """The OpenAPI schema should mention authentication."""
        app = _load_app({"ADMIN_API_KEY": "secret"})
        client = TestClient(app)
        schema = client.get("/openapi.json").json()
        assert "Authentication" in schema.get("info", {}).get("description", "")


# ---------------------------------------------------------------------------
# Startup logging
# ---------------------------------------------------------------------------


class TestStartupLogging:
    """Verify the server emits the correct log messages at import time."""

    @pytest.fixture(autouse=True)
    def _setup(self, _mock_memory):
        pass

    def test_warning_when_auth_disabled(self, caplog):
        with caplog.at_level(logging.WARNING):
            _load_app({"AUTH_DISABLED": "true"}, reload_app=True)
        assert any("AUTH_DISABLED is enabled" in r.message for r in caplog.records)

    def test_no_insecure_warning_when_auth_enabled(self, caplog):
        with caplog.at_level(logging.INFO):
            _load_app({"ADMIN_API_KEY": "a-long-enough-secret-key"}, reload_app=True)
        messages = [r.message for r in caplog.records]
        assert not any("AUTH_DISABLED is enabled" in message for message in messages)
        assert not any("shorter than" in message for message in messages)

    def test_warning_when_key_too_short(self, caplog):
        with caplog.at_level(logging.WARNING):
            _load_app({"ADMIN_API_KEY": "short"}, reload_app=True)
        assert any("shorter than" in r.message for r in caplog.records)
