import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

import webhook_dispatcher  # noqa: E402
from db import get_db  # noqa: E402
from routers import webhooks as webhooks_router  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _WebhookSession:
    def __init__(self):
        self.hooks = {}

    def add(self, hook):
        hook.id = hook.id or uuid.uuid4()
        hook.created_at = hook.created_at or datetime.now(timezone.utc)
        self.hooks[hook.id] = hook

    def execute(self, _statement):
        return _Rows(self.hooks.values())

    def get(self, _model, ident):
        return self.hooks.get(ident)

    def delete(self, hook):
        self.hooks.pop(hook.id, None)

    def commit(self):
        return None

    def refresh(self, _hook):
        return None


@pytest.fixture
def client():
    session = _WebhookSession()
    app = FastAPI()
    app.include_router(webhooks_router.router)
    app.dependency_overrides[webhooks_router.require_project_write] = lambda: None
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


def test_webhook_create_keeps_add_memory_enabled_and_reveals_secret_once(client):
    created = client.post(
        "/webhooks",
        headers={"X-Project-ID": "project-a"},
        json={
            "name": "Memory sync",
            "url": "https://example.test/webhooks",
            "events": ["memory.updated", "memory.updated"],
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["events"] == ["memory.added", "memory.updated"]
    assert created.json()["signing_secret"].startswith("whsec_")

    listed = client.get("/webhooks", headers={"X-Project-ID": "project-a"})
    assert listed.status_code == 200
    assert listed.json()[0]["events"] == ["memory.added", "memory.updated"]
    assert listed.json()[0]["signing_secret"] is None


@pytest.mark.parametrize(
    "payload, detail",
    [
        (
            {"url": "ftp://example.test/hook", "events": ["memory.added"]},
            "Webhook URL must be http or https.",
        ),
        (
            {"url": "https://example.test/hook", "events": ["memory.unknown"]},
            "Unsupported webhook event: memory.unknown",
        ),
    ],
)
def test_webhook_create_rejects_invalid_url_or_event(client, payload, detail):
    response = client.post("/webhooks", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == detail


def test_webhook_delivery_id_is_signed_and_exposed_for_idempotency(monkeypatch):
    hook = SimpleNamespace(
        url="https://example.test/hook",
        signing_secret="whsec_test",
        last_delivery_status=None,
        last_delivery_at=None,
    )
    response = SimpleNamespace(status=202, reason="Accepted")
    response_context = MagicMock()
    response_context.__enter__.return_value = response
    urlopen = MagicMock(return_value=response_context)
    monkeypatch.setattr(webhook_dispatcher.request, "urlopen", urlopen)

    status = webhook_dispatcher.send_webhook(
        hook,
        "memory.added",
        {"results": [{"id": "memory-1"}]},
        "memory-import-stable-key",
    )

    assert status == "202 Accepted"
    sent_request = urlopen.call_args.args[0]
    body = json.loads(sent_request.data)
    headers = {key.lower(): value for key, value in sent_request.header_items()}
    assert body["id"] == "memory-import-stable-key"
    assert headers["x-yiqiao-delivery-id"] == body["id"]
    expected_signature = hmac.new(b"whsec_test", sent_request.data, hashlib.sha256).hexdigest()
    assert headers["x-yiqiao-signature"] == f"sha256={expected_signature}"
