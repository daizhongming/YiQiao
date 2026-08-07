from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from typing import Sequence

import anyio
import uvicorn

from yiqiao_mcp import __version__
from yiqiao_mcp.config import Settings, ToolProfile, Transport
from yiqiao_mcp.server import create_server
from yiqiao_mcp.transports import create_http_app, run_stdio


def _parser(defaults: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YiQiao REST-only MCP companion")
    parser.add_argument("--version", action="version", version=f"yiqiao-mcp {__version__}")
    parser.add_argument("--transport", choices=[item.value for item in Transport], default=defaults.transport.value)
    parser.add_argument("--profile", choices=[item.value for item in ToolProfile], default=defaults.profile.value)
    parser.add_argument("--api-url", default=defaults.api_url, help="YiQiao REST API base URL")
    parser.add_argument("--host", default=defaults.host, help="Streamable HTTP bind host")
    parser.add_argument("--port", type=int, default=defaults.port, help="Streamable HTTP bind port")
    parser.add_argument("--mcp-path", default=defaults.mcp_path, help="Streamable HTTP MCP path")
    parser.add_argument("--connect-timeout", type=float, default=defaults.connect_timeout_seconds)
    parser.add_argument("--request-timeout", type=float, default=defaults.request_timeout_seconds)
    parser.add_argument("--allowed-host", action="append", dest="allowed_hosts", help="Allowed Host value or host:*")
    parser.add_argument(
        "--allowed-origin", action="append", dest="allowed_origins", help="Allowed Origin value or origin:*"
    )
    parser.add_argument("--log-level", choices=["critical", "error", "warning", "info"], default="info")
    return parser


def build_settings(argv: Sequence[str] | None = None) -> tuple[Settings, str]:
    try:
        defaults = Settings.from_environment()
    except (TypeError, ValueError) as error:
        raise SystemExit(f"Invalid yiqiao-mcp environment configuration: {error}") from None
    parser = _parser(defaults)
    args = parser.parse_args(argv)
    try:
        settings = replace(
            defaults,
            api_url=args.api_url,
            profile=ToolProfile(args.profile),
            transport=Transport(args.transport),
            host=args.host,
            port=args.port,
            mcp_path=args.mcp_path,
            connect_timeout_seconds=args.connect_timeout,
            request_timeout_seconds=args.request_timeout,
            allowed_hosts=tuple(args.allowed_hosts) if args.allowed_hosts else defaults.allowed_hosts,
            allowed_origins=tuple(args.allowed_origins) if args.allowed_origins else defaults.allowed_origins,
        )
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    return settings, args.log_level


def main(argv: Sequence[str] | None = None) -> int:
    settings, log_level = build_settings(argv)
    server = create_server(settings)
    if settings.transport is Transport.STDIO:
        anyio.run(run_stdio, server)
        return 0

    app = create_http_app(server, settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=log_level,
        access_log=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
