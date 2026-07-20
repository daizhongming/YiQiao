import hashlib
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

_SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

import auth  # noqa: E402
from db import get_db  # noqa: E402
from models import APIKey, Base, BossHelperPairing, User  # noqa: E402
from routers import api_keys as api_keys_router  # noqa: E402
from routers import boss_helper as boss_helper_router  # noqa: E402

PAIRING_PATH = "/integrations/boss-helper/pairing"
PROJECT_ID = "default-project"


class PairingService:
    def __init__(self, client, session_factory, admin_id, access_token):
        self.client = client
        self.session_factory = session_factory
        self.admin_id = admin_id
        self.access_token = access_token

    def dashboard_headers(self, project_id=PROJECT_ID):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Project-ID": project_id,
        }

    def start(self):
        response = self.client.post(f"{PAIRING_PATH}/start")
        assert response.status_code == 201
        return response.json()

    def approve(self, started):
        return self.client.post(
            f"{PAIRING_PATH}/approve",
            headers=self.dashboard_headers(),
            json={"user_code": started["user_code"], "project_id": PROJECT_ID},
        )

    def connect(self):
        started = self.start()
        approved = self.approve(started)
        assert approved.status_code == 200
        exchanged = self.client.post(
            f"{PAIRING_PATH}/token",
            json={"device_code": started["device_code"]},
        )
        assert exchanged.status_code == 200
        return started, exchanged.json()


@pytest.fixture
def pairing_service(tmp_path, monkeypatch):
    database_path = tmp_path / "boss-helper.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        admin = User(
            id=uuid.uuid4(),
            name="Admin",
            email="admin@example.com",
            password_hash="unused",
            role="admin",
        )
        db.add(admin)
        db.commit()
        admin_id = admin.id

    monkeypatch.setattr(auth, "AUTH_DISABLED", False)
    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    monkeypatch.setattr(auth, "JWT_SECRET", "boss-helper-test-jwt-secret-at-least-32-bytes")
    monkeypatch.setenv("BOSS_HELPER_PAIRING_SECRET", "boss-helper-test-pairing-secret")
    monkeypatch.setenv("BOSS_HELPER_PAIRING_TTL_SECONDS", "120")
    monkeypatch.setenv("BOSS_HELPER_KEY_TTL_DAYS", "30")
    auth.invalidate_api_key_auth_cache()

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(boss_helper_router.router)
    app.include_router(api_keys_router.router)

    @app.post("/memories")
    def add_memory(request: Request, _user=Depends(auth.require_project_write)):
        return {"project_id": request.state.project_id}

    @app.get("/memories")
    def list_memories(_user=Depends(auth.require_project_read)):
        return {"status": "ok"}

    @app.post("/search")
    def search_memory(request: Request, _user=Depends(auth.require_project_read)):
        return {"project_id": request.state.project_id}

    @app.get("/v1/ping/")
    def ping(request: Request, _user=Depends(auth.verify_auth)):
        return {"project_id": request.state.project_id}

    @app.post("/v3/memories/add/")
    def platform_add(request: Request, _user=Depends(auth.require_project_write)):
        return {"project_id": request.state.project_id}

    @app.post("/v3/memories/search/")
    def platform_search(request: Request, _user=Depends(auth.require_project_read)):
        return {"project_id": request.state.project_id}

    @app.get("/private")
    def private_route(_user=Depends(auth.require_project_read)):
        return {"status": "ok"}

    app.dependency_overrides[get_db] = override_get_db
    access_token = auth.create_access_token(str(admin_id), "admin")

    with TestClient(app) as client:
        yield PairingService(client, session_factory, admin_id, access_token)

    auth.invalidate_api_key_auth_cache()
    engine.dispose()


def _api_key_headers(key, project_id=None):
    headers = {"X-API-Key": key}
    if project_id is not None:
        headers["X-Project-ID"] = project_id
    return headers


def _credential_code(response):
    return response.json()["detail"]["code"]


