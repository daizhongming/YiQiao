#!/usr/bin/env python3
"""Reproducible dataset manifests and API benchmarks for chat-memory imports."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import mimetypes
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPO_ROOT / "server"
MANIFEST_SCHEMA = "yiqiao.chat-import-dataset/v1"
TOKENIZER_NAME = "cl100k_base"
TOKENIZER_VERSION = "0.12.0"
MESSAGE_FRAMING_TOKENS = 6
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".md", ".markdown", ".mdx", ".txt"}
TERMINAL_JOB_STATUSES = {"cancelled", "completed", "completed_with_errors", "failed"}
SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "jwt_secret",
    "password",
    "refresh_token",
    "secret",
}
RELEVANT_FILES = (
    "scripts/chat_import_benchmark.py",
    "server/chat_import.py",
    "server/main.py",
    "server/import_repository.py",
    "mem0/memory/main.py",
    "mem0/memory/storage.py",
)
LAUNCH_ARTIFACTS = ("environment.json", "preflight.json", "run-config.json")
FINAL_RUN_ARTIFACTS = ("job.json", "errors.json", "memories.jsonl", "summary.json")
PROTECTED_FINAL_ARTIFACTS = (*FINAL_RUN_ARTIFACTS, "failure.json")


class BenchmarkError(RuntimeError):
    """A benchmark cannot proceed without compromising its evidence."""


class ManifestMismatch(BenchmarkError):
    """The frozen manifest does not describe the supplied input tree."""


@dataclass(frozen=True)
class ApiSettings:
    api_url: str
    api_key: str = ""
    project_id: str = "default-project"
    request_timeout_seconds: float = 180.0


@dataclass(frozen=True)
class RunSettings:
    phase: str
    run_id: str
    project_id: str
    entity_id: str
    input_root: Path
    manifest_path: Path
    output_root: Path
    workers: int = 3
    target_tokens: int = 5000
    max_tokens: int = 6000
    overlap_turns: int = 1
    fast_model: str = "gemini-2.5-flash"
    pro_model: str = "gemini-2.5-pro"
    audit_ratio: float = 0.07
    source_app: str = "auto"
    poll_seconds: float = 3.0
    run_timeout_seconds: float = 12 * 60 * 60
    compose_project: str = "yiqiao-v3"
    include_sensitive_artifacts: bool = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _jsonl_bytes(values: Iterable[Mapping[str, Any]]) -> bytes:
    return "".join(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for value in values
    ).encode("utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_json_bytes(value))
    temporary.replace(path)


def _write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(_jsonl_bytes(values))
    temporary.replace(path)


def _ensure_capture_targets_absent(output_dir: Path) -> None:
    if not output_dir.is_dir():
        raise BenchmarkError(f"Detached capture output must be an existing launch directory: {output_dir}")
    conflicts = [name for name in PROTECTED_FINAL_ARTIFACTS if (output_dir / name).exists()]
    if conflicts:
        raise BenchmarkError(f"Detached capture refuses to overwrite existing final artifacts: {', '.join(conflicts)}")


def _publish_exclusive(output_dir: Path, artifacts: Mapping[str, bytes]) -> None:
    """Atomically publish each final artifact without replacing any existing evidence."""
    _ensure_capture_targets_absent(output_dir)
    if set(artifacts) != set(FINAL_RUN_ARTIFACTS):
        raise BenchmarkError("Detached capture must publish the complete final artifact set.")

    temporary_paths: list[Path] = []
    published_paths: list[Path] = []
    try:
        for name, content in artifacts.items():
            temporary = output_dir / f".{name}.{uuid.uuid4().hex}.capture.tmp"
            temporary_paths.append(temporary)
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for name, temporary in zip(artifacts, temporary_paths):
            target = output_dir / name
            os.link(temporary, target)
            published_paths.append(target)
    except FileExistsError as exc:
        raise BenchmarkError(
            "Detached capture lost an exclusive-publication race; no final artifacts were kept."
        ) from exc
    except OSError as exc:
        raise BenchmarkError(f"Detached capture could not publish final artifacts atomically: {exc}") from exc
    finally:
        if len(published_paths) != len(artifacts):
            for path in published_paths:
                path.unlink(missing_ok=True)
        for path in temporary_paths:
            path.unlink(missing_ok=True)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError(f"Duplicate JSON key in manifest: {key}")
        result[key] = value
    return result


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"Could not read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkError(f"{description.capitalize()} must be a JSON object: {path}")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    value = _load_json_object(path, "dataset manifest")
    verify_manifest_integrity(value)
    return value


def verify_manifest_integrity(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise BenchmarkError(f"Unsupported dataset manifest schema: {manifest.get('schema')!r}")
    recorded = manifest.get("manifest_sha256")
    if not isinstance(recorded, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded):
        raise BenchmarkError("Dataset manifest has no valid manifest_sha256.")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    actual = _sha256_bytes(_canonical_json(body))
    if actual != recorded:
        raise ManifestMismatch(f"Manifest self-hash mismatch: expected {recorded}, calculated {actual}.")
    if not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise BenchmarkError("Dataset manifest contains no files.")


def _load_chat_import():
    server_path = str(SERVER_ROOT)
    if server_path not in sys.path:
        sys.path.insert(0, server_path)
    return importlib.import_module("chat_import")


def _tokenizer_bundle():
    try:
        version = importlib.metadata.version("tiktoken")
        tiktoken = importlib.import_module("tiktoken")
    except (importlib.metadata.PackageNotFoundError, ImportError) as exc:
        raise BenchmarkError(f"tiktoken {TOKENIZER_VERSION} is required to freeze benchmark manifests.") from exc
    if version != TOKENIZER_VERSION:
        raise BenchmarkError(
            f"Expected tiktoken {TOKENIZER_VERSION}, found {version}; refusing to create a non-comparable manifest."
        )
    return tiktoken.get_encoding(TOKENIZER_NAME), version


def _decode_source(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def _length_stratum(tokens: int) -> str:
    if tokens < 2_000:
        return "lt_2k"
    if tokens < 10_000:
        return "2k_to_10k"
    if tokens < 50_000:
        return "10k_to_50k"
    return "gte_50k"


def _input_files(input_root: Path) -> list[Path]:
    root = input_root.resolve()
    if not root.is_dir():
        raise BenchmarkError(f"Input root is not a directory: {input_root}")
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES]
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    if not files:
        raise BenchmarkError(f"No supported chat files found under {input_root}.")
    return files


def _dataset_sha256(files: Sequence[Mapping[str, Any]]) -> str:
    # This line-oriented identity intentionally depends only on raw inputs, not parser output.
    content = "".join(f"{item['path']}\t{item['bytes']}\t{item['sha256']}\n" for item in files)
    return _sha256_bytes(content.encode("utf-8"))


def build_manifest(input_root: Path, *, source_app: str = "auto") -> dict[str, Any]:
    root = input_root.resolve()
    chat_import = _load_chat_import()
    encoder, tokenizer_version = _tokenizer_bundle()
    records: list[dict[str, Any]] = []
    total_conversations = 0
    total_messages = 0
    total_tokens = 0
    total_bytes = 0
    total_inline_data = 0
    identity_records: list[dict[str, Any]] = []

    for path in _input_files(root):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        raw_text = _decode_source(raw)
        raw_inline_data = sum(1 for _ in chat_import.INLINE_BASE64_DATA_URI_RE.finditer(raw_text))
        try:
            conversations = chat_import.parse_file(path, source_app, relative)
        except Exception as exc:
            raise BenchmarkError(f"Parser failed for {relative}: {exc.__class__.__name__}: {exc}") from exc
        if not conversations:
            raise BenchmarkError(f"Parser produced no conversations for supported file: {relative}")

        conversation_records: list[dict[str, Any]] = []
        omitted_inline_data = 0
        for conversation in conversations:
            source_tokens = sum(
                len(encoder.encode(str(message.content), disallowed_special=())) + MESSAGE_FRAMING_TOKENS
                for message in conversation.messages
            )
            role_counts = Counter(str(message.role) for message in conversation.messages)
            omitted_inline_data += sum(
                str(message.content).count(chat_import.INLINE_DATA_PLACEHOLDER) for message in conversation.messages
            )
            conversation_records.append(
                {
                    "id_sha256": _sha256_bytes(str(conversation.id).encode("utf-8")),
                    "message_count": len(conversation.messages),
                    "role_counts": dict(sorted(role_counts.items())),
                    "source_app": str(conversation.source_app),
                    "source_tokens": source_tokens,
                }
            )

        if omitted_inline_data != raw_inline_data:
            raise BenchmarkError(
                f"Inline-data omission count mismatch for {relative}: raw={raw_inline_data}, parsed={omitted_inline_data}."
            )
        message_count = sum(item["message_count"] for item in conversation_records)
        source_tokens = sum(item["source_tokens"] for item in conversation_records)
        flags = ["inline_base64_data_uri"] if raw_inline_data else []
        if len(raw) >= 10 * 1024 * 1024:
            flags.append("large_file")
        raw_sha256 = _sha256_bytes(raw)
        identity_records.append({"bytes": len(raw), "path": relative, "sha256": raw_sha256})
        records.append(
            {
                "asset_flags": flags,
                "bytes": len(raw),
                "conversation_count": len(conversation_records),
                "conversations": conversation_records,
                "inline_base64_data_uri_count": raw_inline_data,
                "length_stratum": _length_stratum(source_tokens),
                "message_count": message_count,
                "path_depth": len(Path(*relative.split("/")).parts),
                "path_sha256": _sha256_bytes(relative.encode("utf-8")),
                "sha256": raw_sha256,
                "source_tokens": source_tokens,
                "suffix": path.suffix.lower(),
            }
        )
        total_conversations += len(conversation_records)
        total_messages += message_count
        total_tokens += source_tokens
        total_bytes += len(raw)
        total_inline_data += raw_inline_data

    parser_path = SERVER_ROOT / "chat_import.py"
    strata = Counter(item["length_stratum"] for item in records)
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "dataset_sha256": _dataset_sha256(identity_records),
        "files": records,
        "parse_options": {"source_app": source_app},
        "parser": {
            "module": "server/chat_import.py",
            "sha256": _sha256_file(parser_path),
        },
        "summary": {
            "bytes": total_bytes,
            "conversation_count": total_conversations,
            "file_count": len(records),
            "inline_base64_data_uri_count": total_inline_data,
            "length_strata": dict(sorted(strata.items())),
            "message_count": total_messages,
            "source_tokens": total_tokens,
        },
        "tokenizer": {
            "encoding": TOKENIZER_NAME,
            "package": "tiktoken",
            "per_message_framing_tokens": MESSAGE_FRAMING_TOKENS,
            "version": tokenizer_version,
        },
    }
    manifest["manifest_sha256"] = _sha256_bytes(_canonical_json(manifest))
    return manifest


def _manifest_difference(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> str:
    expected_files = {item.get("path_sha256"): item for item in expected.get("files", []) if isinstance(item, dict)}
    actual_files = {item.get("path_sha256"): item for item in actual.get("files", []) if isinstance(item, dict)}
    added = sorted(set(expected_files) - set(actual_files))
    removed = sorted(set(actual_files) - set(expected_files))
    changed = sorted(
        path for path in set(expected_files) & set(actual_files) if expected_files[path] != actual_files[path]
    )
    parts = []
    if added:
        parts.append(f"added={added[:5]}")
    if removed:
        parts.append(f"removed={removed[:5]}")
    if changed:
        parts.append(f"changed={changed[:5]}")
    if not parts:
        parts.append("parser, tokenizer, options, or aggregate metadata changed")
    return "; ".join(parts)


def validate_manifest(input_root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    verify_manifest_integrity(manifest)
    parse_options = manifest.get("parse_options")
    source_app = str(parse_options.get("source_app", "auto")) if isinstance(parse_options, dict) else "auto"
    current = build_manifest(input_root, source_app=source_app)
    if current != manifest:
        raise ManifestMismatch(f"Frozen dataset manifest mismatch: {_manifest_difference(current, manifest)}.")
    return {
        "dataset_sha256": manifest["dataset_sha256"],
        "file_count": manifest["summary"]["file_count"],
        "manifest_sha256": manifest["manifest_sha256"],
        "status": "valid",
    }


def _redact(value: Any, key: str | None = None) -> Any:
    normalized = (key or "").lower()
    sensitive = normalized in SENSITIVE_KEYS or normalized.endswith(("_api_key", "_password", "_secret"))
    if sensitive:
        return "[redacted]" if value else value
    if isinstance(value, dict):
        return {item_key: _redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


def _safe_api_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BenchmarkError("API URL must be an absolute http(s) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchmarkError("API URL must not contain credentials, query parameters, or fragments.")
    netloc = parsed.hostname
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "")).rstrip("/")


class ApiClient:
    def __init__(self, settings: ApiSettings, *, transport: httpx.BaseTransport | None = None):
        headers = {"Accept": "application/json", "X-Project-ID": settings.project_id}
        if settings.api_key:
            headers["X-API-Key"] = settings.api_key
        timeout = httpx.Timeout(
            settings.request_timeout_seconds,
            connect=min(30.0, settings.request_timeout_seconds),
        )
        self.settings = settings
        self.client = httpx.Client(
            base_url=_safe_api_url(settings.api_url),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise BenchmarkError(f"API request failed for {method} {path}: {exc.__class__.__name__}: {exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            try:
                body = response.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except (json.JSONDecodeError, UnicodeError):
                detail = "non-JSON response"
            safe_detail = json.dumps(_redact(detail), ensure_ascii=False, sort_keys=True)[:1000]
            raise BenchmarkError(f"API request {method} {path} returned HTTP {response.status_code}: {safe_detail}")
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise BenchmarkError(f"API request {method} {path} returned invalid JSON.") from exc


def _service_fingerprint(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        return {"configured": False}
    config = section.get("config") if isinstance(section.get("config"), dict) else {}
    base_urls = sorted(
        str(value).rstrip("/")
        for key, value in config.items()
        if (key == "base_url" or key.endswith("_base_url")) and value
    )
    return {
        "base_url_sha256": [_sha256_bytes(value.encode("utf-8")) for value in base_urls],
        "configured": True,
        "model": config.get("model"),
        "provider": section.get("provider"),
    }


def perform_preflight(
    client: ApiClient,
    *,
    models: Sequence[str] | None = None,
    require_runtime_model: str | None = None,
    model_route: str = "runtime",
) -> dict[str, Any]:
    if model_route not in {"runtime", "memory_import"}:
        raise BenchmarkError(f"Unsupported model preflight route: {model_route!r}.")
    health = client.request_json("GET", "/api/health")
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise BenchmarkError("API health preflight did not return status=ok.")
    configuration = client.request_json("GET", "/configure")
    if not isinstance(configuration, dict):
        raise BenchmarkError("Configuration preflight returned an invalid response.")
    workspace = client.request_json("GET", "/settings/workspace")
    if not isinstance(workspace, dict):
        raise BenchmarkError("Workspace-settings preflight returned an invalid response.")
    workspace_sections = {key: workspace.get(key) for key in ("categories", "extraction", "retention")}
    categories = workspace_sections.get("categories")
    extraction = workspace_sections.get("extraction")
    llm = configuration.get("llm")
    if not isinstance(llm, dict) or not llm.get("provider"):
        raise BenchmarkError("No configured LLM provider was returned by the API.")
    llm_config = llm.get("config") if isinstance(llm.get("config"), dict) else {}
    runtime_model = str(llm_config.get("model") or "").strip()
    expected_provider = str(llm.get("provider") or "").strip()
    if require_runtime_model and runtime_model != require_runtime_model:
        raise BenchmarkError(
            f"Configured runtime LLM model is {runtime_model!r}; this phase requires {require_runtime_model!r}."
        )
    selected_models = list(
        dict.fromkeys(str(model).strip() for model in (models or [runtime_model]) if str(model).strip())
    )
    if not selected_models:
        raise BenchmarkError("No LLM models were configured for preflight.")

    model_results = []
    for model in selected_models:
        result = client.request_json(
            "POST",
            "/configure/test",
            json={
                "kind": "llm",
                "provider": llm["provider"],
                "config": {"model": model},
                "route": model_route,
            },
        )
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise BenchmarkError(f"LLM preflight returned an invalid result for {model}.")
        if str(result.get("preview") or "").strip() != "YiQiao OK":
            raise BenchmarkError(f"LLM preflight for {model} did not follow the exact-response probe.")
        if result.get("route") != model_route:
            raise BenchmarkError(
                f"LLM preflight for {model} reported route {result.get('route')!r}; expected {model_route!r}."
            )
        if model_route == "memory_import":
            if result.get("route_configured") is not True or result.get("route_effective") is not True:
                raise BenchmarkError(
                    f"LLM preflight for {model} did not prove an explicitly configured, effective memory-import route."
                )
            route_provider = str(result.get("route_provider") or "").strip()
            if route_provider != expected_provider:
                raise BenchmarkError(
                    f"LLM preflight for {model} used provider {route_provider!r}; expected {expected_provider!r}."
                )
            route_model = str(result.get("route_model") or "").strip()
            if route_model != model:
                raise BenchmarkError(
                    f"LLM preflight for {model} reported effective model {route_model!r}; every requested model must be probed."
                )
            route_fingerprint = result.get("route_base_url_sha256")
            if not isinstance(route_fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", route_fingerprint) is None:
                raise BenchmarkError(
                    f"LLM preflight for {model} did not return a valid non-secret base URL fingerprint."
                )
            allowed_route_metadata = {
                "route",
                "route_base_url_sha256",
                "route_configured",
                "route_effective",
                "route_model",
                "route_provider",
            }
            unsafe_route_metadata = sorted(
                key for key in result if key.startswith("route_") and key not in allowed_route_metadata
            )
            if unsafe_route_metadata:
                raise BenchmarkError(
                    f"LLM preflight for {model} returned unsupported route metadata: {unsafe_route_metadata}."
                )
        model_results.append(
            {
                "latency_ms": result.get("latency_ms"),
                "model": model,
                "route": result.get("route"),
                "route_base_url_sha256": result.get("route_base_url_sha256"),
                "route_configured": result.get("route_configured"),
                "route_effective": result.get("route_effective"),
                "route_model": result.get("route_model"),
                "route_provider": result.get("route_provider"),
                "status": "ok",
            }
        )

    embedder = configuration.get("embedder")
    if not isinstance(embedder, dict) or not embedder.get("provider"):
        raise BenchmarkError("No configured embedder provider was returned by the API.")
    embedding_result = client.request_json(
        "POST",
        "/configure/test",
        json={"kind": "embedder", "provider": embedder["provider"], "config": {}},
    )
    if not isinstance(embedding_result, dict) or embedding_result.get("status") != "ok":
        raise BenchmarkError("Embedding preflight returned an invalid result.")
    dimensions = embedding_result.get("dimensions")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
        raise BenchmarkError("Embedding preflight did not return a positive vector dimension.")

    return {
        "api_health": "ok",
        "checked_at": _utc_now(),
        "configured_services": {
            "embedder": _service_fingerprint(embedder),
            "llm": _service_fingerprint(llm),
            "model_route": model_route,
        },
        "embedder": {"dimensions": dimensions, "latency_ms": embedding_result.get("latency_ms"), "status": "ok"},
        "models": model_results,
        "status": "ok",
        "workspace_settings": {
            "category_count": len(categories) if isinstance(categories, list) else 0,
            "extraction_field_count": len(extraction) if isinstance(extraction, dict) else 0,
            "sections_sha256": _sha256_bytes(_canonical_json(workspace_sections)),
        },
    }


def ensure_empty_project(client: ApiClient) -> dict[str, Any]:
    jobs = client.request_json("GET", "/memory-imports", params={"limit": 100})
    memories = client.request_json("GET", "/memories", params={"top_k": 1})
    job_total = int(jobs.get("total", 0)) if isinstance(jobs, dict) else -1
    memory_results = memories.get("results") if isinstance(memories, dict) else None
    if job_total < 0 or not isinstance(memory_results, list):
        raise BenchmarkError("Project-isolation preflight returned an invalid response.")
    if job_total or memory_results:
        raise BenchmarkError(
            f"Benchmark project is not empty (jobs={job_total}, memories={len(memory_results)}); use a fresh project."
        )
    return {"jobs": 0, "memories": 0, "status": "empty"}


def _run_import_options(settings: RunSettings) -> dict[str, Any]:
    tiered = settings.phase == "tiered"
    if settings.phase not in {"optimized_pro", "tiered"}:
        raise BenchmarkError(f"Unsupported API benchmark phase: {settings.phase}")
    return {
        "audit_ratio": settings.audit_ratio,
        "chunk_chars": 12000,
        "chunk_max_tokens": settings.max_tokens,
        "chunk_messages": 20,
        "chunk_overlap_turns": settings.overlap_turns,
        "chunk_target_tokens": settings.target_tokens,
        "entities": [{"id": settings.entity_id, "type": "user"}],
        "fallback_model": settings.pro_model,
        "fast_model": settings.fast_model,
        "infer": True,
        "model_tiering_enabled": tiered,
        "redact_secrets": True,
        "skip_duplicates": True,
        "source_app": settings.source_app,
        "workers": settings.workers,
    }


def _manifest_paths(input_root: Path, manifest: Mapping[str, Any]) -> list[tuple[Path, str]]:
    root = input_root.resolve()
    available: dict[str, tuple[Path, str]] = {}
    for candidate in _input_files(root):
        relative = candidate.relative_to(root).as_posix()
        path_sha256 = _sha256_bytes(relative.encode("utf-8"))
        if path_sha256 in available:
            raise BenchmarkError(f"Input path hash collision: {path_sha256}")
        available[path_sha256] = (candidate.resolve(), relative)
    paths: list[tuple[Path, str]] = []
    for item in manifest["files"]:
        path_sha256 = str(item["path_sha256"])
        matched = available.get(path_sha256)
        if matched is None:
            raise BenchmarkError(f"Manifest input is missing: path_sha256={path_sha256}")
        candidate, relative = matched
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise BenchmarkError(f"Manifest path escapes the input root: path_sha256={path_sha256}") from exc
        if not candidate.is_file():
            raise BenchmarkError(f"Manifest input is missing: path_sha256={path_sha256}")
        paths.append((candidate, relative))
    return paths


def _git_environment() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            return completed.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    head = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=all")
    return {"dirty": bool(status), "head": head, "status": status.splitlines() if status else []}


def _relevant_file_hashes() -> dict[str, str | None]:
    return {
        relative: _sha256_file(REPO_ROOT / relative) if (REPO_ROOT / relative).is_file() else None
        for relative in RELEVANT_FILES
    }


def _docker_images(compose_project: str) -> list[dict[str, Any]]:
    try:
        listed = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={compose_project}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.split()
        if not listed:
            return []
        inspected = subprocess.run(
            ["docker", "inspect", *listed],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        records = json.loads(inspected.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    result = []
    for record in records:
        labels = (record.get("Config") or {}).get("Labels") or {}
        result.append(
            {
                "container_id": str(record.get("Id") or "")[:12],
                "image": (record.get("Config") or {}).get("Image"),
                "image_id": record.get("Image"),
                "service": labels.get("com.docker.compose.service"),
            }
        )
    return sorted(result, key=lambda item: str(item.get("service") or ""))


def capture_environment(
    settings: RunSettings,
    api: ApiSettings,
    manifest: Mapping[str, Any],
    preflight: Mapping[str, Any],
) -> dict[str, Any]:
    packages = {}
    for package in ("httpx", "tiktoken"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {
        "api": {
            "api_key_fingerprint": _sha256_bytes(api.api_key.encode("utf-8"))[:12] if api.api_key else None,
            "api_key_present": bool(api.api_key),
            "url": _safe_api_url(api.api_url),
        },
        "captured_at": _utc_now(),
        "docker": {"compose_project": settings.compose_project, "images": _docker_images(settings.compose_project)},
        "git": _git_environment(),
        "manifest": {
            "dataset_sha256": manifest["dataset_sha256"],
            "file_sha256": _sha256_file(settings.manifest_path),
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "packages": packages,
        "platform": platform.platform(),
        "preflight_services": preflight.get("configured_services"),
        "python": sys.version,
        "relevant_file_sha256": _relevant_file_hashes(),
        "run": {
            "entity_id": settings.entity_id,
            "phase": settings.phase,
            "project_id": settings.project_id,
            "run_id": settings.run_id,
        },
    }


def _fetch_errors(client: ApiClient, job_id: str, expected_total: int) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    offset = 0
    while offset < expected_total or offset == 0:
        response = client.request_json(
            "GET", f"/memory-imports/{job_id}/errors", params={"limit": 500, "offset": offset}
        )
        page = response.get("results") if isinstance(response, dict) else None
        reported_total = response.get("total") if isinstance(response, dict) else None
        if not isinstance(page, list) or isinstance(reported_total, bool) or not isinstance(reported_total, int):
            raise BenchmarkError("Import error endpoint returned an invalid response.")
        if reported_total != expected_total:
            raise BenchmarkError(
                f"Import error endpoint total changed: expected {expected_total}, reported {reported_total}."
            )
        errors.extend(item for item in page if isinstance(item, dict))
        if len(page) < 500:
            break
        offset += len(page)
    return errors


def _strip_source_text(memory: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(memory)
    metadata = dict(result.get("metadata") or {}) if isinstance(result.get("metadata"), dict) else {}
    metadata.pop("source_messages", None)
    metadata.pop("messages", None)
    result["metadata"] = metadata
    return result


def _hash_optional(value: Any) -> str | None:
    text = str(value or "")
    return _sha256_bytes(text.encode("utf-8")) if text else None


def _artifact_error(error: Mapping[str, Any], *, include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(_redact(dict(error)))
    message = str(error.get("message") or "")
    return {
        "attempt": error.get("attempt"),
        "created_at": error.get("created_at"),
        "details_sha256": _sha256_bytes(_canonical_json(_redact(error.get("details"))))
        if error.get("details") is not None
        else None,
        "id": error.get("id"),
        "message_characters": len(message),
        "message_sha256": _hash_optional(message),
        "phase": error.get("phase"),
        "retryable": error.get("retryable"),
        "source_sha256": _hash_optional(error.get("source")),
        "type": error.get("type"),
    }


def _artifact_job(job: Mapping[str, Any], *, include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(_redact(dict(job)))
    result = dict(job)
    input_files = result.pop("input_files", [])
    result["input_file_count"] = len(input_files) if isinstance(input_files, list) else None
    result["input_file_sha256"] = (
        [_hash_optional(item) for item in input_files] if isinstance(input_files, list) else None
    )
    for key in ("current_file", "current_conversation"):
        value = result.pop(key, None)
        result[f"{key}_sha256"] = _hash_optional(value)
    graph_error = result.pop("graph_error", None)
    result["graph_error_sha256"] = _hash_optional(graph_error)
    errors = result.get("errors")
    if isinstance(errors, list):
        result["errors"] = [
            _artifact_error(item, include_sensitive=False) for item in errors if isinstance(item, Mapping)
        ]
    return dict(_redact(result))


def _artifact_memory(memory: Mapping[str, Any], *, include_sensitive: bool) -> dict[str, Any]:
    if include_sensitive:
        return dict(_redact(_strip_source_text(memory)))
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    text = str(memory.get("memory") or "")
    normalized_text = text.strip().casefold()
    categories = memory.get("categories") if isinstance(memory.get("categories"), list) else []

    def field(name: str) -> Any:
        return memory.get(name) if memory.get(name) is not None else metadata.get(name)

    attributed_to = field("attributed_to")
    safe_attribution = attributed_to if attributed_to in {None, "assistant", "system", "user"} else None
    source_path = str(field("source_path") or "").replace("\\", "/")

    return {
        "attributed_to": safe_attribution,
        "attributed_to_sha256": _hash_optional(attributed_to) if safe_attribution is None else None,
        "categories_sha256": sorted(filter(None, (_hash_optional(item) for item in categories))),
        "category_count": len(categories),
        "confidence": field("confidence"),
        "conversation_id_sha256": _hash_optional(field("conversation_id")),
        "core_source_message_indices": field("core_source_message_indices"),
        "id": memory.get("id"),
        "import_key": field("import_key"),
        "memory_characters": len(text),
        "memory_sha256": _sha256_bytes(text.encode("utf-8")),
        "normalized_memory_sha256": _sha256_bytes(normalized_text.encode("utf-8")),
        "source_app": field("source_app"),
        "source_message_indices": field("source_message_indices"),
        "source_path_sha256": _hash_optional(source_path),
    }


def _fetch_memories(client: ApiClient, entity_id: str) -> list[dict[str, Any]]:
    page = 1
    memories: list[dict[str, Any]] = []
    expected_total: int | None = None
    while True:
        response = client.request_json(
            "POST",
            "/memories/query",
            json={
                "filters": [{"entity_type": "user", "field": "entity", "value": entity_id}],
                "page": page,
                "page_size": 100,
            },
        )
        rows = response.get("results") if isinstance(response, dict) else None
        reported_total = response.get("total") if isinstance(response, dict) else None
        reported_pages = response.get("total_pages") if isinstance(response, dict) else None
        if (
            not isinstance(rows, list)
            or isinstance(reported_total, bool)
            or not isinstance(reported_total, int)
            or reported_total < 0
            or isinstance(reported_pages, bool)
            or not isinstance(reported_pages, int)
            or reported_pages < 0
        ):
            raise BenchmarkError("Memory query endpoint returned an invalid response.")
        if expected_total is None:
            expected_total = reported_total
        elif reported_total != expected_total:
            raise BenchmarkError("Memory query endpoint total changed during pagination.")
        memories.extend(_strip_source_text(item) for item in rows if isinstance(item, dict))
        if page >= reported_pages:
            break
        page += 1
    if len(memories) != expected_total:
        raise BenchmarkError(f"Memory query count mismatch: expected {expected_total}, received {len(memories)}.")
    return sorted(memories, key=lambda item: str(item.get("id") or ""))


def _parse_timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _job_elapsed_seconds(job: Mapping[str, Any], wall_seconds: float) -> float:
    started = _parse_timestamp(job.get("started_at") or job.get("created_at"))
    ended = _parse_timestamp(job.get("completed_at") or job.get("updated_at"))
    if started is not None and ended is not None and ended >= started:
        return ended - started
    return wall_seconds


def _conversation_limits(manifest: Mapping[str, Any]) -> tuple[dict[tuple[str, str], int], dict[str, set[int]]]:
    exact: dict[tuple[str, str], int] = {}
    by_id: dict[str, set[int]] = {}
    for file_record in manifest["files"]:
        source_path_sha256 = str(file_record["path_sha256"])
        for conversation in file_record["conversations"]:
            conversation_id_sha256 = str(conversation["id_sha256"])
            count = int(conversation["message_count"])
            exact[(source_path_sha256, conversation_id_sha256)] = count
            by_id.setdefault(conversation_id_sha256, set()).add(count)
    return exact, by_id


def _source_index_valid(memory: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    indices = memory.get("source_message_indices") or metadata.get("source_message_indices")
    if not isinstance(indices, list) or not indices:
        return False
    if any(isinstance(index, bool) or not isinstance(index, int) for index in indices):
        return False
    if len(indices) != len(set(indices)):
        return False
    source_path = str(memory.get("source_path") or metadata.get("source_path") or "").replace("\\", "/")
    conversation_id = str(memory.get("conversation_id") or metadata.get("conversation_id") or "")
    source_path_sha256 = _hash_optional(source_path) or ""
    conversation_id_sha256 = _hash_optional(conversation_id) or ""
    exact, by_id = _conversation_limits(manifest)
    limit = exact.get((source_path_sha256, conversation_id_sha256))
    if limit is None:
        candidates = by_id.get(conversation_id_sha256, set())
        if len(candidates) != 1:
            return False
        limit = next(iter(candidates))
    return all(0 <= index < limit for index in indices)


def build_summary(
    settings: RunSettings,
    manifest: Mapping[str, Any],
    job: Mapping[str, Any],
    memories: Sequence[Mapping[str, Any]],
    wall_seconds: float,
) -> dict[str, Any]:
    texts = [str(item.get("memory") or "").strip() for item in memories]
    normalized = [text.casefold() for text in texts if text]
    distinct = len(set(normalized))
    duplicates = max(0, len(normalized) - distinct)
    category_count = sum(bool(item.get("categories")) for item in memories)
    provenance_count = sum(_source_index_valid(item, manifest) for item in memories)
    elapsed = _job_elapsed_seconds(job, wall_seconds)
    source_tokens = int(manifest["summary"]["source_tokens"])
    valid_complete = (
        job.get("status") == "completed"
        and int(job.get("failed_chunks", 0)) == 0
        and int(job.get("parsed_files", 0)) == int(manifest["summary"]["file_count"])
        and int(job.get("total_conversations", 0)) == int(manifest["summary"]["conversation_count"])
        and int(job.get("total_chunks", 0)) > 0
        and int(job.get("processed_chunks", 0)) == int(job.get("total_chunks", 0))
        and int(job.get("memories_created", 0)) > 0
        and int(job.get("memories_created", 0)) == len(memories)
        and provenance_count == len(memories)
    )
    semantic_reason = (
        "No frozen, adjudicated gold/reference evaluation was supplied. "
        "Structural proxies do not establish semantic accuracy or recall."
    )
    operational_status = "accepted" if valid_complete else "rejected"
    overall_status = "not_evaluated" if valid_complete else "rejected"
    return {
        "acceptance": {
            "operational_completion": {
                "accepted": valid_complete,
                "status": operational_status,
            },
            "overall": {
                "accepted": False,
                "reason": semantic_reason if valid_complete else "Operational completion requirements were not met.",
                "status": overall_status,
            },
            "quality": {
                "accepted": False,
                "accuracy": None,
                "reason": semantic_reason,
                "recall": None,
                "status": "not_evaluated",
            },
        },
        "dataset": {
            "conversation_count": manifest["summary"]["conversation_count"],
            "dataset_sha256": manifest["dataset_sha256"],
            "file_count": manifest["summary"]["file_count"],
            "message_count": manifest["summary"]["message_count"],
            "source_tokens": source_tokens,
        },
        "job": {
            "failed_chunks": int(job.get("failed_chunks", 0)),
            "id": job.get("id"),
            "memories_created": int(job.get("memories_created", 0)),
            "retry_count": int(job.get("retry_count", 0)),
            "status": job.get("status"),
            "total_chunks": int(job.get("total_chunks", 0)),
        },
        "quality_proxies": {
            "category_field_coverage": category_count / len(memories) if memories else None,
            "distinct_memory_texts": distinct,
            "exact_duplicate_rate": duplicates / len(normalized) if normalized else None,
            "exact_duplicate_rows": duplicates,
            "memory_count": len(memories),
            "semantic_evaluation": {
                "accuracy": None,
                "reason": semantic_reason,
                "recall": None,
                "status": "not_evaluated",
            },
            "source_index_structure_valid_count": provenance_count,
            "source_index_structure_validity": provenance_count / len(memories) if memories else None,
        },
        "run": {
            "elapsed_seconds": round(elapsed, 3),
            "phase": settings.phase,
            "project_id": settings.project_id,
            "run_id": settings.run_id,
            "source_tokens_per_minute": round(source_tokens / elapsed * 60, 3)
            if valid_complete and elapsed > 0
            else None,
            "operationally_complete": valid_complete,
            "valid_complete_run": valid_complete,
            "wall_seconds": round(wall_seconds, 3),
        },
    }


def _detached_launch_settings(
    api: ApiSettings,
    output_dir: Path,
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> tuple[RunSettings, dict[str, str], dict[str, Any], dict[str, Any]]:
    missing = [name for name in LAUNCH_ARTIFACTS if not (output_dir / name).is_file()]
    if missing:
        raise BenchmarkError(f"Detached capture requires frozen launch artifacts; missing: {', '.join(missing)}")
    launch_hashes = {name: _sha256_file(output_dir / name) for name in LAUNCH_ARTIFACTS}
    environment = _load_json_object(output_dir / "environment.json", "launch environment")
    preflight = _load_json_object(output_dir / "preflight.json", "launch preflight")
    run_config = _load_json_object(output_dir / "run-config.json", "launch run config")

    policy = run_config.get("artifact_policy") if isinstance(run_config.get("artifact_policy"), Mapping) else {}
    if policy.get("include_sensitive_artifacts") is not False or policy.get("source_messages_included") is not False:
        raise BenchmarkError("Detached capture requires the sanitized launch artifact policy.")

    configured_api = run_config.get("api") if isinstance(run_config.get("api"), Mapping) else {}
    if configured_api.get("project_id") != api.project_id:
        raise BenchmarkError("Explicit project ID does not match the frozen run config.")
    if _safe_api_url(str(configured_api.get("api_url") or "")) != _safe_api_url(api.api_url):
        raise BenchmarkError("Explicit API URL does not match the frozen run config.")

    environment_manifest = environment.get("manifest") if isinstance(environment.get("manifest"), Mapping) else {}
    expected_manifest = {
        "dataset_sha256": manifest.get("dataset_sha256"),
        "file_sha256": _sha256_file(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
    }
    if dict(environment_manifest) != expected_manifest:
        raise BenchmarkError("Explicit manifest does not match the frozen launch environment identity.")

    run = environment.get("run") if isinstance(environment.get("run"), Mapping) else {}
    options = run_config.get("options") if isinstance(run_config.get("options"), Mapping) else {}
    entities = options.get("entities")
    if not isinstance(entities, list) or len(entities) != 1 or not isinstance(entities[0], Mapping):
        raise BenchmarkError("Frozen run config must contain exactly one benchmark entity.")
    entity = entities[0]
    if entity.get("type") != "user" or not isinstance(entity.get("id"), str) or not entity["id"]:
        raise BenchmarkError("Frozen run config must contain one non-empty user entity.")

    phase = str(run.get("phase") or "")
    run_id = str(run.get("run_id") or "")
    if phase not in {"optimized_pro", "tiered"} or not run_id:
        raise BenchmarkError("Frozen launch environment has an invalid phase or run ID.")
    if str(run.get("project_id") or "") != api.project_id or str(run.get("entity_id") or "") != entity["id"]:
        raise BenchmarkError("Frozen launch environment and run config disagree on project or entity identity.")
    if output_dir.name != run_id:
        raise BenchmarkError("Explicit output directory name does not match the frozen run ID.")
    if bool(options.get("model_tiering_enabled")) != (phase == "tiered"):
        raise BenchmarkError("Frozen run phase and model-tiering option disagree.")
    parse_options = manifest.get("parse_options") if isinstance(manifest.get("parse_options"), Mapping) else {}
    if str(options.get("source_app") or "auto") != str(parse_options.get("source_app", "auto")):
        raise BenchmarkError("Frozen run source application does not match the manifest.")
    project_isolation = (
        preflight.get("project_isolation") if isinstance(preflight.get("project_isolation"), Mapping) else {}
    )
    if preflight.get("status") != "ok" or project_isolation.get("status") != "empty":
        raise BenchmarkError("Frozen preflight does not prove a healthy, initially empty benchmark project.")

    docker = environment.get("docker") if isinstance(environment.get("docker"), Mapping) else {}
    settings = RunSettings(
        phase=phase,
        run_id=run_id,
        project_id=api.project_id,
        entity_id=str(entity["id"]),
        input_root=Path("."),
        manifest_path=manifest_path,
        output_root=output_dir.parent,
        workers=int(options.get("workers", 3)),
        target_tokens=int(options.get("chunk_target_tokens", 5000)),
        max_tokens=int(options.get("chunk_max_tokens", 6000)),
        overlap_turns=int(options.get("chunk_overlap_turns", 1)),
        fast_model=str(options.get("fast_model") or "gemini-2.5-flash"),
        pro_model=str(options.get("fallback_model") or "gemini-2.5-pro"),
        audit_ratio=float(options.get("audit_ratio", 0.07)),
        source_app=str(options.get("source_app") or "auto"),
        compose_project=str(docker.get("compose_project") or "yiqiao-v3"),
        include_sensitive_artifacts=False,
    )
    return settings, launch_hashes, environment, dict(options)


def _validate_detached_job(
    job: Any,
    job_id: str,
    settings: RunSettings,
    options: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise BenchmarkError("Detached job lookup returned an invalid response.")
    if str(job.get("id") or "") != job_id:
        raise BenchmarkError("Detached job lookup returned a different job ID.")
    if job.get("project_id") not in {None, settings.project_id}:
        raise BenchmarkError("Detached job lookup returned a different project ID.")
    if job.get("status") != "completed":
        raise BenchmarkError(f"Detached capture requires status=completed; found {job.get('status')!r}.")
    if bool(job.get("cancel_requested")):
        raise BenchmarkError("Detached capture refuses a job with cancellation requested.")

    manifest_summary = manifest.get("summary") if isinstance(manifest.get("summary"), Mapping) else {}
    expected_files = int(manifest_summary.get("file_count", 0))
    expected_conversations = int(manifest_summary.get("conversation_count", 0))
    expected_path_hashes = {
        str(file_record.get("path_sha256"))
        for file_record in manifest.get("files", [])
        if isinstance(file_record, Mapping) and file_record.get("path_sha256")
    }
    input_files = job.get("input_files")
    if not isinstance(input_files, list):
        raise BenchmarkError("Detached job response has no input-file identity.")
    normalized_paths = []
    for value in input_files:
        path = str(value).replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise BenchmarkError("Detached job response contains an invalid input path.")
        normalized_paths.append(path)
    observed_path_hashes = {_sha256_bytes(path.encode("utf-8")) for path in normalized_paths}
    expected_entities = {"user_id": settings.entity_id}
    identity_matches = (
        len(normalized_paths) == expected_files
        and observed_path_hashes == expected_path_hashes
        and job.get("entities") == expected_entities
        and str(job.get("source_app") or "") == settings.source_app
        and bool(job.get("infer")) == bool(options.get("infer", True))
        and int(job.get("configured_workers", 0)) == settings.workers
        and int(job.get("total_input_files", 0)) == expected_files
    )
    if not identity_matches:
        raise BenchmarkError("Detached job identity does not match the frozen manifest and run config.")

    total_chunks = int(job.get("total_chunks", 0))
    clean = (
        int(job.get("failed_chunks", 0)) == 0
        and int(job.get("parsed_files", 0)) == expected_files
        and int(job.get("total_conversations", 0)) == expected_conversations
        and total_chunks > 0
        and int(job.get("processed_chunks", 0)) == total_chunks
        and int(job.get("memories_created", 0)) > 0
    )
    if not clean:
        raise BenchmarkError("Detached completed job does not satisfy clean completion counters.")
    return job


def _detached_wall_seconds(environment: Mapping[str, Any], job: Mapping[str, Any]) -> float:
    started = _parse_timestamp(environment.get("captured_at"))
    ended = _parse_timestamp(job.get("completed_at") or job.get("updated_at"))
    if started is not None and ended is not None and ended >= started:
        return ended - started
    return _job_elapsed_seconds(job, 0.0)


def capture_existing_run(
    api: ApiSettings,
    job_id: str,
    output_dir: Path,
    manifest_path: Path,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Observe a completed job without mutating import, memory, vector, or graph state.

    The normal API authentication, quota checks, and request-audit logging still apply.
    """
    output_dir = output_dir.resolve()
    manifest_path = manifest_path.resolve()
    _ensure_capture_targets_absent(output_dir)
    manifest = load_manifest(manifest_path)
    settings, launch_hashes, environment, options = _detached_launch_settings(api, output_dir, manifest_path, manifest)

    with ApiClient(api, transport=transport) as client:
        job = _validate_detached_job(
            client.request_json("GET", f"/memory-imports/{job_id}"),
            job_id,
            settings,
            options,
            manifest,
        )
        expected_errors = int(job.get("error_count", 0))
        errors = _fetch_errors(client, job_id, expected_errors)
        if len(errors) != expected_errors:
            raise BenchmarkError(
                f"Detached error capture count mismatch: expected {expected_errors}, received {len(errors)}."
            )
        memories = _fetch_memories(client, settings.entity_id)

    for memory in memories:
        metadata = memory.get("metadata") if isinstance(memory.get("metadata"), Mapping) else {}
        observed_project = memory.get("project_id") or metadata.get("project_id")
        observed_user = memory.get("user_id") or metadata.get("user_id")
        if observed_project not in {None, api.project_id} or observed_user not in {None, settings.entity_id}:
            raise BenchmarkError("Detached memory query returned data outside the frozen project/entity scope.")

    wall_seconds = _detached_wall_seconds(environment, job)
    summary = build_summary(settings, manifest, job, memories, wall_seconds)
    if not summary["acceptance"]["operational_completion"]["accepted"]:
        raise BenchmarkError("Detached capture did not meet complete-run coverage requirements.")

    current_launch_hashes = {name: _sha256_file(output_dir / name) for name in LAUNCH_ARTIFACTS}
    if current_launch_hashes != launch_hashes:
        raise BenchmarkError("Frozen launch artifacts changed during detached capture.")

    job_artifact = _artifact_job(job, include_sensitive=False)
    error_artifact = {
        "results": [_artifact_error(item, include_sensitive=False) for item in errors],
        "total": len(errors),
    }
    memory_artifacts = [_artifact_memory(item, include_sensitive=False) for item in memories]
    _publish_exclusive(
        output_dir,
        {
            "job.json": _json_bytes(job_artifact),
            "errors.json": _json_bytes(error_artifact),
            "memories.jsonl": _jsonl_bytes(memory_artifacts),
            "summary.json": _json_bytes(summary),
        },
    )
    return {
        "capture_mode": "detached_domain_read_only",
        "dataset_sha256": manifest["dataset_sha256"],
        "job_id": job_id,
        "output": str(output_dir),
        "project_id": api.project_id,
        "summary": summary,
    }


