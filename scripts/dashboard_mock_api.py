# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Loopback-only deterministic API for Dashboard browser acceptance."""

from __future__ import annotations

import argparse
import json
import threading
from copy import deepcopy
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_BODY_BYTES = 1024 * 1024
NOW = "2026-07-22T12:00:00Z"

WORKSPACE = {
    "organization": {"id": "org_yiqiao", "name": "YiQiao Lab"},
    "organizations": [
        {"id": "org_yiqiao", "name": "YiQiao Lab"},
        {"id": "org_archive", "name": "Archive"},
    ],
    "active_organization_id": "org_yiqiao",
    "active_project_id": "default-project",
    "projects": [
        {
            "id": "default-project",
            "name": "Product memory",
            "description": "Shared product research and decisions",
            "organization_id": "org_yiqiao",
            "is_default": True,
        },
        {
            "id": "research-notes",
            "name": "Research notes",
            "description": "Isolated connector research",
            "organization_id": "org_yiqiao",
            "is_default": False,
        },
        {
            "id": "archive-review",
            "name": "Archive review",
            "description": "Long-term archived memory review",
            "organization_id": "org_archive",
            "is_default": True,
        },
    ],
    "members": [
        {
            "email": "admin@yiqiao.local",
            "role": "OWNER",
            "status": "active",
            "project_id": "default-project",
            "organization_id": "org_yiqiao",
        }
    ],
    "extraction": {
        "multilingual": True,
        "use_case": "Research",
        "memory_depth": "Essential Insights",
        "include": "Decisions, preferences, and durable context",
        "exclude": "Credentials and transient status",
        "custom_instructions": "Keep durable facts concise and attributable.",
    },
    "categories": [
        {"name": "Decision", "description": "Confirmed product decisions"},
        {"name": "Research", "description": "Evidence and open questions"},
        {"name": "Preference", "description": "Stable user preferences"},
    ],
    "retention": {"memory_decay": True, "expiration_date": None},
    "playground": {
        "custom_instructions": "Answer from project memories only.",
        "categories": [],
        "includes_prompt": "",
        "excludes_prompt": "",
        "force_add_only": False,
        "reranking": True,
        "temperature": 0.1,
        "threshold": 0.2,
        "max_tokens": 2048,
        "top_k": 10,
        "top_p": 1,
    },
}

MEMORIES = [
    {
        "id": "mem-001",
        "memory": "The connector issuer must be explicit HTTPS outside loopback development.",
        "project_id": "default-project",
        "user_id": "alice",
        "categories": ["Decision"],
        "metadata": {"source": "architecture review"},
        "created_at": "2026-07-16T09:30:00Z",
        "updated_at": "2026-07-21T08:10:00Z",
    },
    {
        "id": "mem-002",
        "memory": "Refresh tokens rotate on every successful use and replay revokes the family.",
        "project_id": "default-project",
        "agent_id": "connector-reviewer",
        "categories": ["Decision", "Research"],
        "metadata": {"source": "protocol 1.0"},
        "created_at": "2026-07-17T11:20:00Z",
        "updated_at": "2026-07-21T10:45:00Z",
    },
    {
        "id": "mem-003",
        "memory": "Project identifiers are bound during approval and cannot be changed by resource requests.",
        "project_id": "default-project",
        "app_id": "yiqiao-dashboard",
        "categories": ["Decision"],
        "metadata": {"source": "security test"},
        "created_at": "2026-07-18T13:05:00Z",
        "updated_at": "2026-07-22T07:40:00Z",
    },
    {
        "id": "mem-004",
        "memory": "The workspace should remain quiet, dense, and useful on narrow screens.",
        "project_id": "default-project",
        "user_id": "design-lead",
        "categories": ["Preference"],
        "metadata": {"source": "design review"},
        "created_at": "2026-07-19T15:15:00Z",
        "updated_at": "2026-07-22T09:00:00Z",
    },
    {
        "id": "mem-005",
        "memory": "Audit records contain hashes and display-safe metadata, never plaintext credentials.",
        "project_id": "default-project",
        "run_id": "acceptance-2026-07",
        "categories": ["Research"],
        "metadata": {"source": "threat model"},
        "created_at": "2026-07-20T10:00:00Z",
        "updated_at": "2026-07-22T09:35:00Z",
    },
]

