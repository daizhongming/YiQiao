from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from yiqiao_mcp.config import Settings


class _StreamableHTTPApp:
    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self._session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._session_manager.handle_request(scope, receive, send)


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self._app(scope, receive, send)
            return

        headers = [(key.lower(), value) for key, value in scope.get("headers", [])]
        content_lengths = [value for key, value in headers if key == b"content-length"]
        if any(value.isdigit() and int(value) > self._max_bytes for value in content_lengths):
            await PlainTextResponse("Request body too large.", status_code=413)(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self._max_bytes:
                await PlainTextResponse("Request body too large.", status_code=413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        credentials = [value for key, value in headers if key == b"x-api-key" and value]
        if any(credential in body for credential in credentials):
            await PlainTextResponse("Invalid MCP request.", status_code=400)(scope, receive, send)
            return

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay_receive, send)


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "yiqiao-mcp"})


def create_http_app(server: Server[Any, Request], settings: Settings) -> Starlette:
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.allowed_hosts),
        allowed_origins=list(settings.allowed_origins),
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=security,
    )
    mcp_app = RequestBodyLimitMiddleware(_StreamableHTTPApp(manager), settings.max_http_body_bytes)

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with manager.run():
            yield

    return Starlette(
        routes=[
            Route("/healthz", endpoint=_health, methods=["GET"]),
            Route(settings.mcp_path, endpoint=mcp_app, methods=["GET", "POST", "DELETE"]),
        ],
        lifespan=lifespan,
    )


async def run_stdio(server: Server[Any, Any]) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