def run_once(
    settings: RunSettings,
    api: ApiSettings,
    manifest: Mapping[str, Any],
    preflight: Mapping[str, Any],
    isolation: Mapping[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    output_dir = settings.output_root / settings.run_id
    if output_dir.exists():
        raise BenchmarkError(f"Run output already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    options = _run_import_options(settings)
    _write_json(output_dir / "environment.json", capture_environment(settings, api, manifest, preflight))
    _write_json(output_dir / "preflight.json", {**dict(preflight), "project_isolation": dict(isolation)})
    _write_json(
        output_dir / "run-config.json",
        {
            "api": asdict(api) | {"api_key": "[redacted]"},
            "artifact_policy": {
                "include_sensitive_artifacts": settings.include_sensitive_artifacts,
                "source_messages_included": False,
            },
            "options": options,
        },
    )

    started = time.perf_counter()
    job_id: str | None = None
    try:
        with ApiClient(api, transport=transport) as client, ExitStack() as stack:
            upload_files = []
            for path, relative in _manifest_paths(settings.input_root, manifest):
                handle = stack.enter_context(path.open("rb"))
                content_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
                upload_files.append(("files", (relative, handle, content_type)))
            response = client.request_json(
                "POST",
                "/memory-imports",
                data={"options": json.dumps(options, ensure_ascii=False, separators=(",", ":"))},
                files=upload_files,
            )
            if not isinstance(response, dict) or not response.get("id"):
                raise BenchmarkError("Import creation returned an invalid response.")
            job_id = str(response["id"])
            job = response
            deadline = time.monotonic() + settings.run_timeout_seconds
            last_progress: tuple[Any, ...] | None = None
            while job.get("status") not in TERMINAL_JOB_STATUSES:
                if time.monotonic() >= deadline:
                    try:
                        client.request_json("POST", f"/memory-imports/{job_id}/cancel")
                    except BenchmarkError:
                        pass
                    raise BenchmarkError(f"Import {job_id} exceeded the run timeout and cancellation was requested.")
                progress = (job.get("status"), job.get("phase"), job.get("processed_chunks"), job.get("total_chunks"))
                if progress != last_progress:
                    print(
                        f"[{settings.run_id}] status={progress[0]} phase={progress[1]} "
                        f"chunks={progress[2]}/{progress[3]}",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_progress = progress
                time.sleep(settings.poll_seconds)
                job = client.request_json("GET", f"/memory-imports/{job_id}")
                if not isinstance(job, dict):
                    raise BenchmarkError("Import polling returned an invalid response.")

            wall_seconds = time.perf_counter() - started
            errors = _fetch_errors(client, job_id, int(job.get("error_count", 0)))
            memories = _fetch_memories(client, settings.entity_id)

        _write_json(
            output_dir / "job.json",
            _artifact_job(job, include_sensitive=settings.include_sensitive_artifacts),
        )
        _write_json(
            output_dir / "errors.json",
            {
                "results": [
                    _artifact_error(item, include_sensitive=settings.include_sensitive_artifacts) for item in errors
                ],
                "total": len(errors),
            },
        )
        _write_jsonl(
            output_dir / "memories.jsonl",
            [_artifact_memory(item, include_sensitive=settings.include_sensitive_artifacts) for item in memories],
        )
        summary = build_summary(settings, manifest, job, memories, wall_seconds)
        _write_json(output_dir / "summary.json", summary)
        if not summary["acceptance"]["operational_completion"]["accepted"]:
            raise BenchmarkError(
                f"Import {job_id} did not meet complete-run coverage requirements; inspect {output_dir / 'summary.json'}."
            )
        return {"job_id": job_id, "output": str(output_dir), "summary": summary}
    except BaseException as exc:
        error_text = str(exc)[:2000]
        failure = {
            "error": error_text if settings.include_sensitive_artifacts else None,
            "error_characters": len(error_text),
            "error_sha256": _hash_optional(error_text),
            "error_type": exc.__class__.__name__,
            "failed_at": _utc_now(),
            "job_id": job_id,
        }
        _write_json(output_dir / "failure.json", _redact(failure))
        raise


def _api_settings(args: argparse.Namespace, project_id: str) -> ApiSettings:
    api_key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    return ApiSettings(
        api_url=args.api_url,
        api_key=api_key,
        project_id=project_id,
        request_timeout_seconds=args.request_timeout_seconds,
    )


def _new_run_id(phase: str, repeat_index: int, explicit: str | None) -> str:
    if explicit:
        base = explicit
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"{phase}-{stamp}-{uuid.uuid4().hex[:8]}"
    return f"{base}-r{repeat_index:02d}" if repeat_index > 1 else base


def _validate_run_args(args: argparse.Namespace) -> None:
    if args.repeat < 1 or args.repeat > 9:
        raise BenchmarkError("--repeat must be between 1 and 9.")
    if args.project_id and args.repeat != 1:
        raise BenchmarkError("An explicit --project-id can only be used with --repeat 1.")
    if not 1 <= args.workers <= 4:
        raise BenchmarkError("--workers must be between 1 and 4.")
    if not 4000 <= args.target_tokens <= args.max_tokens <= 6000:
        raise BenchmarkError("Token limits must satisfy 4000 <= target <= max <= 6000.")
    if args.overlap_turns not in {0, 1, 2}:
        raise BenchmarkError("--overlap-turns must be 0, 1, or 2.")
    if not 0.05 <= args.audit_ratio <= 0.10:
        raise BenchmarkError("--audit-ratio must be between 0.05 and 0.10.")
    identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
    if args.run_id and (len(args.run_id) > 80 or not identifier.fullmatch(args.run_id)):
        raise BenchmarkError("--run-id must be 1-80 ASCII letters, numbers, dots, underscores, or hyphens.")
    if args.project_id and (len(args.project_id) > 128 or not identifier.fullmatch(args.project_id)):
        raise BenchmarkError("--project-id must be 1-128 ASCII letters, numbers, dots, underscores, or hyphens.")


def command_manifest(args: argparse.Namespace) -> dict[str, Any]:
    manifest = build_manifest(args.input, source_app=args.source_app)
    _write_json(args.out, manifest)
    return {
        "dataset_sha256": manifest["dataset_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "output": str(args.out),
        "summary": manifest["summary"],
    }


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    return validate_manifest(args.input, load_manifest(args.manifest))


def command_preflight(args: argparse.Namespace) -> dict[str, Any]:
    api = _api_settings(args, args.project_id)
    with ApiClient(api) as client:
        result = perform_preflight(client, models=args.models, model_route=args.model_route)
    if args.out:
        _write_json(args.out, result)
    return result


def command_capture(args: argparse.Namespace) -> dict[str, Any]:
    identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
    if len(args.project_id) > 128 or not identifier.fullmatch(args.project_id):
        raise BenchmarkError("--project-id must be 1-128 ASCII letters, numbers, dots, underscores, or hyphens.")
    if len(args.job_id) > 128 or not identifier.fullmatch(args.job_id):
        raise BenchmarkError("--job-id must be 1-128 ASCII letters, numbers, dots, underscores, or hyphens.")
    api = _api_settings(args, args.project_id)
    return capture_existing_run(api, args.job_id, args.output, args.manifest)


def command_run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_run_args(args)
    manifest = load_manifest(args.manifest)
    validate_manifest(args.input, manifest)

    descriptors: list[tuple[RunSettings, ApiSettings]] = []
    for repeat_index in range(1, args.repeat + 1):
        run_id = _new_run_id(args.phase, repeat_index, args.run_id)
        project_id = args.project_id or f"benchmark-{run_id}"
        entity_id = f"benchmark-user-{manifest['dataset_sha256'][:16]}"
        settings = RunSettings(
            phase=args.phase,
            run_id=run_id,
            project_id=project_id,
            entity_id=entity_id,
            input_root=args.input.resolve(),
            manifest_path=args.manifest.resolve(),
            output_root=args.out.resolve(),
            workers=args.workers,
            target_tokens=args.target_tokens,
            max_tokens=args.max_tokens,
            overlap_turns=args.overlap_turns,
            fast_model=args.fast_model,
            pro_model=args.pro_model,
            audit_ratio=args.audit_ratio,
            source_app=str(manifest.get("parse_options", {}).get("source_app", "auto")),
            poll_seconds=args.poll_seconds,
            run_timeout_seconds=args.run_timeout_seconds,
            compose_project=args.compose_project,
            include_sensitive_artifacts=args.include_sensitive_artifacts,
        )
        descriptors.append((settings, _api_settings(args, project_id)))

    models = [args.pro_model] if args.phase == "optimized_pro" else [args.fast_model, args.pro_model]
    first_settings, first_api = descriptors[0]
    require_runtime = args.pro_model if args.phase == "optimized_pro" else None
    with ApiClient(first_api) as client:
        preflight = perform_preflight(
            client,
            models=models,
            require_runtime_model=require_runtime,
            model_route="memory_import" if args.phase == "tiered" else "runtime",
        )

    # Every isolation check is completed before the first output directory is created.
    isolations: list[dict[str, Any]] = []
    for _settings, api in descriptors:
        with ApiClient(api) as client:
            isolations.append(ensure_empty_project(client))

    results = [
        run_once(settings, api, manifest, preflight, isolation)
        for (settings, api), isolation in zip(descriptors, isolations)
    ]
    return {
        "dataset_sha256": manifest["dataset_sha256"],
        "phase": first_settings.phase,
        "results": results,
        "status": "completed",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="Create a deterministic, text-free dataset manifest.")
    manifest.add_argument("--input", type=Path, required=True, help="Directory containing the frozen chat files.")
    manifest.add_argument("--out", type=Path, required=True, help="Manifest JSON path.")
    manifest.add_argument("--source-app", default="auto", help="Parser source application override.")
    manifest.set_defaults(handler=command_manifest)

    validate = subparsers.add_parser("validate", help="Recompute and validate a frozen dataset manifest.")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.set_defaults(handler=command_validate)

    def add_api_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--api-url", default=os.environ.get("YIQIAO_API_URL", "http://localhost:8888"))
        command.add_argument(
            "--api-key-env",
            default="YIQIAO_API_KEY",
            help="Environment variable containing an admin-capable API key; the key is never written.",
        )
        command.add_argument("--request-timeout-seconds", type=float, default=180.0)

    preflight = subparsers.add_parser("preflight", help="Probe the configured API, LLM models, and embedder.")
    add_api_arguments(preflight)
    preflight.add_argument("--models", nargs="+", help="LLM model IDs to probe; defaults to the runtime model.")
    preflight.add_argument("--model-route", choices=("runtime", "memory_import"), default="runtime")
    preflight.add_argument("--project-id", default="default-project")
    preflight.add_argument("--out", type=Path, help="Optional JSON output; written only after all probes pass.")
    preflight.set_defaults(handler=command_preflight)

    capture = subparsers.add_parser(
        "capture",
        help=(
            "Capture final sanitized artifacts without mutating import or memory domain state; "
            "normal API request auditing still applies."
        ),
    )
    capture.add_argument("--api-url", required=True, help="Explicit API URL; must match the frozen run config.")
    capture.add_argument(
        "--api-key-env",
        default="YIQIAO_API_KEY",
        help="Environment variable containing an API key; the key is never written.",
    )
    capture.add_argument("--request-timeout-seconds", type=float, default=180.0)
    capture.add_argument("--project-id", required=True)
    capture.add_argument("--job-id", required=True)
    capture.add_argument("--output", type=Path, required=True, help="Existing directory containing launch artifacts.")
    capture.add_argument("--manifest", type=Path, required=True)
    capture.set_defaults(handler=command_capture)

    run = subparsers.add_parser("run", help="Run an isolated current-API benchmark after mandatory preflight.")
    add_api_arguments(run)
    run.add_argument("--phase", choices=("optimized_pro", "tiered"), required=True)
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--out", type=Path, default=Path("output/chat-import-benchmark"))
    run.add_argument("--run-id", help="Stable run label; a timestamped label is generated when omitted.")
    run.add_argument("--project-id", help="Fresh empty project; generated uniquely when omitted.")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--workers", type=int, default=3)
    run.add_argument("--target-tokens", type=int, default=5000)
    run.add_argument("--max-tokens", type=int, default=6000)
    run.add_argument("--overlap-turns", type=int, default=1)
    run.add_argument("--fast-model", default="gemini-2.5-flash")
    run.add_argument("--pro-model", default="gemini-2.5-pro")
    run.add_argument("--audit-ratio", type=float, default=0.07)
    run.add_argument("--poll-seconds", type=float, default=3.0)
    run.add_argument("--run-timeout-seconds", type=float, default=12 * 60 * 60)
    run.add_argument("--compose-project", default="yiqiao-v3")
    run.add_argument(
        "--include-sensitive-artifacts",
        action="store_true",
        help="Persist raw extracted memory and error text; source chat messages remain omitted.",
    )
    run.set_defaults(handler=command_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
    except (BenchmarkError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
