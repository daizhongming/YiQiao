from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from yiqiao_mcp.config import Settings, ToolProfile
from yiqiao_mcp.errors import CompanionError, InputError
from yiqiao_mcp.rest import ProjectCredential, YiQiaoRestClient
from yiqiao_mcp.tools import (
    DELETE_TOOL_NAME,
    MAX_MESSAGE_CHARS,
    MAX_METADATA_BYTES,
    MAX_QUERY_CHARS,
    MAX_TEXT_CHARS,
    MAX_TOP_K,
    PROFILE_TOOL_NAMES,
    tools_for_profile,
    validate_tool_arguments,
)


def test_profiles_expose_only_the_expected_tools():
    assert (
        tuple(tool.name for tool in tools_for_profile(ToolProfile.READ_ONLY))
        == PROFILE_TOOL_NAMES[ToolProfile.READ_ONLY]
    )
    assert tuple(tool.name for tool in tools_for_profile(ToolProfile.MEMORY)) == PROFILE_TOOL_NAMES[ToolProfile.MEMORY]
    assert (
        tuple(tool.name for tool in tools_for_profile(ToolProfile.DESTRUCTIVE))
        == PROFILE_TOOL_NAMES[ToolProfile.DESTRUCTIVE]
    )
    assert DELETE_TOOL_NAME not in PROFILE_TOOL_NAMES[ToolProfile.MEMORY]


def test_all_tool_schemas_are_closed_and_have_no_project_selector():
    for tool in tools_for_profile(ToolProfile.DESTRUCTIVE):
        assert tool.inputSchema["additionalProperties"] is False
        assert "project_id" not in tool.inputSchema["properties"]


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("yiqiao_memory_search", {"query": "hello", "project_id": "project-b"}),
        (
            "yiqiao_memory_add",
            {
                "messages": [{"role": "system", "content": "not a raw turn"}],
                "user_id": "alice",
            },
        ),
        (
            "yiqiao_memory_add",
            {
                "messages": [{"role": "user", "content": "hello"}],
                "user_id": "alice",
                "metadata": {"nested": {"project_id": "project-b"}},
            },
        ),
        (
            "yiqiao_memory_update",
            {"memory_id": "00000000-0000-4000-8000-000000000001"},
        ),
    ],
)
def test_strict_tool_validation_rejects_unsafe_inputs(tool_name, arguments):
    with pytest.raises(InputError):
        validate_tool_arguments(tool_name, arguments)