ENTITIES = [
    {"id": "alice", "type": "user", "total_memories": 8, "created_at": "2026-07-16T09:00:00Z", "updated_at": NOW},
    {"id": "design-lead", "type": "user", "total_memories": 5, "created_at": "2026-07-17T09:00:00Z", "updated_at": NOW},
    {
        "id": "connector-reviewer",
        "type": "agent",
        "total_memories": 12,
        "created_at": "2026-07-18T09:00:00Z",
        "updated_at": NOW,
    },
    {
        "id": "yiqiao-dashboard",
        "type": "app",
        "total_memories": 6,
        "created_at": "2026-07-19T09:00:00Z",
        "updated_at": NOW,
    },
    {
        "id": "acceptance-2026-07",
        "type": "run",
        "total_memories": 4,
        "created_at": "2026-07-20T09:00:00Z",
        "updated_at": NOW,
    },
]

REQUESTS = [
    {
        "id": f"req-{index:03d}",
        "created_at": f"2026-07-22T{11 - index:02d}:2{index}:00Z",
        "method": "POST" if index != 3 else "GET",
        "path": ["/search", "/memories", "/search", "/v1/ping/", "/memories"][index],
        "status_code": 200,
        "latency_ms": [42.1, 87.6, 33.8, 9.4, 65.2][index],
        "auth_type": "oauth" if index < 3 else "bearer",
        "project_id": "default-project",
        "operation": "api_request",
        "event_type": "memory_search" if index in {0, 2} else "memory_write",
        "entities": [{"type": "user", "id": "alice"}],
        "request_payload": None,
        "response_payload": None,
        "result_count": 3 if index in {0, 2} else 1,
        "has_results": True,
        "status": "succeeded",
    }
    for index in range(5)
]


class MockState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.api_keys = [
            {
                "id": "00000000-0000-4000-8000-000000000101",
                "label": "Dashboard acceptance key",
                "key_prefix": "yqk_preview_",
                "project_id": "default-project",
                "created_at": "2026-07-18T08:00:00Z",
                "last_used_at": "2026-07-22T11:48:00Z",
            }
        ]
        self.device = {
            "id": "2db1048e-a24e-4ea8-a25f-1ae04763b694",
            "client_id": "research-assistant",
            "application_name": "Research Assistant",
            "audience": "yiqiao:memory-api",
            "requested_scopes": ["memory:read", "memory:write"],
            "approved_scopes": [],
            "status": "pending",
            "project_id": None,
            "expires_at": "2026-07-22T13:00:00Z",
            "created_at": NOW,
        }
        self.grants = [
            {
                "id": "fd0b4ba1-40f0-459e-9f83-e3fbe755e40f",
                "client_id": "knowledge-canvas",
                "application_name": "Knowledge Canvas",
                "audience": "yiqiao:memory-api",
                "scopes": ["memory:read"],
                "project_id": "default-project",
                "status": "active",
                "access_expires_at": "2026-07-22T12:15:00Z",
                "refresh_expires_at": "2026-08-21T12:00:00Z",
                "last_used_at": "2026-07-22T11:48:00Z",
                "revoked_at": None,
                "revoke_reason": None,
                "created_at": "2026-07-18T08:00:00Z",
                "is_owner": True,
            },
            {
                "id": "c4c2b75d-9d25-4ccc-9ea0-625cdfa6f491",
                "client_id": "research-assistant",
                "application_name": "Research Assistant",
                "audience": "yiqiao:memory-api",
                "scopes": ["memory:read", "memory:write"],
                "project_id": "default-project",
                "status": "revoked",
                "access_expires_at": "2026-07-20T10:15:00Z",
                "refresh_expires_at": "2026-08-19T10:00:00Z",
                "last_used_at": "2026-07-20T09:58:00Z",
                "revoked_at": "2026-07-20T10:02:00Z",
                "revoke_reason": "dashboard_revocation",
                "created_at": "2026-07-19T10:00:00Z",
                "is_owner": True,
            },
        ]
        self.applications = [
            {
                "client_id": "knowledge-canvas",
                "display_name": "Knowledge Canvas",
                "client_type": "public",
                "allowed_audiences": ["yiqiao:memory-api"],
                "allowed_scopes": ["memory:read"],
                "status": "active",
                "operator_metadata": {"website": "https://example.invalid/knowledge-canvas"},
                "created_at": "2026-07-17T08:00:00Z",
                "updated_at": "2026-07-17T08:00:00Z",
                "last_used_at": "2026-07-22T11:48:00Z",
                "revoked_at": None,
            },
            {
                "client_id": "research-assistant",
                "display_name": "Research Assistant",
                "client_type": "public",
                "allowed_audiences": ["yiqiao:memory-api"],
                "allowed_scopes": ["memory:read", "memory:write"],
                "status": "active",
                "operator_metadata": {},
                "created_at": "2026-07-18T08:00:00Z",
                "updated_at": "2026-07-18T08:00:00Z",
                "last_used_at": "2026-07-20T09:58:00Z",
                "revoked_at": None,
            },
        ]


