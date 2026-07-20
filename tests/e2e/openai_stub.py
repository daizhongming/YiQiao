"""Minimal deterministic OpenAI-compatible server for full-stack release tests."""

from __future__ import annotations

import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = os.environ.get("STUB_HOST", "0.0.0.0")
PORT = int(os.environ.get("STUB_PORT", "8080"))
DEFAULT_DIMENSIONS = int(os.environ.get("STUB_EMBEDDING_DIMS", "16"))


def _embedding(value: Any, dimensions: int) -> list[float]:
    """Return a stable, non-zero unit vector with high cross-query similarity."""
    material = json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    vector = [1.0 + (digest[index % len(digest)] / 4096.0) for index in range(dimensions)]
    magnitude = sum(component * component for component in vector) ** 0.5
    return [component / magnitude for component in vector]


class Handler(BaseHTTPRequestHandler):
    server_version = "YiQiaoE2EStub/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path in {"/health", "/v1/health"}:
            self._json(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "yiqiao-e2e", "object": "model", "owned_by": "yiqiao"},
                        {"id": "yiqiao-e2e-embedding", "object": "model", "owned_by": "yiqiao"},
                    ],
                },
            )
            return
        self._json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        payload = self._request_json()
        if self.path == "/v1/embeddings":
            dimensions = payload.get("dimensions", DEFAULT_DIMENSIONS)
            if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not 1 <= dimensions <= 4096:
                self._json(400, {"error": {"message": "Invalid dimensions", "type": "invalid_request_error"}})
                return
            values = payload.get("input", [])
            if not isinstance(values, list):
                values = [values]
            self._json(
                200,
                {
                    "object": "list",
                    "model": payload.get("model", "yiqiao-e2e-embedding"),
                    "data": [
                        {"object": "embedding", "index": index, "embedding": _embedding(value, dimensions)}
                        for index, value in enumerate(values)
                    ],
                    "usage": {"prompt_tokens": 0, "total_tokens": 0},
                },
            )
            return
        if self.path == "/v1/chat/completions":
            self._json(
                200,
                {
                    "id": "chatcmpl-yiqiao-e2e",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model", "yiqiao-e2e"),
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": '{"memory":[]}'},
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
            return
        self._json(404, {"error": {"message": "Not found", "type": "invalid_request_error"}})


if __name__ == "__main__":
    print(f"YiQiao E2E model stub listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
