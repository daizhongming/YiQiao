#!/usr/bin/env python3
"""Run the deterministic YiQiao release smoke test in an isolated Compose project."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
BASE_COMPOSE = SERVER / "docker-compose.yaml"
BUILD_COMPOSE = SERVER / "docker-compose.build.yaml"
E2E_COMPOSE = SERVER / "docker-compose.e2e.yaml"
ENV_FILE = SERVER / ".env"
MCP_CONTRACT_SMOKE = ROOT / "scripts" / "mcp_contract_smoke.py"
REQUIRED_SECRETS = (
    "POSTGRES_PASSWORD",
    "NEO4J_PASSWORD",
    "JWT_SECRET",
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
    "POSTGRES_COLLECTION_NAME",
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "YIQIAO_DIR",
    "YIQIAO_DISABLE_SPACY_DOWNLOAD",
)
MCP_RUNTIME_ENV_NAMES = (
    "MCP_BIND_ADDRESS",
    "MCP_PORT",
    "YIQIAO_MCP_IMAGE",
    "YIQIAO_MCP_PROFILE",
    "YIQIAO_MCP_ALLOWED_HOSTS",
    "YIQIAO_MCP_ALLOWED_ORIGINS",
    "YIQIAO_MCP_CONNECT_TIMEOUT",
    "YIQIAO_MCP_REQUEST_TIMEOUT",
)
E2E_PROVIDER_KEY = "local-e2e-only"
E2E_PROVIDER_BASE_URL = "http://model-stub:8080/v1"
E2E_LLM_MODEL = "yiqiao-e2e"
E2E_EMBEDDER_MODEL = "yiqiao-e2e-embedding"
E2E_EMBEDDING_DIMS = 16
EXPECTED_MIGRATION = "019"
PROVIDER_SETUP_DETAIL = (
    "Model provider credentials are not configured. Complete provider setup before using memory operations."
)
MCP_KEY_ENV = "YIQIAO_MCP_SMOKE_API_KEY"
MCP_CONTAINER_URL = "http://yiqiao-mcp:8000/mcp"
MCP_CONTRACT_CONTAINER_PATH = "/opt/yiqiao/mcp_contract_smoke.py"
_PSQL_VARIABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PSQL_VARIABLE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$")


class SmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectApiKey:
    id: str
    project_id: str
    value: str = field(repr=False)


def _redact(value: str, sensitive_values: Iterable[str]) -> str:
    redacted = value
    candidates = {candidate for candidate in sensitive_values if candidate}
    for candidate in sorted(candidates, key=len, reverse=True):
        redacted = redacted.replace(candidate, "[REDACTED]")
    return redacted


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> Any:
    body = None
    content_type = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        content_type = "application/json"
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


def _wait_for_mcp_health(mcp_url: str, timeout: float) -> dict[str, Any]:
    return _wait_for_health("MCP", f"{mcp_url}/healthz", timeout)


def _wait_until(
    description: str,
    timeout: float,
    probe: Callable[[], Any],
    ready: Callable[[Any], bool],
    *,
    interval: float = 0.5,
) -> Any:
    deadline = time.monotonic() + timeout
    last_value: Any = None
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            last_value = probe()
            last_error = None
            if ready(last_value):
                return last_value
        except Exception as exc:  # noqa: BLE001 - report the final bounded-polling failure
            last_error = exc
        time.sleep(interval)
    detail = f"last error: {last_error}" if last_error is not None else f"last value: {last_value!r}"
    raise SmokeError(f"Timed out waiting for {description} ({detail})")


def _expect_http_error(
    method: str,
    url: str,
    expected_status: int,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> Any:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            raise SmokeError(f"{method} {url} unexpectedly returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if exc.code != expected_status:
            detail = raw.decode("utf-8", errors="replace")
            raise SmokeError(f"{method} {url} returned HTTP {exc.code}, expected {expected_status}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise SmokeError(f"{method} {url} failed: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"{method} {url} returned invalid JSON for expected HTTP {expected_status}") from exc


def _run_mcp_contract(
    stack: Stack,
    mode: str,
    api_key: str,
    timeout: float,
    *,
    expect_success: bool,
    sensitive_values: Iterable[str] = (),
) -> subprocess.CompletedProcess[str]:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        f"{stack.project}_backend",
        "--read-only",
        "--security-opt",
        "no-new-privileges:true",
        "--cap-drop",
        "ALL",
        "--env",
        MCP_KEY_ENV,
        "--volume",
        f"{MCP_CONTRACT_SMOKE}:{MCP_CONTRACT_CONTAINER_PATH}:ro",
        stack.env["YIQIAO_MCP_IMAGE"],
        "python",
        MCP_CONTRACT_CONTAINER_PATH,
        mode,
        "--url",
        MCP_CONTAINER_URL,
        "--key-env",
        MCP_KEY_ENV,
        "--timeout",
        str(timeout),
    ]
    contract_env = os.environ.copy()
    contract_env[MCP_KEY_ENV] = api_key
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=contract_env,
        check=False,
        text=True,
        capture_output=True,
    )
    succeeded = result.returncode == 0
    if succeeded == expect_success:
        return result

    outcome = "failed" if expect_success else "unexpectedly passed"
    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    safe_output = _redact(output, (*sensitive_values, api_key))
    detail = f"\n{safe_output}" if safe_output else ""
    raise SmokeError(f"MCP {mode} contract {outcome} ({result.returncode}){detail}")


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
        self.mcp_port = int(getattr(args, "mcp_port", 18765))
        self.env = os.environ.copy()
        for key in (*REQUIRED_SECRETS, *PROVIDER_CREDENTIAL_ENV_NAMES, *RUNTIME_ENV_NAMES, *MCP_RUNTIME_ENV_NAMES):
            self.env.pop(key, None)
        self.env.update(
            {
                "API_BIND_ADDRESS": "127.0.0.1",
                "API_PORT": str(args.api_port),
                "DASHBOARD_BIND_ADDRESS": "127.0.0.1",
                "DASHBOARD_PORT": str(args.dashboard_port),
                "MCP_BIND_ADDRESS": "127.0.0.1",
                "MCP_PORT": str(self.mcp_port),
                "YIQIAO_API_IMAGE": f"{self.project}-api:smoke",
                "YIQIAO_DASHBOARD_IMAGE": f"{self.project}-dashboard:smoke",
                "YIQIAO_MCP_IMAGE": f"{self.project}-mcp:smoke",
                "YIQIAO_MCP_PROFILE": "memory",
                "YIQIAO_MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*,[::1]:*,yiqiao-mcp:*",
                "YIQIAO_MCP_ALLOWED_ORIGINS": "http://127.0.0.1:*,http://localhost:*,http://[::1]:*",
                "YIQIAO_MCP_CONNECT_TIMEOUT": "5",
                "YIQIAO_MCP_REQUEST_TIMEOUT": "30",
                "YIQIAO_PULL_POLICY": "build",
            }
        )
        self.created_env = False
        self._api_key_values: list[str] = []
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

    def run(
        self,
        *args: str,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.compose, *args],
            cwd=SERVER,
            env=self.env,
            check=False,
            text=True,
            capture_output=capture,
            input=input_text,
        )
        if check and result.returncode:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
            raise SmokeError(f"docker compose {' '.join(args)} failed ({result.returncode})\n{output}")
        return result

    @property
    def api_key_values(self) -> tuple[str, ...]:
        return tuple(self._api_key_values)

    def remember_api_key(self, value: str) -> None:
        if value and value not in self._api_key_values:
            self._api_key_values.append(value)

    def redact(self, value: str) -> str:
        return _redact(value, self._api_key_values)

    def psql(self, query: str, *, variables: Mapping[str, str] | None = None) -> str:
        variable_args: list[str] = []
        for name, value in sorted((variables or {}).items()):
            if not _PSQL_VARIABLE_NAME.fullmatch(name):
                raise SmokeError(f"Unsafe psql variable name: {name!r}")
            if not _PSQL_VARIABLE_VALUE.fullmatch(value):
                raise SmokeError(f"Unsafe psql variable value for {name}")
            variable_args.extend(["-v", f"{name}={value}"])
        result = self.run(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            *variable_args,
            "-U",
            "postgres",
            "-d",
            "yiqiao_app",
            "-At",
            "-F",
            "|",
            capture=True,
            input_text=f"{query.rstrip()}\n",
        )
        return result.stdout.strip()

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


def _jwt_headers(access_token: str, project_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    if project_id is not None:
        headers["X-Project-ID"] = project_id
    return headers


def _response_id(response: Any, resource: str) -> str:
    value = str(response.get("id") or "") if isinstance(response, dict) else ""
    if not value:
        raise SmokeError(f"{resource} creation did not return an ID")
    return value


def _create_acceptance_projects(api_url: str, access_token: str) -> tuple[str, str]:
    suffix = secrets.token_hex(4)
    organization = _request_json(
        "POST",
        f"{api_url}/api/v1/orgs/organizations/",
        payload={"name": f"MCP acceptance {suffix}"},
        headers=_jwt_headers(access_token),
    )
    organization_id = _response_id(organization, "MCP acceptance organization")

    project_ids = []
    for label in ("A", "B"):
        project = _request_json(
            "POST",
            f"{api_url}/api/v1/orgs/organizations/{organization_id}/projects/",
            payload={"name": f"MCP acceptance {label} {suffix}"},
            headers=_jwt_headers(access_token),
        )
        project_ids.append(_response_id(project, f"MCP acceptance project {label}"))
    if project_ids[0] == project_ids[1]:
        raise SmokeError("MCP acceptance projects did not receive distinct IDs")
    return project_ids[0], project_ids[1]


def _create_project_api_key(
    stack: Stack,
    api_url: str,
    access_token: str,
    project_id: str,
    label: str,
    *,
    scopes: list[str] | None = None,
    expires_at: datetime | None = None,
) -> ProjectApiKey:
    payload: dict[str, Any] = {"label": label, "project_id": project_id}
    if scopes is not None:
        payload["scopes"] = scopes
    if expires_at is not None:
        payload["expires_at"] = expires_at.isoformat()
    response = _request_json(
        "POST",
        f"{api_url}/api-keys",
        payload=payload,
        headers=_jwt_headers(access_token, project_id),
    )
    key_id = _response_id(response, f"{label} API key")
    value = str(response.get("key") or "") if isinstance(response, dict) else ""
    response_project = str(response.get("project_id") or "") if isinstance(response, dict) else ""
    if not value:
        raise SmokeError(f"{label} API key creation did not return the one-time key")
    stack.remember_api_key(value)
    if response_project != project_id:
        raise SmokeError(f"{label} API key was not bound to the selected project")
    return ProjectApiKey(id=key_id, project_id=project_id, value=value)


def _memory_ids(response: Any) -> list[str]:
    values = (
        response.get("results", []) if isinstance(response, dict) else response if isinstance(response, list) else []
    )
    return [
        str(item.get("id") or item.get("memory_id") or "")
        for item in values
        if isinstance(item, dict) and (item.get("id") or item.get("memory_id"))
    ]


def _add_known_memory(api_url: str, project_key: ProjectApiKey, marker: str) -> str:
    response = _request_json(
        "POST",
        f"{api_url}/memories",
        payload={
            "messages": [{"role": "user", "content": marker}],
            "user_id": "mcp-cross-project-user",
            "infer": False,
        },
        headers={"X-API-Key": project_key.value},
        timeout=120,
    )
    memory_ids = _memory_ids(response)
    if marker not in _memory_texts(response) or not memory_ids:
        raise SmokeError("Cross-project probe add did not return its memory and ID")
    return memory_ids[0]


def _integer_fields(value: str, expected: int, description: str) -> tuple[int, ...]:
    fields = value.split("|") if value else []
    if len(fields) != expected:
        raise SmokeError(f"Unexpected {description} SQL result")
    try:
        return tuple(int(field) for field in fields)
    except ValueError as exc:
        raise SmokeError(f"Non-numeric {description} SQL result") from exc


def _key_log_snapshot(stack: Stack, project_key: ProjectApiKey) -> tuple[int, int, int, int]:
    output = stack.psql(
        """
