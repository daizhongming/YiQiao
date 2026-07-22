#!/usr/bin/env python3
"""Run the deterministic YiQiao release smoke test in an isolated Compose project."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
BASE_COMPOSE = SERVER / "docker-compose.yaml"
BUILD_COMPOSE = SERVER / "docker-compose.build.yaml"
E2E_COMPOSE = SERVER / "docker-compose.e2e.yaml"
ENV_FILE = SERVER / ".env"
REQUIRED_SECRETS = (
    "POSTGRES_PASSWORD",
    "NEO4J_PASSWORD",
    "JWT_SECRET",
    "OAUTH_USER_CODE_HMAC_SECRET",
    "OAUTH_AUDIT_HMAC_SECRET",
)
PROVIDER_CREDENTIAL_ENV_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "LLM_API_KEY",
    "EMBEDDING_API_KEY",
    "RERANK_API_KEY",
    "MEM0_LLM_API_KEY",
    "MEM0_EMBEDDER_API_KEY",
    "MEM0_RERANK_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_API_BASE",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "MEMORY_IMPORT_LLM_PROVIDER",
    "MEMORY_IMPORT_LLM_BASE_URL",
    "MEMORY_IMPORT_LLM_API_KEY_ENV",
    "YIQIAO_E2E_PROVIDER_KEY",
)
RUNTIME_ENV_NAMES = (
    "ADMIN_API_KEY",
    "APP_DB_NAME",
    "DATABASE_URL",
    "HISTORY_DB_PATH",
    "MEM0_DIR",
    "MEMORY_IMPORT_MODEL_TIERING_ENABLED",
    "NEO4J_DATABASE",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "OAUTH_ISSUER",
    "POSTGRES_COLLECTION_NAME",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "YIQIAO_DIR",
    "YIQIAO_DISABLE_SPACY_DOWNLOAD",
)
E2E_PROVIDER_KEY = "local-e2e-only"
E2E_PROVIDER_BASE_URL = "http://model-stub:8080/v1"
E2E_LLM_MODEL = "yiqiao-e2e"
E2E_EMBEDDER_MODEL = "yiqiao-e2e-embedding"
E2E_EMBEDDING_DIMS = 16
EXPECTED_MIGRATION = "018"
PROVIDER_SETUP_DETAIL = (
    "Model provider credentials are not configured. Complete provider setup before using memory operations."
)


class SmokeError(RuntimeError):
    pass


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> Any:
    if payload is not None and form is not None:
        raise ValueError("payload and form are mutually exclusive")
    body = None
    content_type = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("ascii")
        content_type = "application/x-www-form-urlencoded"
    request_headers = {"Accept": "application/json", **(headers or {})}
    if content_type is not None:
        request_headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeError(f"{method} {url} returned HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeError(f"{method} {url} failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{method} {url} returned invalid JSON") from exc


def _request_status(
    method: str,
    url: str,
    *,
    form: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> tuple[int, str]:
    body = None if form is None else urllib.parse.urlencode(form).encode("ascii")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeError(f"{method} {url} failed: {exc}") from exc


def _wait_for_json(url: str, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _request_json("GET", url, timeout=5)
        except Exception as exc:  # noqa: BLE001 - the final error is reported with context
            last_error = exc
            time.sleep(2)
    raise SmokeError(f"Timed out waiting for {url}: {last_error}")


def _wait_for_health(service: str, url: str, timeout: float) -> dict[str, Any]:
    response = _wait_for_json(url, timeout)
    if not isinstance(response, dict) or response.get("status") != "ok":
        raise SmokeError(f"Unexpected {service} health response: {response!r}")
    return response


def _assert_connector_discovery(issuer: str) -> None:
    metadata = _request_json("GET", f"{issuer}/.well-known/oauth-authorization-server")
    capabilities = _request_json("GET", f"{issuer}/.well-known/service-capabilities")
    health = _request_json("GET", f"{issuer}/oauth/health")
    expected_metadata = {
        "issuer": issuer,
        "device_authorization_endpoint": f"{issuer}/oauth/device_authorization",
        "token_endpoint": f"{issuer}/oauth/token",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "grant_types_supported": [
            "urn:ietf:params:oauth:grant-type:device_code",
            "refresh_token",
        ],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["memory:read", "memory:write"],
        "protocol_version": "1.0",
    }
    if metadata != expected_metadata:
        raise SmokeError(f"Unexpected OAuth metadata: {metadata!r}")
    expected_capabilities = {
        "protocol_version": "1.0",
        "service_id": "yiqiao",
        "issuer": issuer,
        "oauth_metadata": f"{issuer}/.well-known/oauth-authorization-server",
        "audiences": ["yiqiao:memory-api"],
        "health_endpoint": f"{issuer}/oauth/health",
        "project_selection": {"required": True, "performed_during_authorization": True},
        "memory_api": {
            "search_endpoint": f"{issuer}/search",
            "write_endpoint": f"{issuer}/memories",
            "ping_endpoint": f"{issuer}/v1/ping/",
            "scopes": {"read": "memory:read", "write": "memory:write"},
        },
    }
    if capabilities != expected_capabilities:
        raise SmokeError(f"Unexpected service capabilities: {capabilities!r}")
    if health != {"status": "ok", "service_id": "yiqiao", "protocol_version": "1.0"}:
        raise SmokeError(f"Unexpected connector health response: {health!r}")
    public_contract = json.dumps([metadata, capabilities], sort_keys=True).casefold()
    if "bosshelper" in public_contract or "boss-helper" in public_contract:
        raise SmokeError("Public connector discovery contains a client-specific field or value")


def _initializer_command(platform: str | None = None) -> list[str]:
    if (platform or sys.platform) == "win32":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "init.ps1"),
            "-EnvFile",
            str(ENV_FILE),
        ]
    return ["sh", str(ROOT / "scripts" / "init.sh")]


def _load_required_secrets(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key not in REQUIRED_SECRETS:
            continue
        if key in values:
            raise SmokeError(f"Initializer produced duplicate {key} entries in {env_file}")

        value = raw_value.strip()
        if value[:1] in {'"', "'"}:
            if len(value) < 2 or value[-1] != value[0]:
                raise SmokeError(f"Initializer produced malformed {key} on line {line_number} of {env_file}")
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if not value.strip():
            raise SmokeError(f"Initializer did not configure {key} in {env_file}")
        values[key] = value

    missing = [key for key in REQUIRED_SECRETS if key not in values]
    if missing:
        raise SmokeError(f"Initializer did not configure {', '.join(missing)} in {env_file}")
    return values


def _memory_texts(response: Any) -> list[str]:
    if isinstance(response, dict):
        values = response.get("results", [])
    elif isinstance(response, list):
        values = response
    else:
        values = []
    return [
        str(item.get("memory") or item.get("text") or item.get("data") or "")
        for item in values
        if isinstance(item, dict)
    ]


def _configure_local_provider(api_url: str, access_token: str) -> None:
    response = _request_json(
        "POST",
        f"{api_url}/configure",
        payload={
            "version": "v1.1",
            "llm": {
                "provider": "openai",
                "config": {
                    "api_key": E2E_PROVIDER_KEY,
                    "openai_base_url": E2E_PROVIDER_BASE_URL,
                    "model": E2E_LLM_MODEL,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "api_key": E2E_PROVIDER_KEY,
                    "openai_base_url": E2E_PROVIDER_BASE_URL,
                    "model": E2E_EMBEDDER_MODEL,
                    "embedding_dims": E2E_EMBEDDING_DIMS,
                },
            },
            "vector_store": {"config": {"embedding_model_dims": E2E_EMBEDDING_DIMS}},
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=120,
    )
    if not isinstance(response, dict) or response.get("message") != "Configuration set successfully":
        raise SmokeError(f"Local provider configuration was not accepted: {response!r}")


def _assert_provider_setup_required(api_url: str, access_token: str) -> None:
    request = urllib.request.Request(
        f"{api_url}/search",
        data=json.dumps({"query": "setup probe", "filters": {"user_id": "release-smoke-user"}}).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raise SmokeError(f"Provider setup probe unexpectedly returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code != 503:
            raise SmokeError(f"Provider setup probe returned HTTP {exc.code}: {detail}") from exc
        try:
            payload = json.loads(detail)
        except json.JSONDecodeError as error:
            raise SmokeError(f"Provider setup probe returned invalid JSON: {detail}") from error
        if payload != {"detail": PROVIDER_SETUP_DETAIL}:
            raise SmokeError(f"Provider setup probe returned an unexpected response: {payload!r}")


class Stack:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.project = args.project_name
        self.env = os.environ.copy()
        for key in (*REQUIRED_SECRETS, *PROVIDER_CREDENTIAL_ENV_NAMES, *RUNTIME_ENV_NAMES):
            self.env.pop(key, None)
        self.env.update(
            {
                "API_BIND_ADDRESS": "127.0.0.1",
                "API_PORT": str(args.api_port),
                "DASHBOARD_BIND_ADDRESS": "127.0.0.1",
                "DASHBOARD_PORT": str(args.dashboard_port),
                "OAUTH_ISSUER": f"http://127.0.0.1:{args.dashboard_port}",
                "YIQIAO_API_IMAGE": f"{self.project}-api:smoke",
                "YIQIAO_DASHBOARD_IMAGE": f"{self.project}-dashboard:smoke",
                "YIQIAO_PULL_POLICY": "build",
            }
        )
        self.created_env = False
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            str(ENV_FILE),
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(BUILD_COMPOSE),
            "-f",
            str(E2E_COMPOSE),
        ]

    def prepare(self) -> None:
        existed = ENV_FILE.exists()
        if existed and not ENV_FILE.is_file():
            raise SmokeError(f"{ENV_FILE} exists but is not a regular file")

        initializer_env = self.env.copy()
        initializer_env["YIQIAO_ENV_FILE"] = str(ENV_FILE)
        result = subprocess.run(
            _initializer_command(),
            cwd=ROOT,
            env=initializer_env,
            check=False,
            text=True,
            capture_output=True,
        )
        self.created_env = not existed and ENV_FILE.is_file()
        if result.returncode:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            raise SmokeError(f"Environment initializer failed ({result.returncode})\n{output}")

        self.env.update(_load_required_secrets(ENV_FILE))

    def run(self, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.compose, *args],
            cwd=SERVER,
            env=self.env,
            check=False,
            text=True,
            capture_output=capture,
        )
        if check and result.returncode:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            raise SmokeError(f"docker compose {' '.join(args)} failed ({result.returncode})\n{output}")
        return result

    def up(self, *, rebuild: bool) -> None:
        args = ["up", "--detach", "--remove-orphans"]
        args.append("--build" if rebuild else "--no-build")
        self.run(*args)

    def diagnostics(self) -> None:
        print("\nCompose status:", file=sys.stderr)
        self.run("ps", check=False)
        print("\nCompose logs:", file=sys.stderr)
        self.run("logs", "--no-color", "--tail", "200", check=False)

    def cleanup(self) -> None:
        self.run("down", "--volumes", "--remove-orphans", check=False)
        if self.created_env:
            ENV_FILE.unlink(missing_ok=True)


def _assert_migration(stack: Stack) -> str:
    result = stack.run(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        "yiqiao_app",
        "-Atc",
        "SELECT version_num FROM alembic_version;",
        capture=True,
    )
    revision = result.stdout.strip()
    if revision != EXPECTED_MIGRATION:
        raise SmokeError(f"Expected Alembic migration {EXPECTED_MIGRATION}, found {revision or '<empty>'}")
    return revision


def _exercise_api(api_url: str, marker: str) -> tuple[str, str, str]:
    tokens = _request_json(
        "POST",
        f"{api_url}/auth/register",
        payload={
            "name": "Release Smoke",
            "email": "release-smoke@example.com",
            "password": secrets.token_urlsafe(24),
        },
    )
    access_token = str(tokens.get("access_token") or "") if isinstance(tokens, dict) else ""
    if not access_token:
        raise SmokeError("Admin registration did not return an access token")

    _assert_provider_setup_required(api_url, access_token)
    _configure_local_provider(api_url, access_token)

    project_headers = {"Authorization": f"Bearer {access_token}", "X-Project-ID": "default-project"}
    created_key = _request_json(
        "POST",
        f"{api_url}/api-keys",
        payload={"label": "Release smoke", "project_id": "default-project"},
        headers=project_headers,
    )
    api_key = str(created_key.get("key") or "") if isinstance(created_key, dict) else ""
    if not api_key:
        raise SmokeError("API key creation did not return the one-time key")

    key_headers = {"X-API-Key": api_key, "X-Project-ID": "default-project"}
    added = _request_json(
        "POST",
        f"{api_url}/memories",
        payload={
            "messages": [{"role": "user", "content": marker}],
            "user_id": "release-smoke-user",
            "infer": False,
        },
        headers=key_headers,
        timeout=120,
    )
    if marker not in _memory_texts(added):
        raise SmokeError(f"Memory add response did not contain the marker: {added!r}")

    searched = _request_json(
        "POST",
        f"{api_url}/search",
        payload={"query": "release persistence preference", "filters": {"user_id": "release-smoke-user"}},
        headers=key_headers,
        timeout=120,
    )
    if marker not in _memory_texts(searched):
        raise SmokeError(f"Memory search did not return the added marker: {searched!r}")
    return access_token, api_key, marker


def _exercise_connector(
    api_url: str,
    issuer: str,
    admin_access_token: str,
) -> tuple[str, str, str, str]:
    client_id = f"release-smoke-{secrets.token_hex(6)}"
    dashboard_headers = {
        "Authorization": f"Bearer {admin_access_token}",
        "X-Project-ID": "default-project",
    }
    application = _request_json(
        "POST",
        f"{api_url}/oauth/applications",
        payload={
            "client_id": client_id,
            "display_name": "Release Smoke Connector",
            "client_type": "public",
            "allowed_audiences": ["yiqiao:memory-api"],
            "allowed_scopes": ["memory:read", "memory:write"],
            "operator_metadata": {"purpose": "isolated-release-smoke"},
        },
        headers=dashboard_headers,
    )
    if application.get("client_id") != client_id or application.get("status") != "active":
        raise SmokeError(f"Public client registration was not accepted: {application!r}")

    code_verifier = secrets.token_urlsafe(48)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
    code_challenge = code_challenge.rstrip(b"=").decode("ascii")
    device = _request_json(
        "POST",
        f"{issuer}/oauth/device_authorization",
        form={
            "client_id": client_id,
            "scope": "memory:read memory:write",
            "audience": "yiqiao:memory-api",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        },
    )
    device_code = str(device.get("device_code") or "")
    user_code = str(device.get("user_code") or "")
    if not device_code.startswith("yqod_") or not user_code:
        raise SmokeError(f"Device authorization did not return opaque codes: {device!r}")
    if device.get("verification_uri") != f"{issuer}/dashboard/connected-apps":
        raise SmokeError(f"Device authorization returned an unexpected verification URI: {device!r}")

    request = _request_json(
        "POST",
        f"{api_url}/oauth/device-requests/lookup",
        payload={"user_code": user_code},
        headers=dashboard_headers,
    )
    request_id = str(request.get("id") or "")
    if not request_id or request.get("client_id") != client_id or request.get("status") != "pending":
        raise SmokeError(f"Dashboard device-code lookup returned an unexpected response: {request!r}")
    approved = _request_json(
        "POST",
        f"{api_url}/oauth/device-requests/{request_id}/approve",
        payload={
            "project_id": "default-project",
            "approved_scopes": ["memory:read", "memory:write"],
        },
        headers=dashboard_headers,
    )
    if approved.get("status") != "approved" or approved.get("project_id") != "default-project":
        raise SmokeError(f"Device authorization approval failed: {approved!r}")

    token = _request_json(
        "POST",
        f"{issuer}/oauth/token",
        form={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
    )
    access_token = str(token.get("access_token") or "")
    refresh_token = str(token.get("refresh_token") or "")
    if (
        not access_token.startswith("yqoa_")
        or not refresh_token.startswith("yqor_")
        or token.get("audience") != "yiqiao:memory-api"
        or token.get("scope") != "memory:read memory:write"
        or token.get("project") != "default-project"
    ):
        raise SmokeError(f"Device-code exchange returned an unexpected token response: {token!r}")

    connector_marker = f"YiQiao connector marker {secrets.token_hex(8)} persists across restarts."
    bearer_headers = {"Authorization": f"Bearer {access_token}"}
    added = _request_json(
        "POST",
        f"{issuer}/memories",
        payload={
            "messages": [{"role": "user", "content": connector_marker}],
            "user_id": "release-connector-user",
            "infer": False,
        },
        headers=bearer_headers,
        timeout=120,
    )
    if connector_marker not in _memory_texts(added):
        raise SmokeError(f"OAuth memory add response did not contain the marker: {added!r}")
    searched = _request_json(
        "POST",
        f"{issuer}/search",
        payload={
            "query": "connector persistence marker",
            "filters": {"user_id": "release-connector-user"},
        },
        headers=bearer_headers,
        timeout=120,
    )
    if connector_marker not in _memory_texts(searched):
        raise SmokeError(f"OAuth memory search did not return the added marker: {searched!r}")
    ping = _request_json("GET", f"{issuer}/v1/ping/", headers=bearer_headers)
    if not isinstance(ping, dict):
        raise SmokeError(f"OAuth ping returned an unexpected response: {ping!r}")

    unknown_status, unknown_body = _request_status(
        "POST",
        f"{issuer}/oauth/revoke",
        form={
            "token": f"yqor_{secrets.token_urlsafe(32)}",
            "token_type_hint": "refresh_token",
            "client_id": client_id,
        },
    )
    if unknown_status != 200 or unknown_body:
        raise SmokeError(f"Unknown-token revocation was not RFC 7009 compliant: {unknown_status} {unknown_body!r}")
    return client_id, access_token, refresh_token, connector_marker


def _assert_persisted(api_url: str, api_key: str, marker: str) -> None:
    searched = _request_json(
        "POST",
        f"{api_url}/search",
        payload={"query": "release persistence preference", "filters": {"user_id": "release-smoke-user"}},
        headers={"X-API-Key": api_key, "X-Project-ID": "default-project"},
        timeout=120,
    )
    if marker not in _memory_texts(searched):
        raise SmokeError("Memory was not available after the full Compose restart")


def _assert_connector_persisted_and_revoke(
    api_url: str,
    issuer: str,
    admin_access_token: str,
    client_id: str,
    access_token: str,
    refresh_token: str,
    marker: str,
) -> None:
    bearer_headers = {"Authorization": f"Bearer {access_token}"}
    searched = _request_json(
        "POST",
        f"{issuer}/search",
        payload={
            "query": "connector persistence marker",
            "filters": {"user_id": "release-connector-user"},
        },
        headers=bearer_headers,
        timeout=120,
    )
    if marker not in _memory_texts(searched):
        raise SmokeError("OAuth memory was not available after the full Compose restart")

    rotated = _request_json(
        "POST",
        f"{issuer}/oauth/token",
        form={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )
    next_access_token = str(rotated.get("access_token") or "")
    next_refresh_token = str(rotated.get("refresh_token") or "")
    if (
        not next_access_token.startswith("yqoa_")
        or not next_refresh_token.startswith("yqor_")
        or next_access_token == access_token
        or next_refresh_token == refresh_token
    ):
        raise SmokeError(f"Refresh-token rotation returned an unexpected response: {rotated!r}")

    stale_status, stale_body = _request_status("GET", f"{issuer}/v1/ping/", headers=bearer_headers)
    if stale_status != 401:
        raise SmokeError(f"Rotated access token remained usable: {stale_status} {stale_body!r}")

    next_headers = {"Authorization": f"Bearer {next_access_token}"}
    _request_json("GET", f"{issuer}/v1/ping/", headers=next_headers)
    revoked_status, revoked_body = _request_status(
        "POST",
        f"{issuer}/oauth/revoke",
        form={
            "token": next_refresh_token,
            "token_type_hint": "refresh_token",
            "client_id": client_id,
        },
    )
    if revoked_status != 200 or revoked_body:
        raise SmokeError(f"Refresh-token revocation failed: {revoked_status} {revoked_body!r}")
    rejected_status, rejected_body = _request_status(
        "GET",
        f"{issuer}/v1/ping/",
        headers=next_headers,
    )
    if rejected_status != 401:
        raise SmokeError(f"Revoked grant remained usable: {rejected_status} {rejected_body!r}")

    grants = _request_json(
        "GET",
        f"{api_url}/oauth/grants",
        headers={
            "Authorization": f"Bearer {admin_access_token}",
            "X-Project-ID": "default-project",
        },
    )
    matching = [item for item in grants.get("items", []) if item.get("client_id") == client_id]
    if len(matching) != 1 or matching[0].get("status") != "revoked":
        raise SmokeError(f"Dashboard grant state did not reflect revocation: {grants!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=f"yiqiao-release-smoke-{os.getpid()}-{secrets.token_hex(4)}")
    parser.add_argument("--api-port", type=int, default=18888)
    parser.add_argument("--dashboard-port", type=int, default=13000)
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--no-cache", action="store_true", help="Build API and dashboard images without cache")
    parser.add_argument("--skip-build", action="store_true", help="Use images already tagged for this project")
    parser.add_argument("--keep", action="store_true", help="Leave the isolated stack and volumes running")
    args = parser.parse_args()
    if args.no_cache and args.skip_build:
        parser.error("--no-cache and --skip-build cannot be used together")
    return args


def main() -> int:
    args = parse_args()
    stack = Stack(args)
    api_url = f"http://127.0.0.1:{args.api_port}"
    dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
    succeeded = False
    try:
        stack.prepare()
        stack.run("config", "--quiet")
        if args.no_cache:
            stack.run("build", "--no-cache", "yiqiao", "yiqiao-dashboard")
        stack.up(rebuild=not args.skip_build and not args.no_cache)

        _wait_for_health("API", f"{api_url}/api/health", args.timeout)
        _wait_for_health("dashboard", f"{dashboard_url}/api/health", args.timeout)
        _assert_connector_discovery(dashboard_url)

        revision = _assert_migration(stack)
        marker = f"YiQiao release marker {secrets.token_hex(8)} persists across restarts."
        admin_access_token, api_key, marker = _exercise_api(api_url, marker)
        client_id, oauth_access_token, oauth_refresh_token, connector_marker = _exercise_connector(
            api_url,
            dashboard_url,
            admin_access_token,
        )

        stack.run("down", "--remove-orphans")
        stack.up(rebuild=False)
        _wait_for_health("API", f"{api_url}/api/health", args.timeout)
        _wait_for_health("dashboard", f"{dashboard_url}/api/health", args.timeout)
        _assert_connector_discovery(dashboard_url)
        _assert_persisted(api_url, api_key, marker)
        _assert_connector_persisted_and_revoke(
            api_url,
            dashboard_url,
            admin_access_token,
            client_id,
            oauth_access_token,
            oauth_refresh_token,
            connector_marker,
        )

        print(
            f"PASS: migration {revision}; health, admin, API key, generic OAuth Device Flow, "
            "add/search, refresh rotation, revocation, and restart persistence verified."
        )
        succeeded = True
        return 0
    except (SmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        stack.diagnostics()
        return 1
    finally:
        if not args.keep:
            stack.cleanup()
        elif succeeded:
            print(f"Stack retained as Compose project {stack.project}.")


if __name__ == "__main__":
    raise SystemExit(main())
