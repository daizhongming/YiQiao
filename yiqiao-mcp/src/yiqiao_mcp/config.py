from __future__ import annotations

import math
import os
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit


class ToolProfile(str, Enum):
    READ_ONLY = "read-only"
    MEMORY = "memory"
    DESTRUCTIVE = "destructive"


class Transport(str, Enum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


DEFAULT_ALLOWED_HOSTS = (
    "127.0.0.1:*",
    "localhost:*",
    "[::1]:*",
    "yiqiao-mcp:*",
)
DEFAULT_ALLOWED_ORIGINS = (
    "http://127.0.0.1:*",
    "http://localhost:*",
    "http://[::1]:*",
)


def normalize_api_url(value: str) -> str:
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("YiQiao API URL must be an absolute http or https URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("YiQiao API URL must not contain credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("YiQiao API URL must not contain a query string or fragment.")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def parse_csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise ValueError("A configured allowlist cannot be empty.")
    return items


@dataclass(frozen=True)
class Settings:
    api_url: str = "http://127.0.0.1:8888"
    profile: ToolProfile = ToolProfile.MEMORY
    transport: Transport = Transport.STDIO
    host: str = "127.0.0.1"
    port: int = 8765
    mcp_path: str = "/mcp"
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 30.0
    max_response_bytes: int = 2 * 1024 * 1024
    max_http_body_bytes: int = 512 * 1024
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    allowed_origins: tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS
    stdio_api_key_env: str = "YIQIAO_API_KEY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_url", normalize_api_url(self.api_url))
        if not (1 <= self.port <= 65535):
            raise ValueError("MCP port must be between 1 and 65535.")
        if not self.mcp_path.startswith("/") or self.mcp_path == "/":
            raise ValueError("MCP path must start with '/' and cannot be '/'.")
        if self.mcp_path.endswith("/"):
            raise ValueError("MCP path must not end with '/'.")
        if not all(
            math.isfinite(value) and value > 0 for value in (self.connect_timeout_seconds, self.request_timeout_seconds)
        ):
            raise ValueError("HTTP timeouts must be positive finite numbers.")
        if self.max_response_bytes < 1024 or self.max_http_body_bytes < 1024:
            raise ValueError("HTTP size limits must be at least 1024 bytes.")
        if not self.allowed_hosts or not self.allowed_origins:
            raise ValueError("Host and Origin allowlists must not be empty.")

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            api_url=os.environ.get("YIQIAO_API_URL", cls.api_url),
            profile=ToolProfile(os.environ.get("YIQIAO_MCP_PROFILE", ToolProfile.MEMORY.value)),
            transport=Transport(os.environ.get("YIQIAO_MCP_TRANSPORT", Transport.STDIO.value)),
            host=os.environ.get("YIQIAO_MCP_HOST", cls.host),
            port=int(os.environ.get("YIQIAO_MCP_PORT", str(cls.port))),
            mcp_path=os.environ.get("YIQIAO_MCP_PATH", cls.mcp_path),
            connect_timeout_seconds=float(
                os.environ.get("YIQIAO_MCP_CONNECT_TIMEOUT", str(cls.connect_timeout_seconds))
            ),
            request_timeout_seconds=float(
                os.environ.get("YIQIAO_MCP_REQUEST_TIMEOUT", str(cls.request_timeout_seconds))
            ),
            max_response_bytes=int(os.environ.get("YIQIAO_MCP_MAX_RESPONSE_BYTES", str(cls.max_response_bytes))),
            max_http_body_bytes=int(os.environ.get("YIQIAO_MCP_MAX_BODY_BYTES", str(cls.max_http_body_bytes))),
            allowed_hosts=parse_csv(os.environ.get("YIQIAO_MCP_ALLOWED_HOSTS"), DEFAULT_ALLOWED_HOSTS),
            allowed_origins=parse_csv(os.environ.get("YIQIAO_MCP_ALLOWED_ORIGINS"), DEFAULT_ALLOWED_ORIGINS),
            stdio_api_key_env=os.environ.get("YIQIAO_MCP_STDIO_KEY_ENV", cls.stdio_api_key_env),
        )
