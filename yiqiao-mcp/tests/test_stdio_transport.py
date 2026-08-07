from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class _RestHandler(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.__class__.calls.append((self.headers.get("X-API-Key", ""), self.path, body))
        payload = json.dumps({"results": [{"id": "00000000-0000-4000-8000-000000000001", "memory": body["query"]}]})
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


@pytest.mark.asyncio
async def test_official_client_stdio_initialize_list_and_tool_call():
    _RestHandler.calls = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    companion_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(companion_root / "src"), env.get("PYTHONPATH", "")]))
    env["YIQIAO_API_KEY"] = "stdio-project-key"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "yiqiao_mcp",
            "--transport",
            "stdio",
            "--profile",
            "read-only",
            "--api-url",
            f"http://127.0.0.1:{httpd.server_port}",
        ],
        env=env,
        cwd=companion_root,
    )

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
            async with stdio_client(params, errlog=stderr) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    initialized = await session.initialize()
                    listed = await session.list_tools()
                    called = await session.call_tool(
                        "yiqiao_memory_search",
                        {"query": "stdio query", "user_id": "alice", "top_k": 2},
                    )
            stderr.seek(0)
            server_stderr = stderr.read()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert initialized.protocolVersion == "2025-11-25"
    assert [tool.name for tool in listed.tools] == [
        "yiqiao_memory_search",
        "yiqiao_memory_get",
        "yiqiao_memory_history",
    ]
    assert called.isError is False
    assert called.structuredContent["data"]["results"][0]["memory"] == "stdio query"
    assert "stdio-project-key" not in server_stderr
    assert _RestHandler.calls == [
        (
            "stdio-project-key",
            "/search",
            {"query": "stdio query", "filters": {"user_id": "alice"}, "top_k": 2},
        )
    ]