def test_metadata_byte_limit_is_enforced_without_echoing_input():
    marker = "sensitive-marker"
    arguments = {
        "messages": [{"role": "user", "content": "hello"}],
        "user_id": "alice",
        "metadata": {"blob": marker + ("x" * MAX_METADATA_BYTES)},
    }
    with pytest.raises(InputError) as raised:
        validate_tool_arguments("yiqiao_memory_add", arguments)
    assert marker not in str(raised.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "sensitive-marker" + ("x" * MAX_QUERY_CHARS)},
        {"query": "hello", "top_k": 0},
        {"query": "hello", "top_k": MAX_TOP_K + 1},
        {
            "messages": [{"role": "user", "content": "sensitive-marker" + ("x" * MAX_TEXT_CHARS)}],
            "user_id": "alice",
        },
        {
            "messages": [
                {"role": "user", "content": "x" * ((MAX_MESSAGE_CHARS // 3) + 1)},
                {"role": "assistant", "content": "y" * ((MAX_MESSAGE_CHARS // 3) + 1)},
                {"role": "user", "content": "z" * ((MAX_MESSAGE_CHARS // 3) + 1)},
            ],
            "user_id": "alice",
        },
    ],
    ids=("query", "top-k-minimum", "top-k-maximum", "message-text", "message-aggregate"),
)
def test_query_top_k_and_message_limits_are_enforced_without_echoing_input(arguments):
    tool_name = "yiqiao_memory_add" if "messages" in arguments else "yiqiao_memory_search"

    with pytest.raises(InputError) as raised:
        validate_tool_arguments(tool_name, arguments)

    assert "sensitive-marker" not in str(raised.value)


def test_project_credential_repr_never_contains_the_secret():
    secret = "yqsk_repr-must-not-leak"
    assert secret not in repr(ProjectCredential(secret))


@pytest.mark.asyncio
async def test_rest_client_forwards_credentials_per_request_without_cross_talk():
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0)
        body = json.loads(request.content)
        seen.append((request.headers["x-api-key"], body["query"]))
        return httpx.Response(200, json={"results": [{"memory": body["query"]}]})

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        first, second = await asyncio.gather(
            client.request("POST", "/search", ProjectCredential("key-a"), json_body={"query": "alpha"}),
            client.request("POST", "/search", ProjectCredential("key-b"), json_body={"query": "beta"}),
        )
    finally:
        await client.aclose()

    assert first["results"][0]["memory"] == "alpha"
    assert second["results"][0]["memory"] == "beta"
    assert sorted(seen) == [("key-a", "alpha"), ("key-b", "beta")]


@pytest.mark.asyncio
async def test_rest_error_is_sanitized_and_preserves_safe_status_metadata():
    secret = "yqsk_this-must-never-leak"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "17", "X-Request-ID": "request-123"},
            json={"detail": {"code": "quota_exceeded", "message": secret}},
        )

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CompanionError) as raised:
            await client.request("POST", "/search", ProjectCredential(secret), json_body={"query": "hello"})
    finally:
        await client.aclose()

    error = raised.value
    assert error.status == 429
    assert error.code == "quota_exceeded"
    assert error.retry_after == 17
    assert error.request_id == "request-123"
    assert secret not in str(error)
    assert secret not in json.dumps(error.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["code", "body-request-id", "header-request-id"])
async def test_rest_error_drops_metadata_that_contains_the_project_key(source):
    secret = "yqsk_error-metadata-must-not-leak"

    async def handler(_request: httpx.Request) -> httpx.Response:
        detail = {"code": secret if source == "code" else "quota_exceeded"}
        payload = {"detail": detail}
        headers = {}
        if source == "body-request-id":
            payload["request_id"] = secret
        if source == "header-request-id":
            headers["X-Request-ID"] = secret
        return httpx.Response(429, headers=headers, json=payload)

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CompanionError) as raised:
            await client.request("POST", "/search", ProjectCredential(secret), json_body={"query": "hello"})
    finally:
        await client.aclose()

    error = raised.value
    assert error.code == ("rest_rate_limited" if source == "code" else "quota_exceeded")
    assert error.request_id is None
    assert secret not in json.dumps(error.as_dict())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_code",
    [
        (422, "rest_validation_failed"),
        (503, "rest_unavailable"),
    ],
)
async def test_rest_422_and_503_statuses_are_preserved(status, expected_code):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": "upstream detail is deliberately not relayed"})

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CompanionError) as raised:
            await client.request("POST", "/search", ProjectCredential("key-a"), json_body={"query": "hello"})
    finally:
        await client.aclose()

    assert raised.value.status == status
    assert raised.value.code == expected_code


@pytest.mark.asyncio
async def test_rest_timeout_is_sanitized():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("included-sensitive-debug-text", request=request)

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(CompanionError) as raised:
            await client.request("GET", "/memories/one", ProjectCredential("key-a"))
    finally:
        await client.aclose()

    assert raised.value.status == 504
    assert "sensitive" not in str(raised.value)


@pytest.mark.asyncio
async def test_rest_cancellation_propagates_to_the_in_flight_request():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    client = YiQiaoRestClient(Settings(), transport=httpx.MockTransport(handler))
    task = asyncio.create_task(client.request("GET", "/memories/one", ProjectCredential("key-a")))
    await started.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await client.aclose()

    assert cancelled.is_set()