def test_pairing_lifecycle_hashes_secrets_and_rejects_replay(pairing_service):
    started = pairing_service.start()
    assert started["device_code"].startswith("yqdc_")
    assert started["expires_in"] == 120
    assert started["verification_uri_complete"].endswith(f"user_code={started['user_code']}")

    with pairing_service.session_factory() as db:
        pairing = db.scalar(select(BossHelperPairing))
        assert pairing.device_code_hash == hashlib.sha256(started["device_code"].encode()).hexdigest()
        assert started["device_code"] not in pairing.device_code_hash
        assert started["user_code"].replace("-", "") not in pairing.user_code_hash
        pairing_id = pairing.id

    pending = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": started["device_code"]},
    )
    assert pending.status_code == 202
    assert pending.json()["code"] == "authorization_pending"

    approved = pairing_service.approve(started)
    assert approved.status_code == 200
    assert approved.json()["pairing_id"] == str(pairing_id)
    assert approved.json()["status"] == "approved"
    assert approved.json()["scopes"] == ["memory:read", "memory:write", "ping"]

    repeated_approval = pairing_service.approve(started)
    assert repeated_approval.status_code == 200
    assert repeated_approval.json()["pairing_id"] == str(pairing_id)

    exchanged = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": started["device_code"]},
    )
    assert exchanged.status_code == 200
    credential = exchanged.json()
    assert credential["status"] == "connected"
    assert credential["api_key"].startswith("yqsk_")
    assert credential["project_id"] == PROJECT_ID
    assert credential["scope"] == ["memory:read", "memory:write", "ping"]

    replay = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": started["device_code"]},
    )
    assert replay.status_code == 409
    assert replay.json()["code"] == "device_code_consumed"

    status = pairing_service.client.get(
        f"{PAIRING_PATH}/status",
        headers=pairing_service.dashboard_headers(),
        params={"user_code": started["user_code"].lower()},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "connected"

    with pairing_service.session_factory() as db:
        pairing = db.get(BossHelperPairing, pairing_id)
        api_key = db.get(APIKey, pairing.api_key_id)
        assert pairing.status == "consumed"
        assert api_key.key_type == auth.BOSS_HELPER_KEY_TYPE
        assert api_key.scopes == ["memory:read", "memory:write", "ping"]
        assert api_key.expires_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=29)


def test_pairing_and_key_expiration_are_enforced(pairing_service):
    pending = pairing_service.start()
    with pairing_service.session_factory() as db:
        pairing = db.scalar(
            select(BossHelperPairing).where(
                BossHelperPairing.device_code_hash == hashlib.sha256(pending["device_code"].encode()).hexdigest()
            )
        )
        pairing.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    expired_exchange = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": pending["device_code"]},
    )
    assert expired_exchange.status_code == 410
    assert expired_exchange.json()["code"] == "pairing_expired"
    expired_approval = pairing_service.approve(pending)
    assert expired_approval.status_code == 410
    assert _credential_code(expired_approval) == "pairing_expired"

    _started, credential = pairing_service.connect()
    headers = _api_key_headers(credential["api_key"])
    assert pairing_service.client.get("/v1/ping/", headers=headers).status_code == 200

    with pairing_service.session_factory() as db:
        api_key = db.scalar(select(APIKey).where(APIKey.key_prefix == credential["api_key"][:12]))
        api_key.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    expired_key = pairing_service.client.get("/v1/ping/", headers=headers)
    assert expired_key.status_code == 401
    assert _credential_code(expired_key) == "auth_expired"


def test_dashboard_pairing_routes_reject_wrong_authentication(pairing_service, monkeypatch):
    started = pairing_service.start()
    body = {"user_code": started["user_code"], "project_id": PROJECT_ID}

    missing = pairing_service.client.post(f"{PAIRING_PATH}/approve", json=body)
    invalid_jwt = pairing_service.client.post(
        f"{PAIRING_PATH}/approve",
        headers={"Authorization": "Bearer invalid", "X-Project-ID": PROJECT_ID},
        json=body,
    )
    assert missing.status_code == 401
    assert invalid_jwt.status_code == 401

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "admin-bootstrap-key")
    admin_key = pairing_service.client.post(
        f"{PAIRING_PATH}/approve",
        headers={"X-API-Key": "admin-bootstrap-key", "X-Project-ID": PROJECT_ID},
        json=body,
    )
    assert admin_key.status_code == 403
    assert _credential_code(admin_key) == "dashboard_login_required"

    monkeypatch.setattr(auth, "ADMIN_API_KEY", "")
    full_key, prefix, key_hash = auth.generate_api_key()
    with pairing_service.session_factory() as db:
        db.add(
            APIKey(
                key_prefix=prefix,
                key_hash=key_hash,
                label="Standard",
                project_id=PROJECT_ID,
                created_by=pairing_service.admin_id,
            )
        )
        db.commit()

    regular_key = pairing_service.client.post(
        f"{PAIRING_PATH}/approve",
        headers=_api_key_headers(full_key, PROJECT_ID),
        json=body,
    )
    assert regular_key.status_code == 403
    assert _credential_code(regular_key) == "dashboard_login_required"

    mismatch = pairing_service.client.post(
        f"{PAIRING_PATH}/approve",
        headers=pairing_service.dashboard_headers("other-project"),
        json=body,
    )
    assert mismatch.status_code == 403
    assert _credential_code(mismatch) == "project_scope_mismatch"