SELECT
    COUNT(*),
    COUNT(*) FILTER (WHERE operation = 'memory_write' AND status_code < 400),
    COUNT(*) FILTER (WHERE operation = 'memory_search' AND status_code < 400),
    COUNT(*) FILTER (WHERE project_id IS DISTINCT FROM :'project_id')
FROM request_logs
WHERE api_key_id = :'key_id'::uuid;
""".strip(),
        variables={"key_id": project_key.id, "project_id": project_key.project_id},
    )
    values = _integer_fields(output, 4, "request-log accounting")
    return values[0], values[1], values[2], values[3]


def _wait_for_key_accounting(
    stack: Stack,
    project_key: ProjectApiKey,
    *,
    minimum_total: int,
    minimum_writes: int,
    minimum_searches: int,
    timeout: float,
) -> tuple[int, int, int, int]:
    snapshot = _wait_until(
        f"request-log accounting for key {project_key.id}",
        timeout,
        lambda: _key_log_snapshot(stack, project_key),
        lambda value: value[0] >= minimum_total and value[1] >= minimum_writes and value[2] >= minimum_searches,
    )
    if snapshot[3]:
        raise SmokeError(f"API key {project_key.id} was attributed to {snapshot[3]} request log(s) outside its project")
    return snapshot


def _request_status_count(stack: Stack, project_key: ProjectApiKey, operation: str, status: int) -> int:
    if operation not in {"memory_read", "memory_search", "memory_write"}:
        raise SmokeError(f"Unsupported request-log operation: {operation}")
    output = stack.psql(
        """
