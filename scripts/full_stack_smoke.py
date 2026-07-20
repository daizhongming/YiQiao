#!/usr/bin/env python3
"""Run the deterministic YiQiao release smoke test in an isolated Compose project."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
BASE_COMPOSE = SERVER / "docker-compose.yaml"
BUILD_COMPOSE = SERVER / "docker-compose.build.yaml"
E2E_COMPOSE = SERVER / "docker-compose.e2e.yaml"
ENV_FILE = SERVER / ".env"
REQUIRED_SECRETS = ("POSTGRES_PASSWORD", "NEO4J_PASSWORD", "JWT_SECRET")


class SmokeError(RuntimeError):
    pass


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
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


class Stack:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.project = args.project_name
        self.env = os.environ.copy()
        for key in REQUIRED_SECRETS:
            self.env.pop(key, None)
        self.env.update(
            {
                "API_BIND_ADDRESS": "127.0.0.1",
                "API_PORT": str(args.api_port),
                "DASHBOARD_BIND_ADDRESS": "127.0.0.1",
                "DASHBOARD_PORT": str(args.dashboard_port),
                "YIQIAO_API_IMAGE": f"{self.project}-api:smoke",
                "YIQIAO_DASHBOARD_IMAGE": f"{self.project}-dashboard:smoke",
                "YIQIAO_E2E_PROVIDER_KEY": "local-e2e-only",
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
    if not revision:
        raise SmokeError("Alembic did not record a migration revision")
    return revision


def _exercise_api(api_url: str, marker: str) -> tuple[str, str]:
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
    return api_key, marker


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
    parser.add_argument("--project-name", default=f"yiqiao-release-smoke-{os.getpid()}")
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

        revision = _assert_migration(stack)
        marker = f"YiQiao release marker {secrets.token_hex(8)} persists across restarts."
        api_key, marker = _exercise_api(api_url, marker)

        stack.run("down", "--remove-orphans")
        stack.up(rebuild=False)
        _wait_for_health("API", f"{api_url}/api/health", args.timeout)
        _wait_for_health("dashboard", f"{dashboard_url}/api/health", args.timeout)
        _assert_persisted(api_url, api_key, marker)

        print(f"PASS: migration {revision}; health, admin, API key, add/search, and restart persistence verified.")
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