STATE = MockState()


def usage_summary() -> dict:
    start = date(2026, 7, 16)
    series = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "api_requests": 20 + index * 7,
            "memory_writes": 5 + index,
            "memory_searches": 9 + index * 3,
        }
        for index in range(7)
    ]
    return {
        "scope": {
            "type": "project",
            "id": "default-project",
            "project_id": "default-project",
            "organization_id": "org_yiqiao",
        },
        "period": {"days": 7, "start": "2026-07-16", "end": "2026-07-22"},
        "totals": {
            "stored_memories": 36,
            "api_requests": 287,
            "errors": 2,
            "memory_writes": 61,
            "memory_searches": 142,
        },
        "series": series,
        "breakdown": {"api_keys": [], "members": [{"id": "admin", "email": "admin@yiqiao.local", "requests": 287}]},
        "effective_limits": [],
        "can_manage": True,
        "metering": {"model_tokens_available": False, "reason": "Mock acceptance data"},
    }


class DashboardMockHandler(BaseHTTPRequestHandler):
    server_version = "YiQiaoDashboardMock/1.0"

    @property
    def dashboard_origin(self) -> str:
        return self.server.dashboard_origin  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _headers(self, status: int = HTTPStatus.OK, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", self.dashboard_origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Project-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.end_headers()

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", self.dashboard_origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Project-ID")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 0 or length > MAX_BODY_BYTES:
            return {}
        raw = self.rfile.read(length) if length else b""
        try:
            value = json.loads(raw) if raw else {}
        except (UnicodeDecodeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._headers(HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/api/health":
            self._json({"status": "ok", "source": "isolated-dashboard-mock"})
        elif path == "/auth/setup-status":
            self._json({"setup_complete": True, "user_count": 1})
        elif path == "/auth/me":
            self._json(
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "name": "YiQiao Admin",
                    "email": "admin@yiqiao.local",
                    "role": "admin",
                    "created_at": "2026-07-01T08:00:00Z",
                }
            )
        elif path == "/settings/workspace":
            self._json(deepcopy(WORKSPACE))
        elif path == "/usage/summary":
            self._json(usage_summary())
        elif path == "/usage/subjects":
            self._json(
                {
                    "organization": WORKSPACE["organization"],
                    "project": WORKSPACE["projects"][0],
                    "members": WORKSPACE["members"],
                    "api_keys": [],
                    "can_manage_project": True,
                    "can_manage_organization": True,
                    "current_member_email": "admin@yiqiao.local",
                }
            )
        elif path == "/usage/policies":
            self._json({"policies": []})
        elif path == "/api-keys":
            with STATE.lock:
                self._json(deepcopy(STATE.api_keys))
        elif path == "/entities":
            self._json(deepcopy(ENTITIES))
        elif path.startswith("/entities/"):
            parts = path.split("/")
            entity_type, entity_id = parts[2], parts[3] if len(parts) > 3 else "unknown"
            match = next((item for item in ENTITIES if item["type"] == entity_type and item["id"] == entity_id), None)
            self._json(
                {
                    **(
                        match
                        or {
                            "id": entity_id,
                            "type": entity_type,
                            "total_memories": 0,
                            "created_at": NOW,
                            "updated_at": NOW,
                        }
                    ),
                    "total_requests": 14,
                }
            )
        elif path == "/memories":
            self._json({"results": deepcopy(MEMORIES), "total": len(MEMORIES)})
        elif path.startswith("/memories/") and path.endswith("/details"):
            memory_id = path.split("/")[2]
            memory = next((item for item in MEMORIES if item["id"] == memory_id), MEMORIES[0])
            self._json(
                {
                    "memory": deepcopy(memory),
                    "source": [
                        {"role": "user", "content": "Capture the durable connector decision."},
                        {"role": "assistant", "content": memory["memory"]},
                    ],
                    "history": [
                        {
                            "id": "history-1",
                            "event": "ADD",
                            "new_memory": memory["memory"],
                            "created_at": memory["created_at"],
                        }
                    ],
                    "feedback": None,
                }
            )
        elif path == "/requests":
            self._json(
                {
                    "items": deepcopy(REQUESTS),
                    "total": len(REQUESTS),
                    "page": 1,
                    "page_size": 20,
                    "series": [{"bucket": "2026-07-22T11:00:00Z", "count": 5}],
                }
            )
        elif path == "/graph":
            self._json(
                {
                    "configured": True,
                    "nodes": [
                        {"id": "mem-001", "label": "Issuer policy", "kind": "memory"},
                        {"id": "mem-002", "label": "Refresh rotation", "kind": "memory"},
                        {"id": "alice", "label": "alice", "kind": "user"},
                    ],
                    "edges": [
                        {"source": "mem-001", "target": "alice", "type": "MENTIONS", "weight": "1"},
                        {"source": "mem-001", "target": "mem-002", "type": "RELATED", "weight": "0.82"},
                    ],
                    "status": {
                        "configured": True,
                        "enabled": True,
                        "reachable": True,
                        "project_id": "default-project",
                        "memories": 36,
                        "entities": 5,
                        "relationships": 18,
                        "last_error": None,
                    },
                }
            )
        elif path == "/graph/entities":
            self._json(
                {
                    "results": [
                        {"name": "OAuth", "norm": "oauth", "type": "concept", "memory_count": 12},
                        {"name": "YiQiao", "norm": "yiqiao", "type": "product", "memory_count": 18},
                    ]
                }
            )
        elif path == "/webhooks":
            self._json(
                [
                    {
                        "id": "hook-001",
                        "name": "Index updates",
                        "url": "https://example.invalid/hooks/index",
                        "events": ["memory.created", "memory.updated"],
                        "enabled": True,
                        "created_at": "2026-07-18T08:00:00Z",
                        "last_delivery_status": "success",
                        "last_delivery_at": "2026-07-22T10:30:00Z",
                    }
                ]
            )
        elif path == "/memory-exports":
            item = {
                "id": "export-001",
                "status": "completed",
                "entity": {"user_id": "alice"},
                "started_at": "2026-07-21T09:00:00Z",
                "completed_at": "2026-07-21T09:00:03Z",
                "filters": {"categories": ["Decision"]},
                "result": {"exported_at": "2026-07-21T09:00:03Z", "total": 3, "memories": MEMORIES[:3]},
            }
            self._json(
                {
                    "items": [item],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 1,
                    "has_next": False,
                    "has_previous": False,
                }
            )
        elif path == "/configure":
            self._json(
                {
                    "llm": {"provider": "openai", "config": {"model": "mock-model"}},
                    "embedder": {"provider": "openai", "config": {"model": "mock-embedding"}},
                    "vector_store": {"provider": "qdrant", "config": {}},
                }
            )
        elif path == "/configure/providers":
            self._json({"llm": ["openai"], "embedder": ["openai"], "vector_store": ["qdrant"]})
        elif path == "/oauth/grants":
            with STATE.lock:
                audits = [
                    {
                        "id": "audit-001",
                        "event_type": "resource.access",
                        "outcome": "success",
                        "client_id": "knowledge-canvas",
                        "application_name": "Knowledge Canvas",
                        "grant_id": STATE.grants[0]["id"],
                        "project_id": "default-project",
                        "metadata": {},
                        "created_at": "2026-07-22T11:48:00Z",
                    }
                ]
                self._json({"items": deepcopy(STATE.grants), "audit_events": audits, "can_manage_project": True})
        elif path == "/oauth/applications":
            with STATE.lock:
                self._json({"items": deepcopy(STATE.applications), "can_register": True})
        else:
            self._json({"items": [], "results": [], "status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        body = self._body()
        if path in {"/auth/login", "/auth/register", "/auth/refresh"}:
            self._json(
                {
                    "access_token": "dashboard-demo-access",
                    "refresh_token": "dashboard-demo-refresh",
                    "token_type": "bearer",
                }
            )
        elif path == "/memories/query":
            self._json(
                {
                    "results": deepcopy(MEMORIES),
                    "total": len(MEMORIES),
                    "page": 1,
                    "page_size": 20,
                    "total_pages": 1,
                    "facets": {
                        "total": len(MEMORIES),
                        "categories": [
                            {"name": "Decision", "count": 3},
                            {"name": "Research", "count": 2},
                            {"name": "Preference", "count": 1},
                        ],
                    },
                }
            )
        elif path == "/api-keys":
            with STATE.lock:
                key_id = f"00000000-0000-4000-8000-{len(STATE.api_keys) + 102:012d}"
                stored = {
                    "id": key_id,
                    "label": str(body.get("label") or "Dashboard acceptance key"),
                    "key_prefix": "yqk_preview_",
                    "project_id": "default-project",
                    "created_at": NOW,
                    "last_used_at": None,
                }
                STATE.api_keys.append(stored)
                self._json({**deepcopy(stored), "key": "one-time-dashboard-preview-key"}, HTTPStatus.CREATED)
        elif path == "/oauth/device-requests/lookup":
            if body.get("user_code") not in {"YIQO-2026", "yiqo-2026"}:
                self._json({"detail": "Device request not found."}, HTTPStatus.NOT_FOUND)
            else:
                with STATE.lock:
                    self._json(deepcopy(STATE.device))
        elif path.endswith("/approve") and path.startswith("/oauth/device-requests/"):
            with STATE.lock:
                STATE.device["status"] = "approved"
                STATE.device["project_id"] = str(body.get("project_id") or "default-project")
                STATE.device["approved_scopes"] = body.get("approved_scopes") or STATE.device["requested_scopes"]
                self._json(deepcopy(STATE.device))
        elif path.endswith("/reject") and path.startswith("/oauth/device-requests/"):
            with STATE.lock:
                STATE.device["status"] = "denied"
                self._json(deepcopy(STATE.device))
        elif path.endswith("/revoke") and path.startswith("/oauth/grants/"):
            grant_id = path.split("/")[3]
            with STATE.lock:
                grant = next((item for item in STATE.grants if item["id"] == grant_id), None)
                if grant:
                    grant["status"] = "revoked"
                    grant["revoked_at"] = NOW
                    grant["revoke_reason"] = "dashboard_revocation"
                self._json({"id": grant_id, "status": "revoked"})
        elif path == "/oauth/grants/revoke-by-application":
            with STATE.lock:
                revoked = 0
                for grant in STATE.grants:
                    if grant["client_id"] == body.get("client_id") and grant["status"] == "active":
                        grant["status"] = "revoked"
                        grant["revoked_at"] = NOW
                        revoked += 1
                self._json(
                    {"client_id": body.get("client_id"), "project_id": body.get("project_id"), "revoked": revoked}
                )
        elif path == "/oauth/applications":
            application = {
                "client_id": str(body.get("client_id") or "new-public-client"),
                "display_name": str(body.get("display_name") or "New public client"),
                "client_type": "public",
                "allowed_audiences": ["yiqiao:memory-api"],
                "allowed_scopes": body.get("allowed_scopes") or ["memory:read"],
                "status": "active",
                "operator_metadata": body.get("operator_metadata") or {},
                "created_at": NOW,
                "updated_at": NOW,
                "last_used_at": None,
                "revoked_at": None,
            }
            with STATE.lock:
                STATE.applications.append(application)
            self._json(application, HTTPStatus.CREATED)
        elif path.endswith("/revoke") and path.startswith("/oauth/applications/"):
            client_id = path.split("/")[3]
            with STATE.lock:
                application = next((item for item in STATE.applications if item["client_id"] == client_id), None)
                if application:
                    application["status"] = "revoked"
                    application["revoked_at"] = NOW
                self._json(deepcopy(application or {"client_id": client_id, "status": "revoked"}))
        else:
            self._json({"status": "ok", "id": "mock-operation"}, HTTPStatus.CREATED)

    def do_PATCH(self) -> None:  # noqa: N802
        self._body()
        self._json({"status": "ok"})

    def do_PUT(self) -> None:  # noqa: N802
        self._body()
        self._json(deepcopy(WORKSPACE))

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path.rstrip("/") or "/"
        self._body()
        if path.startswith("/api-keys/"):
            key_id = path.split("/")[2]
            with STATE.lock:
                STATE.api_keys = [item for item in STATE.api_keys if item["id"] != key_id]
            self._json({"status": "revoked", "id": key_id})
        else:
            self._json({"status": "revoked"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8102)
    parser.add_argument("--dashboard-origin", default="http://127.0.0.1:3100")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    parsed_origin = urlsplit(args.dashboard_origin)
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc or parsed_origin.path not in {"", "/"}:
        parser.error("--dashboard-origin must be an HTTP(S) origin")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardMockHandler)
    server.dashboard_origin = args.dashboard_origin.rstrip("/")  # type: ignore[attr-defined]
    print(f"YiQiao Dashboard mock API: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