SELECT COUNT(*)
FROM request_logs
WHERE api_key_id = :'key_id'::uuid
  AND project_id = :'project_id'
  AND operation = :'operation'
  AND status_code = :'status'::integer;
""".strip(),
        variables={
            "key_id": project_key.id,
            "operation": operation,
            "project_id": project_key.project_id,
            "status": str(status),
        },
    )
    return _integer_fields(output, 1, "request-log status")[0]


def _wait_for_request_status(
    stack: Stack,
    project_key: ProjectApiKey,
    operation: str,
    status: int,
    timeout: float,
) -> None:
    _wait_until(
        f"HTTP {status} {operation} accounting for key {project_key.id}",
        timeout,
        lambda: _request_status_count(stack, project_key, operation, status),
        lambda count: count >= 1,
    )


def _create_webhook(api_url: str, access_token: str, project_id: str, label: str) -> str:
    response = _request_json(
        "POST",
        f"{api_url}/webhooks",
        payload={
            "name": label,
            "url": "http://model-stub:8080/webhook",
            "events": ["memory.added"],
        },
        headers=_jwt_headers(access_token, project_id),
    )
    return _response_id(response, label)


def _webhook_delivery_state(
    api_url: str,
    access_token: str,
    project_id: str,
    hook_id: str,
) -> tuple[str | None, str | None]:
    response = _request_json(
        "GET",
        f"{api_url}/webhooks",
        headers=_jwt_headers(access_token, project_id),
    )
    if not isinstance(response, list):
        raise SmokeError("Webhook listing returned an unexpected response")
    hook = next((item for item in response if isinstance(item, dict) and str(item.get("id")) == hook_id), None)
    if hook is None:
        raise SmokeError(f"Webhook {hook_id} was not listed in its project")
    status = str(hook.get("last_delivery_status")) if hook.get("last_delivery_status") is not None else None
    delivered_at = str(hook.get("last_delivery_at")) if hook.get("last_delivery_at") is not None else None
    return status, delivered_at


def _set_search_quota(api_url: str, access_token: str, project_key: ProjectApiKey) -> None:
    response = _request_json(
        "PUT",
        f"{api_url}/usage/policies",
        payload={
            "scope_type": "api_key",
            "scope_id": project_key.id,
            "project_id": project_key.project_id,
            "policies": [
                {
                    "metric": "memory_searches",
                    "period": "day",
                    "limit_value": 1,
                    "mode": "hard",
                    "warning_threshold": 0.8,
                }
            ],
        },
        headers=_jwt_headers(access_token, project_key.project_id),
    )
    policies = response.get("policies") if isinstance(response, dict) else None
    if not isinstance(policies, list) or len(policies) != 1:
        raise SmokeError("Search quota policy was not accepted")


def _exercise_api(stack: Stack, api_url: str, marker: str) -> tuple[str, str, str]:
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
    stack.remember_api_key(api_key)

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


def _exercise_mcp_acceptance(
    stack: Stack,
    api_url: str,
    access_token: str,
    timeout: float,
) -> ProjectApiKey:
    contract_timeout = min(timeout, 120.0)
    polling_timeout = min(timeout, 60.0)
    project_a, project_b = _create_acceptance_projects(api_url, access_token)
    key_a = _create_project_api_key(stack, api_url, access_token, project_a, "MCP project A")
    key_b = _create_project_api_key(stack, api_url, access_token, project_b, "MCP project B")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _run_mcp_contract,
                stack,
                "hermes",
                project_key.value,
                contract_timeout,
                expect_success=True,
                sensitive_values=stack.api_key_values,
            )
            for project_key in (key_a, key_b)
        ]
        for future in futures:
            future.result()

    _wait_for_key_accounting(
        stack,
        key_a,
        minimum_total=3,
        minimum_writes=1,
        minimum_searches=1,
        timeout=polling_timeout,
    )
    _wait_for_key_accounting(
        stack,
        key_b,
        minimum_total=3,
        minimum_writes=1,
        minimum_searches=1,
        timeout=polling_timeout,
    )
    _run_mcp_contract(
        stack,
        "openclaw",
        key_a.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )

    cross_project_marker = f"MCP project A isolation {secrets.token_hex(8)}"
    project_a_memory_id = _add_known_memory(api_url, key_a, cross_project_marker)
    _expect_http_error(
        "GET",
        f"{api_url}/memories/{project_a_memory_id}",
        404,
        headers={"X-API-Key": key_b.value},
    )
    _expect_http_error(
        "POST",
        f"{api_url}/search",
        403,
        payload={"query": cross_project_marker, "filters": {"user_id": "mcp-cross-project-user"}},
        headers={"X-API-Key": key_a.value, "X-Project-ID": project_b},
    )

    revoked_key = _create_project_api_key(stack, api_url, access_token, project_a, "MCP revoked")
    _run_mcp_contract(
        stack,
        "openclaw",
        revoked_key.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )
    _request_json(
        "DELETE",
        f"{api_url}/api-keys/{revoked_key.id}",
        headers=_jwt_headers(access_token, project_a),
    )
    _run_mcp_contract(
        stack,
        "openclaw",
        revoked_key.value,
        contract_timeout,
        expect_success=False,
        sensitive_values=stack.api_key_values,
    )

    expired_key = _create_project_api_key(
        stack,
        api_url,
        access_token,
        project_a,
        "MCP expired",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    _run_mcp_contract(
        stack,
        "openclaw",
        expired_key.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )
    updated = stack.psql(
        """
