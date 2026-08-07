#!/usr/bin/env python3
"""Exercise the Hermes workflow or OpenClaw MCP contract with the official client."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import secrets
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_PROTOCOL = "2025-11-25"
READ_TOOLS = {
    "yiqiao_memory_search",
    "yiqiao_memory_get",
    "yiqiao_memory_history",
}
MEMORY_TOOLS = READ_TOOLS | {
    "yiqiao_memory_add",
    "yiqiao_memory_update",
}
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class SmokeError(RuntimeError):
    pass


class SmokeArgumentParser(argparse.ArgumentParser):
    """Keep unexpected argv values out of diagnostics."""

    def error(self, _message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: invalid command-line arguments.\n")

    def safe_error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SmokeError("--url must be an absolute HTTP or HTTPS URL.")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise SmokeError("--url must not contain credentials, a query string, or a fragment.")
    return value


def _require_success(result: Any, operation: str) -> dict[str, Any]:
    if result.isError:
        raise SmokeError(f"{operation} returned an MCP tool error.")
    structured = result.structuredContent
    if not isinstance(structured, dict) or structured.get("source") != "yiqiao_rest":
        raise SmokeError(f"{operation} did not return the YiQiao REST wrapper.")
    if structured.get("trust") != "untrusted" or not structured.get("warning"):
        raise SmokeError(f"{operation} did not mark recalled content as untrusted.")
    return structured


def _find_memory_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("id", "memory_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            found = _find_memory_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_memory_id(child)
            if found:
                return found
    return None


def _contains_marker(value: Any, marker: str) -> bool:
    return marker in json.dumps(value, ensure_ascii=False, sort_keys=True)


def _check_tools(listed: Any, required: set[str]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in listed.tools}
    missing = sorted(required - tools.keys())
    if missing:
        raise SmokeError(f"MCP server is missing required tools: {', '.join(missing)}")
    for tool in tools.values():
        schema = tool.inputSchema
        if schema.get("additionalProperties") is not False:
            raise SmokeError(f"{tool.name} does not reject unknown arguments.")
        properties = schema.get("properties") or {}
        forbidden = {"project_id", "api_key", "credential"} & properties.keys()
        if forbidden:
            raise SmokeError(f"{tool.name} exposes a forbidden credential or project field.")
    return tools


async def _hermes(session: ClientSession, listed: Any) -> None:
    _check_tools(listed, MEMORY_TOOLS)
    marker = f"YiQiao Hermes contract {secrets.token_hex(12)}"
    run_id = f"contract-{secrets.token_hex(8)}"
    entities = {
        "user_id": "yiqiao-contract-user",
        "agent_id": "hermes-agent",
        "app_id": "hermes",
        "run_id": run_id,
    }
    added = _require_success(
        await session.call_tool(
            "yiqiao_memory_add",
            {
                "messages": [{"role": "user", "content": marker}],
                **entities,
                "metadata": {"source": "hermes-contract-smoke"},
                "infer": False,
            },
        ),
        "Hermes add",
    )
    memory_id = _find_memory_id(added.get("data"))
    if not memory_id:
        raise SmokeError("Hermes add did not return a memory identifier.")

    searched = _require_success(
        await session.call_tool(
            "yiqiao_memory_search",
            {"query": marker, **entities, "top_k": 10},
        ),
        "Hermes search",
    )
    if not _contains_marker(searched.get("data"), marker):
        raise SmokeError("Hermes search did not return the written marker.")

    fetched = _require_success(
        await session.call_tool("yiqiao_memory_get", {"memory_id": memory_id}),
        "Hermes get",
    )
    if not _contains_marker(fetched.get("data"), marker):
        raise SmokeError("Hermes get did not return the written marker.")


async def _openclaw(session: ClientSession, listed: Any) -> None:
    _check_tools(listed, READ_TOOLS)
    result = _require_success(
        await session.call_tool(
            "yiqiao_memory_search",
            {
                "query": "OpenClaw YiQiao MCP contract probe",
                "user_id": "yiqiao-contract-user",
                "agent_id": "openclaw-agent",
                "app_id": "openclaw",
                "run_id": f"contract-{secrets.token_hex(8)}",
                "top_k": 1,
            },
        ),
        "OpenClaw search",
    )
    if "data" not in result:
        raise SmokeError("OpenClaw search returned no data field.")


async def _run(args: argparse.Namespace, api_key: str) -> None:
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    async with httpx.AsyncClient(
        headers={"X-API-Key": api_key},
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as http_client:
        async with streamable_http_client(
            args.url,
            http_client=http_client,
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                if initialized.protocolVersion != EXPECTED_PROTOCOL:
                    raise SmokeError(f"Expected MCP {EXPECTED_PROTOCOL}, negotiated {initialized.protocolVersion}.")
                listed = await session.list_tools()
                if args.mode == "hermes":
                    await _hermes(session, listed)
                else:
                    await _openclaw(session, listed)


def parse_args() -> argparse.Namespace:
    parser = SmokeArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hermes", "openclaw"))
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--key-env", default="YIQIAO_MCP_SMOKE_API_KEY")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    try:
        args.url = _safe_url(args.url)
    except SmokeError as error:
        parser.safe_error(str(error))
    if not ENV_NAME.fullmatch(args.key_env):
        parser.safe_error("--key-env must name an uppercase environment variable.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.safe_error("--timeout must be a positive finite number.")
    return args


def main() -> int:
    args = parse_args()
    api_key = os.environ.get(args.key_env, "")
    if not api_key or api_key != api_key.strip():
        print(f"FAIL: set {args.key_env} to a project API key.", file=sys.stderr)
        return 2
    try:
        asyncio.run(asyncio.wait_for(_run(args, api_key), timeout=args.timeout + 10.0))
    except (SmokeError, TimeoutError, OSError) as error:
        message = str(error).replace(api_key, "[REDACTED]")
        print(f"FAIL: {message}", file=sys.stderr)
        return 1
    print(f"PASS: {args.mode} MCP contract verified at {args.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