def test_boss_helper_scopes_allow_only_add_search_and_ping(pairing_service):
    _started, credential = pairing_service.connect()
    key = credential["api_key"]
    headers = _api_key_headers(key)

    for method, path in (
        ("POST", "/memories"),
        ("POST", "/search"),
        ("GET", "/v1/ping/"),
        ("POST", "/v3/memories/add/"),
        ("POST", "/v3/memories/search/"),
    ):
        response = pairing_service.client.request(method, path, headers=headers)
        assert response.status_code == 200, (method, path, response.text)
        assert response.json()["project_id"] == PROJECT_ID

    for path in ("/memories", "/private", "/api-keys"):
        denied = pairing_service.client.get(path, headers=headers)
        assert denied.status_code == 403
        assert _credential_code(denied) == "insufficient_scope"

    mismatch = pairing_service.client.post(
        "/search",
        headers=_api_key_headers(key, "other-project"),
    )
    assert mismatch.status_code == 403
    assert _credential_code(mismatch) == "project_scope_mismatch"

    with pairing_service.session_factory() as db:
        api_key = db.scalar(select(APIKey).where(APIKey.key_prefix == key[:12]))
        api_key.scopes = ["memory:read", "ping"]
        db.commit()

    assert pairing_service.client.post("/search", headers=headers).status_code == 200
    write_denied = pairing_service.client.post("/memories", headers=headers)
    assert write_denied.status_code == 403
    assert _credential_code(write_denied) == "insufficient_scope"


def test_cache_rechecks_database_and_revoke_invalidates_entry(pairing_service, monkeypatch):
    started, credential = pairing_service.connect()
    key = credential["api_key"]
    headers = _api_key_headers(key)
    original_verify = auth.verify_api_key_hash
    hash_checks = 0

    def count_hash_checks(plain_key, hashed):
        nonlocal hash_checks
        hash_checks += 1
        return original_verify(plain_key, hashed)

    monkeypatch.setattr(auth, "verify_api_key_hash", count_hash_checks)
    assert pairing_service.client.get("/v1/ping/", headers=headers).status_code == 200
    assert pairing_service.client.get("/v1/ping/", headers=headers).status_code == 200
    assert hash_checks == 1

    with pairing_service.session_factory() as db:
        pairing = db.scalar(
            select(BossHelperPairing).where(
                BossHelperPairing.device_code_hash == hashlib.sha256(started["device_code"].encode()).hexdigest()
            )
        )
        api_key_id = pairing.api_key_id

    assert auth._get_cached_api_key_id(key) == api_key_id
    revoked = pairing_service.client.post(
        f"{PAIRING_PATH}/revoke",
        headers=pairing_service.dashboard_headers(),
        json={"pairing_id": str(pairing.id)},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert auth._get_cached_api_key_id(key) is None

    rejected = pairing_service.client.get("/v1/ping/", headers=headers)
    assert rejected.status_code == 401
    assert _credential_code(rejected) == "key_revoked"


def test_pending_pairing_can_be_revoked_and_invalid_codes_are_safe(pairing_service):
    started = pairing_service.start()
    status = pairing_service.client.get(
        f"{PAIRING_PATH}/status",
        headers=pairing_service.dashboard_headers(),
        params={"user_code": started["user_code"]},
    )
    pairing_id = status.json()["pairing_id"]

    revoked = pairing_service.client.post(
        f"{PAIRING_PATH}/revoke",
        headers=pairing_service.dashboard_headers(),
        json={"pairing_id": pairing_id},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    exchange = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": started["device_code"]},
    )
    assert exchange.status_code == 403
    assert exchange.json()["code"] == "pairing_revoked"

    unknown = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": "yqdc_" + "x" * 40},
    )
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "invalid_device_code"
    malformed = pairing_service.client.post(
        f"{PAIRING_PATH}/token",
        json={"device_code": "short"},
    )
    assert malformed.status_code == 422


def test_concurrent_token_exchange_issues_one_key(pairing_service, monkeypatch):
    started = pairing_service.start()
    assert pairing_service.approve(started).status_code == 200

    original_lookup = boss_helper_router._pairing_by_device_code
    selected = threading.Barrier(2)

    def synchronize_initial_reads(db, device_code, *, lock=False):
        pairing = original_lookup(db, device_code, lock=lock)
        if lock:
            selected.wait(timeout=10)
        return pairing

    monkeypatch.setattr(boss_helper_router, "_pairing_by_device_code", synchronize_initial_reads)

    def exchange():
        return pairing_service.client.post(
            f"{PAIRING_PATH}/token",
            json={"device_code": started["device_code"]},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _index: exchange(), range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json()["code"] == "device_code_consumed"

    with pairing_service.session_factory() as db:
        keys = db.scalars(select(APIKey).where(APIKey.key_type == auth.BOSS_HELPER_KEY_TYPE)).all()
        pairing = db.scalar(select(BossHelperPairing))
        assert len(keys) == 1
        assert pairing.status == "consumed"
        assert pairing.api_key_id == keys[0].id
