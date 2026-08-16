from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from yiqiao_mcp.config import Settings
from yiqiao_mcp.errors import CompanionError

_SAFE_CODE = re.compile(r"^[a-zA-Z0-9_.-]{1,64}$")
_SAFE_REQUEST_ID = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class ProjectCredential:
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        key = self.api_key
        if not key or key != key.strip() or len(key) > 512 or any(ord(character) < 32 for character in key):
            raise ValueError("Invalid project API key format.")


class YiQiaoRestClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{settings.api_url}/",
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
                pool=settings.connect_timeout_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "yiqiao-mcp/1.0.0"},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        credential: ProjectCredential,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"X-API-Key": credential.api_key}
        try:
            async with self._client.stream(
                method,
                path.lstrip("/"),
                headers=headers,
                json=json_body,
            ) as response:
                body = await self._read_limited(response)
        except httpx.TimeoutException:
            raise CompanionError(
                code="rest_timeout",
                message="The YiQiao REST API timed out.",
                status=504,
            ) from None
        except httpx.TransportError:
            raise CompanionError(
                code="rest_unavailable",
                message="The YiQiao REST API is unavailable.",
                status=503,
            ) from None

        payload = self._decode_json(body)
        if not 200 <= response.status_code < 300:
            raise self._response_error(response, payload, credential)
        if response.status_code == 204 or not body:
            return None
        if payload is None:
            raise CompanionError(
                code="invalid_rest_response",
                message="The YiQiao REST API returned an invalid response.",
                status=502,
            )
        return payload

    async def _read_limited(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self._settings.max_response_bytes:
            raise CompanionError(
                code="rest_response_too_large",
                message="The YiQiao REST API response exceeded the companion limit.",
                status=502,
            )
        chunks = bytearray()
        async for chunk in response.aiter_bytes():
            chunks.extend(chunk)
            if len(chunks) > self._settings.max_response_bytes:
                raise CompanionError(
                    code="rest_response_too_large",
                    message="The YiQiao REST API response exceeded the companion limit.",
                    status=502,
                )
        return bytes(chunks)

    @staticmethod
    def _decode_json(body: bytes) -> Any | None:
        if not body:
            return None
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _response_error(
        response: httpx.Response,
        payload: Any | None,
        credential: ProjectCredential,
    ) -> CompanionError:
        status = response.status_code
        code: str | None = None
        request_id: str | None = None
        if isinstance(payload, dict):
            detail = payload.get("detail")
            candidates = [
                detail.get("code") if isinstance(detail, dict) else None,
                payload.get("code"),
            ]
            code = next(
                (
                    value
                    for value in candidates
                    if isinstance(value, str) and _SAFE_CODE.fullmatch(value) and credential.api_key not in value
                ),
                None,
            )
            raw_request_id = payload.get("request_id")
            if (
                isinstance(raw_request_id, str)
                and _SAFE_REQUEST_ID.fullmatch(raw_request_id)
                and credential.api_key not in raw_request_id
            ):
                request_id = raw_request_id
        if request_id is None:
            raw_request_id = response.headers.get("x-request-id")
            if (
                raw_request_id
                and _SAFE_REQUEST_ID.fullmatch(raw_request_id)
                and credential.api_key not in raw_request_id
            ):
                request_id = raw_request_id

        retry_after: int | None = None
        raw_retry_after = response.headers.get("retry-after", "")
        if raw_retry_after.isdigit():
            retry_after = min(int(raw_retry_after), 86_400)

        default_code, message = _status_error(status)
        return CompanionError(
            code=code or default_code,
            message=message,
            status=status,
            request_id=request_id,
            retry_after=retry_after,
        )


def _status_error(status: int) -> tuple[str, str]:
    if status == 400:
        return "rest_invalid_request", "The YiQiao REST API rejected the request."
    if status == 401:
        return "rest_authentication_failed", "The project API key was rejected."
    if status == 403:
        return "rest_permission_denied", "The project API key does not permit this operation."
    if status == 404:
        return "memory_not_found", "The requested memory was not found."
    if status == 422:
        return "rest_validation_failed", "The YiQiao REST API rejected the request as invalid."
    if status == 429:
        return "rest_rate_limited", "The YiQiao REST API rate limit or quota was exceeded."
    if status in {502, 503, 504}:
        return "rest_unavailable", "The YiQiao REST API is temporarily unavailable."
    if 300 <= status < 400:
        return "rest_redirect_rejected", "The YiQiao REST API returned an unexpected redirect."
    return "rest_error", "The YiQiao REST API request failed."


@asynccontextmanager
async def rest_client_lifespan(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[YiQiaoRestClient]:
    client = YiQiaoRestClient(settings, transport=transport)
    try:
        yield client
    finally:
        await client.aclose()