WITH expired AS (
    UPDATE api_keys
    SET expires_at = NOW() - INTERVAL '1 minute'
    WHERE id = :'key_id'::uuid
    RETURNING 1
)
SELECT COUNT(*) FROM expired;
""".strip(),
        variables={"key_id": expired_key.id},
    )
    if updated != "1":
        raise SmokeError("Expired-key probe did not update exactly one API key")
    _run_mcp_contract(
        stack,
        "openclaw",
        expired_key.value,
        contract_timeout,
        expect_success=False,
        sensitive_values=stack.api_key_values,
    )

    read_only_key = _create_project_api_key(
        stack,
        api_url,
        access_token,
        project_a,
        "MCP read only",
        scopes=["memory:read"],
    )
    _run_mcp_contract(
        stack,
        "openclaw",
        read_only_key.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )
    _run_mcp_contract(
        stack,
        "hermes",
        read_only_key.value,
        contract_timeout,
        expect_success=False,
        sensitive_values=stack.api_key_values,
    )
    _wait_for_key_accounting(
        stack,
        read_only_key,
        minimum_total=2,
        minimum_writes=0,
        minimum_searches=1,
        timeout=polling_timeout,
    )
    _wait_for_request_status(stack, read_only_key, "memory_write", 403, polling_timeout)

    write_only_key = _create_project_api_key(
        stack,
        api_url,
        access_token,
        project_a,
        "MCP write only",
        scopes=["memory:write"],
    )
    _run_mcp_contract(
        stack,
        "hermes",
        write_only_key.value,
        contract_timeout,
        expect_success=False,
        sensitive_values=stack.api_key_values,
    )
    _wait_for_key_accounting(
        stack,
        write_only_key,
        minimum_total=2,
        minimum_writes=1,
        minimum_searches=0,
        timeout=polling_timeout,
    )
    _wait_for_request_status(stack, write_only_key, "memory_search", 403, polling_timeout)

    quota_key = _create_project_api_key(stack, api_url, access_token, project_a, "MCP search quota")
    _set_search_quota(api_url, access_token, quota_key)
    _run_mcp_contract(
        stack,
        "openclaw",
        quota_key.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )
    _wait_for_key_accounting(
        stack,
        quota_key,
        minimum_total=1,
        minimum_writes=0,
        minimum_searches=1,
        timeout=polling_timeout,
    )
    _run_mcp_contract(
        stack,
        "openclaw",
        quota_key.value,
        contract_timeout,
        expect_success=False,
        sensitive_values=stack.api_key_values,
    )
    _wait_for_request_status(stack, quota_key, "memory_search", 429, polling_timeout)

    hook_a = _create_webhook(api_url, access_token, project_a, "MCP project A delivery")
    hook_b = _create_webhook(api_url, access_token, project_b, "MCP project B isolation")
    _run_mcp_contract(
        stack,
        "hermes",
        key_a.value,
        contract_timeout,
        expect_success=True,
        sensitive_values=stack.api_key_values,
    )
    state_a = _wait_until(
        "project A webhook dispatch",
        polling_timeout,
        lambda: _webhook_delivery_state(api_url, access_token, project_a, hook_a),
        lambda state: state[0] is not None and state[1] is not None,
    )
    if not state_a[0]:
        raise SmokeError("Project A webhook dispatch did not record a delivery status")
    state_b = _webhook_delivery_state(api_url, access_token, project_b, hook_b)
    if state_b != (None, None):
        raise SmokeError("Project B webhook was dispatched for a project A MCP write")

    for project_key, minimum_total in (
        (key_a, 8),
        (key_b, 4),
        (read_only_key, 2),
        (write_only_key, 2),
        (quota_key, 2),
    ):
        _wait_for_key_accounting(
            stack,
            project_key,
            minimum_total=minimum_total,
            minimum_writes=0,
            minimum_searches=0,
            timeout=polling_timeout,
        )
    return key_a


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", default=f"yiqiao-release-smoke-{os.getpid()}-{secrets.token_hex(4)}")
    parser.add_argument("--api-port", type=int, default=18888)
    parser.add_argument("--dashboard-port", type=int, default=13000)
    parser.add_argument("--mcp-port", type=int, default=18765)
    parser.add_argument("--timeout", type=float, default=420)
    parser.add_argument("--no-cache", action="store_true", help="Build API, dashboard, and MCP images without cache")
    parser.add_argument("--skip-build", action="store_true", help="Use images already tagged for this project")
    parser.add_argument("--keep", action="store_true", help="Leave the isolated stack and volumes running")
    args = parser.parse_args()
    if args.no_cache and args.skip_build:
        parser.error("--no-cache and --skip-build cannot be used together")
    if not all(1 <= port <= 65535 for port in (args.api_port, args.dashboard_port, args.mcp_port)):
        parser.error("service ports must be between 1 and 65535")
    if len({args.api_port, args.dashboard_port, args.mcp_port}) != 3:
        parser.error("service ports must be distinct")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    return args


def main() -> int:
    args = parse_args()
    stack = Stack(args)
    api_url = f"http://127.0.0.1:{args.api_port}"
    dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
    mcp_url = f"http://127.0.0.1:{args.mcp_port}"
    succeeded = False
    try:
        stack.prepare()
        stack.run("config", "--quiet")
        if args.no_cache:
            stack.run("build", "--no-cache", "yiqiao", "yiqiao-dashboard", "yiqiao-mcp")
        stack.up(rebuild=not args.skip_build and not args.no_cache)

        _wait_for_health("API", f"{api_url}/api/health", args.timeout)
        _wait_for_health("dashboard", f"{dashboard_url}/api/health", args.timeout)
        _wait_for_mcp_health(mcp_url, args.timeout)
        revision = _assert_migration(stack)
        marker = f"YiQiao release marker {secrets.token_hex(8)} persists across restarts."
        admin_access_token, api_key, marker = _exercise_api(stack, api_url, marker)
        restart_mcp_key = _exercise_mcp_acceptance(
            stack,
            api_url,
            admin_access_token,
            args.timeout,
        )

        stack.run("down", "--remove-orphans")
        stack.up(rebuild=False)
        _wait_for_health("API", f"{api_url}/api/health", args.timeout)
        _wait_for_health("dashboard", f"{dashboard_url}/api/health", args.timeout)
        _wait_for_mcp_health(mcp_url, args.timeout)
        _assert_persisted(api_url, api_key, marker)
        _run_mcp_contract(
            stack,
            "openclaw",
            restart_mcp_key.value,
            min(args.timeout, 120.0),
            expect_success=True,
            sensitive_values=stack.api_key_values,
        )

        print(
            f"PASS: migration {revision}; API/dashboard/MCP health, admin, scoped keys, MCP contracts, "
            "project isolation, quota, webhooks, accounting, and restart persistence verified."
        )
        succeeded = True
        return 0
    except (SmokeError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {stack.redact(str(exc))}", file=sys.stderr)
        stack.diagnostics()
        return 1
    finally:
        if not args.keep:
            stack.cleanup()
        elif succeeded:
            print(f"Stack retained as Compose project {stack.project}.")


if __name__ == "__main__":
    raise SystemExit(main())
