"""Chat history parsing and background import jobs for YiQiao."""

from __future__ import annotations

import hashlib
import json
import math
import queue
import random
import re
import shutil
import statistics
import tarfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from import_repository import ImportActiveJobLimitExceeded, ImportLeaseLost

SUPPORTED_SUFFIXES = {".json", ".jsonl", ".md", ".markdown", ".mdx", ".txt"}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_FILES = 5000
MAX_JOB_ERRORS = 50
IMPORT_STORE_MAX_ATTEMPTS = 3
IMPORT_RETRY_BASE_SECONDS = 1.0
COMPLEX_CHUNK_MIN_TOKENS = 5900
COMPLEX_TABLE_MIN_ROWS = 30
COMPLEX_TABLE_MIN_PIPES = 100
COMPLEX_CODE_MIN_LINES = 120
COMPLEX_TABBED_MIN_LINES = 60
COMPLEX_MIXED_TABLE_MIN_ROWS = 15
COMPLEX_MIXED_CODE_MIN_LINES = 60
DEFAULT_MAX_SPLIT_DEPTH = 5
TIMEOUT_SPLIT_ATTEMPTS = 2
TIMEOUT_SPLIT_MIN_TOKENS = 2000
LEGACY_IMPORT_KEY_SCHEMA_VERSION = 1
CURRENT_IMPORT_KEY_SCHEMA_VERSION = 2
SUPPORTED_IMPORT_KEY_SCHEMA_VERSIONS = {
    LEGACY_IMPORT_KEY_SCHEMA_VERSION,
    CURRENT_IMPORT_KEY_SCHEMA_VERSION,
}

ROLE_MAP = {
    "assistant": "assistant",
    "ai": "assistant",
    "bot": "assistant",
    "chatgpt": "assistant",
    "claude": "assistant",
    "gemini": "assistant",
    "model": "assistant",
    "doubao": "assistant",
    "豆包": "assistant",
    "助手": "assistant",
    "human": "user",
    "me": "user",
    "sender": "user",
    "user": "user",
    "you": "user",
    "用户": "user",
    "我": "user",
    "developer": "system",
    "system": "system",
    "系统": "system",
}
ROLE_LABEL_PATTERN = "|".join(sorted((re.escape(item) for item in ROLE_MAP), key=len, reverse=True))
BRACKET_ROLE_RE = re.compile(
    rf"^\s*(?:>\s*)?(?:#{{1,6}}\s*)?(?:\*\*|__)?\s*\[\s*(?P<label>{ROLE_LABEL_PATTERN})\s*\]"
    rf"\s*(?:\*\*|__)?\s*[:：]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
HEADING_ROLE_RE = re.compile(
    rf"^\s*(?:>\s*)?#{{1,6}}\s+(?:\*\*|__)?\s*(?P<label>{ROLE_LABEL_PATTERN})\s*"
    rf"(?:\*\*|__)?\s*[:：]?\s*(?:\*\*|__)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
PREFIX_ROLE_RE = re.compile(
    rf"^\s*(?:>\s*)?(?:[-*+]\s+)?(?:\*\*|__)?\s*(?P<label>{ROLE_LABEL_PATTERN})\s*"
    rf"(?:\*\*|__)?\s*[:：]\s*(?:\*\*|__)?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
TIME_TAG_RE = re.compile(r"<time\b[^>]*\bdatetime=[\"'](?P<value>[^\"']+)[\"'][^>]*>.*?</time>", re.IGNORECASE)
INLINE_TIMESTAMP_RE = re.compile(r"^\s*\[(?P<value>\d{4}-\d{1,2}-\d{1,2}(?:[T\s][^\]]+)?)\]\s*(?P<rest>.+)$")
TITLE_RE = re.compile(r"^\s*#\s+(?P<title>.+?)\s*$")
SOURCE_URL_RE = re.compile(
    r"^\s*>\s*(?:来源|source|url)\s*[:：]\s*(?P<url>https?://[^\s>)\"']+)",
    re.IGNORECASE,
)
FRONTMATTER_DATE_RE = re.compile(
    r"^\s*(?P<key>created_at|create_time|created|date|timestamp|updated_at|update_time)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
CHATGPT_MARKER_RE = re.compile(
    r"^\s*#{1,6}\s+(?:\*\*|__)?\s*(?:You|ChatGPT|Plugin\s*\([^\r\n)]{0,200}\))"
    r"\s*(?:\*\*|__)?\s*[:：]?",
    re.IGNORECASE | re.MULTILINE,
)
PLUGIN_ROLE_RE = re.compile(
    r"^\s*(?:>\s*)?#{1,6}\s+(?:\*\*|__)?\s*Plugin\s*\([^\r\n)]{0,200}\)"
    r"\s*(?:\*\*|__)?\s*[:：]?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
DOUBAO_MARKER_RE = re.compile(
    r"^\s*#{1,6}\s+(?:\*\*|__)?\s*\[\s*(?:用户|AI)\s*\]",
    re.IGNORECASE | re.MULTILINE,
)

SECRET_KEY_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*([^\s,;]+)")
QUOTED_SECRET_KEY_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|token|secret|password|authorization)[\"']?\s*[:=]\s*[\"']?)"
    r"([^\"',\s;&}]+)"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@\s/]+)(@)")
SECRET_TOKEN_PATTERNS = [
    re.compile(r"\byqsk_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
]
INLINE_BASE64_DATA_URI_RE = re.compile(
    r"data:[A-Za-z0-9.+-]*/?[A-Za-z0-9.+-]*"
    r"(?:;[A-Za-z0-9.+_-]+(?:=[^;,\s\"'<>)]{1,200})?)*"
    r";base64,[A-Za-z0-9+/_=-]+",
    re.IGNORECASE,
)
INLINE_DATA_PLACEHOLDER = "[inline-data-omitted]"


def strip_inline_base64_data_uris(value: str) -> str:
    """Remove embedded binary payloads before token counting or LLM submission."""
    return INLINE_BASE64_DATA_URI_RE.sub(INLINE_DATA_PLACEHOLDER, value)


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str | int | float | None = None
    source_index: int | None = None
    part_index: int = 0

    def __post_init__(self) -> None:
        self.content = strip_inline_base64_data_uris(str(self.content))


@dataclass
class Conversation:
    id: str
    title: str
    messages: list[ChatMessage]
    created_at: str | int | float | None = None
    updated_at: str | int | float | None = None
    source_path: str = ""
    source_app: str = "generic"
    source_url: str | None = None


@dataclass(frozen=True)
class SourceFile:
    path: Path
    display_path: str


@dataclass
class DiscoveryResult:
    files: list[SourceFile] = field(default_factory=list)
    skipped_files: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportOptions:
    entities: dict[str, str]
    source_app: str = "auto"
    infer: bool = True
    redact_secrets: bool = True
    skip_duplicates: bool = True
    chunk_messages: int = 20
    chunk_chars: int = 12000
    chunk_target_tokens: int = 5000
    chunk_max_tokens: int = 6000
    chunk_overlap_turns: int = 1
    chunk_gap_minutes: int = 45
    workers: int = 3
    max_workers: int = 4
    max_attempts: int = 3
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0
    retry_jitter: float = 0.35
    model_tiering_enabled: bool = True
    fast_model: str = "gemini-2.5-flash"
    fallback_model: str = "gemini-2.5-pro"
    audit_ratio: float = 0.07
    min_confidence: float = 0.65
    max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH
    batch_id: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("chat-import-%Y%m%dT%H%M%SZ"))
    import_key_schema_version: int = CURRENT_IMPORT_KEY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.import_key_schema_version = int(self.import_key_schema_version)
        if self.import_key_schema_version not in SUPPORTED_IMPORT_KEY_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported import-key schema version: {self.import_key_schema_version}")
        self.chunk_target_tokens = max(4000, min(int(self.chunk_target_tokens), 6000))
        self.chunk_max_tokens = max(self.chunk_target_tokens, min(int(self.chunk_max_tokens), 6000))
        self.chunk_overlap_turns = max(0, min(int(self.chunk_overlap_turns), 2))
        self.max_workers = max(1, min(int(self.max_workers), 4))
        self.workers = max(1, min(int(self.workers), self.max_workers))
        self.max_attempts = max(1, int(self.max_attempts))
        self.audit_ratio = max(0.0, min(float(self.audit_ratio), 1.0))

    @classmethod
    def from_persisted_snapshot(cls, snapshot: Mapping[str, Any] | None) -> ImportOptions:
        values = dict(snapshot or {})
        version_missing = "import_key_schema_version" not in values
        values.setdefault("import_key_schema_version", CURRENT_IMPORT_KEY_SCHEMA_VERSION)
        options = cls(**values)
        if version_missing:
            options._import_key_schema_version_missing = True
        return options


def coerce_iso_datetime(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None
    try:
        return coerce_iso_datetime(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def normalize_role(value: Any) -> str:
    role = str(value or "user").strip().lower()
    return ROLE_MAP.get(role, "user")


def stringify_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [stringify_content(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value", "message"):
            if key in value:
                return stringify_content(value[key])
        if value.get("type") == "text" and "parts" in value:
            return stringify_content(value["parts"])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def redact_secrets(text: str) -> str:
    def replace_key_value(match: re.Match[str]) -> str:
        return f"{match.group(1)}=[redacted]"

    redacted = BEARER_TOKEN_RE.sub("Bearer [redacted]", text)
    redacted = SECRET_KEY_VALUE_RE.sub(replace_key_value, redacted)
    redacted = QUOTED_SECRET_KEY_VALUE_RE.sub(r"\1[redacted]", redacted)
    redacted = URL_CREDENTIAL_RE.sub(r"\1[redacted]\3", redacted)
    for pattern in SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def detect_source_app(path: Path | str, explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        return explicit.strip().lower()
    haystack = str(path).lower()
    sources = (
        "chatgpt",
        "doubao",
        "openai",
        "claude",
        "gemini",
        "cursor",
        "codex",
        "trae",
        "hermes",
        "qclaw",
    )
    for name in sources:
        if name in haystack:
            return "chatgpt" if name == "openai" else name
    return "generic"


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def _parse_role_marker(line: str) -> tuple[str, str, str | None] | None:
    timestamp: str | None = None
    candidate = line
    inline_timestamp = INLINE_TIMESTAMP_RE.match(candidate)
    if inline_timestamp:
        timestamp = inline_timestamp.group("value")
        candidate = inline_timestamp.group("rest")
    plugin = PLUGIN_ROLE_RE.match(candidate)
    if plugin:
        return "assistant", plugin.group("rest").strip(), timestamp
    for pattern in (BRACKET_ROLE_RE, HEADING_ROLE_RE, PREFIX_ROLE_RE):
        match = pattern.match(candidate)
        if match:
            rest = match.group("rest").strip()
            return normalize_role(match.group("label")), rest, timestamp
    return None


def _strip_inline_markdown(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", text)
    return text.strip()


def content_from_chatgpt_message(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    if isinstance(content, dict):
        if "parts" in content:
            return stringify_content(content["parts"])
        if "text" in content:
            return stringify_content(content["text"])
    return stringify_content(content)


def chatgpt_active_branch_nodes(
    mapping: Mapping[Any, Any],
    current_node: Any,
) -> list[dict[str, Any]]:
    """Return one deterministic root-to-tip branch from a ChatGPT mapping export."""
    nodes = {
        str(key): node for key, node in sorted(mapping.items(), key=lambda item: str(item[0])) if isinstance(node, dict)
    }
    if not nodes:
        return []

    references = {key: key for key in nodes}
    for key, node in nodes.items():
        node_id = node.get("id")
        if node_id is not None:
            references.setdefault(str(node_id), key)

    def resolve(reference: Any) -> str | None:
        if reference is None:
            return None
        return references.get(str(reference))

    derived_parents: dict[str, list[str]] = {}
    for parent_key, node in nodes.items():
        children = node.get("children")
        if not isinstance(children, list):
            continue
        for child in children:
            child_key = resolve(child)
            if child_key is not None:
                derived_parents.setdefault(child_key, []).append(parent_key)

    def parent_key(key: str) -> str | None:
        explicit = resolve(nodes[key].get("parent"))
        if explicit is not None:
            return explicit
        candidates = derived_parents.get(key, [])
        return min(candidates) if candidates else None

    def branch(key: str) -> list[dict[str, Any]]:
        reversed_branch: list[dict[str, Any]] = []
        seen: set[str] = set()
        while key not in seen:
            seen.add(key)
            reversed_branch.append(nodes[key])
            parent = parent_key(key)
            if parent is None:
                break
            key = parent
        reversed_branch.reverse()
        return reversed_branch

    selected = resolve(current_node)
    if selected is not None:
        return branch(selected)

    referenced_parents = {parent for key in nodes if (parent := parent_key(key)) is not None}
    tips = sorted(set(nodes) - referenced_parents) or sorted(nodes)

    def node_timestamp(node: Mapping[str, Any]) -> str:
        message = node.get("message") if isinstance(node.get("message"), dict) else {}
        raw_timestamp = message.get("create_time")
        if raw_timestamp is None:
            raw_timestamp = node.get("create_time")
        return coerce_iso_datetime(raw_timestamp) or ""

    def fallback_rank(key: str) -> tuple[bool, str, int, str]:
        candidate_branch = branch(key)
        timestamp = max((node_timestamp(node) for node in candidate_branch), default="")
        return bool(timestamp), timestamp, len(candidate_branch), key

    return branch(max(tips, key=fallback_rank))


def parse_chatgpt_export(data: Any, path: Path, source_app: str, source_path: str) -> list[Conversation]:
    if not isinstance(data, list) or not any(isinstance(item, dict) and "mapping" in item for item in data):
        return []
    conversations: list[Conversation] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("mapping"), dict):
            continue
        messages: list[ChatMessage] = []
        nodes = chatgpt_active_branch_nodes(item["mapping"], item.get("current_node"))
        for node in nodes:
            message = node.get("message") if isinstance(node, dict) else None
            if not isinstance(message, dict):
                continue
            role = normalize_role(((message.get("author") or {}).get("role")))
            if role not in {"user", "assistant"}:
                continue
            content = content_from_chatgpt_message(message)
            if content:
                messages.append(ChatMessage(role=role, content=content, created_at=message.get("create_time")))
        if messages:
            conversations.append(
                Conversation(
                    id=str(item.get("id") or f"chatgpt-{index}"),
                    title=str(item.get("title") or path.stem),
                    messages=messages,
                    created_at=item.get("create_time") or messages[0].created_at,
                    updated_at=item.get("update_time"),
                    source_path=source_path,
                    source_app=source_app,
                )
            )
    return conversations


def message_from_generic(raw: Any) -> ChatMessage | None:
    if isinstance(raw, str):
        content = raw.strip()
        return ChatMessage(role="user", content=content) if content else None
    if not isinstance(raw, dict):
        return None
    role = normalize_role(raw.get("role") or raw.get("sender") or raw.get("author") or raw.get("from"))
    content = ""
    for key in ("content", "text", "message", "body", "data"):
        if key in raw:
            content = stringify_content(raw[key])
            break
    if not content:
        return None
    created_at = (
        raw.get("created_at") or raw.get("create_time") or raw.get("timestamp") or raw.get("time") or raw.get("date")
    )
    return ChatMessage(role=role, content=content, created_at=created_at)


def conversation_from_generic(
    raw: Any,
    path: Path,
    source_app: str,
    source_path: str,
    fallback_id: str,
) -> Conversation | None:
    if not isinstance(raw, dict):
        return None
    raw_messages = raw.get("messages") or raw.get("chat_messages") or raw.get("turns") or raw.get("items")
    if not isinstance(raw_messages, list):
        return None
    messages = [message for message in (message_from_generic(item) for item in raw_messages) if message]
    if not messages:
        return None
    conversation_id = str(raw.get("id") or raw.get("uuid") or raw.get("conversation_id") or fallback_id)
    title = str(raw.get("title") or raw.get("name") or raw.get("summary") or path.stem)
    created_at = raw.get("created_at") or raw.get("create_time") or raw.get("timestamp") or messages[0].created_at
    updated_at = raw.get("updated_at") or raw.get("update_time")
    return Conversation(conversation_id, title, messages, created_at, updated_at, source_path, source_app)


def parse_json_data(data: Any, path: Path, source_app: str, source_path: str | None = None) -> list[Conversation]:
    display_path = source_path or str(path)
    chatgpt = parse_chatgpt_export(data, path, source_app, display_path)
    if chatgpt:
        return chatgpt

    if isinstance(data, dict):
        if isinstance(data.get("conversations"), list):
            candidates = data["conversations"]
        elif isinstance(data.get("data"), list):
            candidates = data["data"]
        else:
            candidates = [data]
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = []

    conversations: list[Conversation] = []
    loose_messages: list[ChatMessage] = []
    for index, item in enumerate(candidates):
        conversation = conversation_from_generic(item, path, source_app, display_path, f"{path.stem}-{index}")
        if conversation:
            conversations.append(conversation)
            continue
        message = message_from_generic(item)
        if message:
            loose_messages.append(message)
    if loose_messages:
        conversations.append(
            Conversation(
                path.stem, path.stem, loose_messages, loose_messages[0].created_at, None, display_path, source_app
            )
        )
    return conversations


def parse_json_file(path: Path, source_app: str, source_path: str | None = None) -> list[Conversation]:
    return parse_json_data(json.loads(_read_text(path)), path, source_app, source_path)


def parse_jsonl_file(path: Path, source_app: str, source_path: str | None = None) -> list[Conversation]:
    display_path = source_path or str(path)
    conversations: list[Conversation] = []
    loose_messages: list[ChatMessage] = []
    for index, line in enumerate(_read_text(path).splitlines()):
        if not line.strip():
            continue
        raw = json.loads(line)
        conversation = conversation_from_generic(raw, path, source_app, display_path, f"{path.stem}-{index}")
        if conversation:
            conversations.append(conversation)
            continue
        message = message_from_generic(raw)
        if message:
            loose_messages.append(message)
    if loose_messages:
        conversations.append(
            Conversation(
                path.stem, path.stem, loose_messages, loose_messages[0].created_at, None, display_path, source_app
            )
        )
    return conversations


def parse_text_file(
    path: Path,
    source_app: str,
    source_path: str | None = None,
    *,
    detect_format: bool = False,
) -> list[Conversation]:
    display_path = source_path or str(path)
    text = _read_text(path)
    if detect_format:
        if "doubao.com/" in text or DOUBAO_MARKER_RE.search(text):
            source_app = "doubao"
        elif CHATGPT_MARKER_RE.search(text):
            source_app = "chatgpt"
    messages: list[ChatMessage] = []
    current_role: str | None = None
    current_created_at: str | int | float | None = None
    current_lines: list[str] = []
    title = path.stem
    source_url: str | None = None
    frontmatter_created_at: str | None = None
    frontmatter_updated_at: str | None = None

    def flush() -> None:
        nonlocal current_role, current_created_at, current_lines
        while current_lines and not current_lines[-1].strip():
            current_lines.pop()
        while current_lines and current_lines[-1].strip() in {"---", "***", "___"}:
            current_lines.pop()
            while current_lines and not current_lines[-1].strip():
                current_lines.pop()
        content = "\n".join(current_lines).strip()
        if current_role and content:
            messages.append(ChatMessage(role=current_role, content=content, created_at=current_created_at))
        current_role = None
        current_created_at = None
        current_lines = []

    in_frontmatter = False
    for index, line in enumerate(text.splitlines()):
        if index == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                continue
            date_match = FRONTMATTER_DATE_RE.match(line)
            if date_match:
                key = date_match.group("key").lower()
                value = date_match.group("value")
                if key.startswith("update"):
                    frontmatter_updated_at = value
                elif frontmatter_created_at is None:
                    frontmatter_created_at = value
            continue

        if title == path.stem:
            title_match = TITLE_RE.match(line)
            if title_match and _parse_role_marker(line) is None:
                title = _strip_inline_markdown(title_match.group("title")) or path.stem
        if source_url is None:
            source_match = SOURCE_URL_RE.match(line)
            if source_match:
                source_url = source_match.group("url").rstrip(".,")

        marker = _parse_role_marker(line)
        if marker:
            flush()
            current_role, rest, current_created_at = marker
            current_lines = [rest] if rest else []
            continue

        if current_role:
            time_match = TIME_TAG_RE.search(line)
            if time_match and not any(item.strip() for item in current_lines):
                current_created_at = current_created_at or time_match.group("value")
                remaining = TIME_TAG_RE.sub("", line).strip()
                if remaining:
                    current_lines.append(remaining)
                continue
            current_lines.append(line)
    flush()

    if not messages and text.strip():
        messages = [ChatMessage(role="user", content=text.strip(), created_at=frontmatter_created_at)]
    if not messages:
        return []
    conversation_id = path.stem
    if source_url:
        url_id = source_url.rstrip("/").rsplit("/", 1)[-1]
        if url_id:
            conversation_id = url_id
    return [
        Conversation(
            id=conversation_id,
            title=title,
            messages=messages,
            created_at=messages[0].created_at or frontmatter_created_at,
            updated_at=frontmatter_updated_at,
            source_path=display_path,
            source_app=source_app,
            source_url=source_url,
        )
    ]


def parse_file(path: Path, source_app_arg: str | None = None, source_path: str | None = None) -> list[Conversation]:
    source_app = detect_source_app(source_path or path, source_app_arg)
    suffix = path.suffix.lower()
    if suffix == ".json":
        conversations = parse_json_file(path, source_app, source_path)
    elif suffix == ".jsonl":
        conversations = parse_jsonl_file(path, source_app, source_path)
    elif suffix in {".md", ".markdown", ".mdx", ".txt"}:
        conversations = parse_text_file(
            path,
            source_app,
            source_path,
            detect_format=not source_app_arg or source_app_arg == "auto",
        )
    else:
        conversations = []
    if not source_app_arg or source_app_arg == "auto":
        for conversation in conversations:
            if conversation.source_url:
                conversation.source_app = detect_source_app(conversation.source_url)
    return conversations


def _archive_kind(path: Path | str) -> str | None:
    lowered = str(path).lower()
    return next((suffix for suffix in ARCHIVE_SUFFIXES if lowered.endswith(suffix)), None)


def is_supported_input(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_SUFFIXES or _archive_kind(name) is not None


def _safe_member_path(name: str) -> PurePosixPath | None:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        return None
    if ":" in normalized.parts[0]:
        return None
    return normalized


def safe_upload_path(name: str, fallback: str) -> Path:
    relative = _safe_member_path(name)
    if relative is None:
        return Path(fallback)
    return Path(*relative.parts)


def _extract_zip(
    archive: Path,
    destination: Path,
    display_prefix: str,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_files: int,
) -> DiscoveryResult:
    result = DiscoveryResult()
    total_bytes = 0
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            if member.is_dir():
                continue
            relative = _safe_member_path(member.filename)
            unix_mode = (member.external_attr >> 16) & 0o170000
            if relative is None or unix_mode == 0o120000:
                result.skipped_files += 1
                result.warnings.append(f"Skipped unsafe archive entry: {member.filename}")
                continue
            if relative.suffix.lower() not in SUPPORTED_SUFFIXES:
                result.skipped_files += 1
                continue
            if member.file_size > max_file_bytes:
                result.skipped_files += 1
                result.warnings.append(f"Skipped oversized file: {display_prefix}!/{relative.as_posix()}")
                continue
            total_bytes += member.file_size
            if total_bytes > max_total_bytes:
                raise ValueError(f"Archive expands beyond {max_total_bytes // (1024 * 1024)} MB.")
            if len(result.files) >= max_files:
                raise ValueError(f"Archive contains more than {max_files} supported files.")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            result.files.append(SourceFile(target, f"{display_prefix}!/{relative.as_posix()}"))
    return result


def _extract_tar(
    archive: Path,
    destination: Path,
    display_prefix: str,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_files: int,
) -> DiscoveryResult:
    result = DiscoveryResult()
    total_bytes = 0
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:*") as handle:
        for member in handle:
            if not member.isfile():
                if member.issym() or member.islnk():
                    result.skipped_files += 1
                    result.warnings.append(f"Skipped archive link: {member.name}")
                continue
            relative = _safe_member_path(member.name)
            if relative is None:
                result.skipped_files += 1
                result.warnings.append(f"Skipped unsafe archive entry: {member.name}")
                continue
            if relative.suffix.lower() not in SUPPORTED_SUFFIXES:
                result.skipped_files += 1
                continue
            if member.size > max_file_bytes:
                result.skipped_files += 1
                result.warnings.append(f"Skipped oversized file: {display_prefix}!/{relative.as_posix()}")
                continue
            total_bytes += member.size
            if total_bytes > max_total_bytes:
                raise ValueError(f"Archive expands beyond {max_total_bytes // (1024 * 1024)} MB.")
            if len(result.files) >= max_files:
                raise ValueError(f"Archive contains more than {max_files} supported files.")
            extracted = handle.extractfile(member)
            if extracted is None:
                result.skipped_files += 1
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with extracted, target.open("wb") as output:
                shutil.copyfileobj(extracted, output, length=1024 * 1024)
            result.files.append(SourceFile(target, f"{display_prefix}!/{relative.as_posix()}"))
    return result


def discover_input_files(
    inputs: Iterable[Path],
    extraction_root: Path,
    *,
    input_root: Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
) -> DiscoveryResult:
    result = DiscoveryResult()
    seen: set[Path] = set()
    expanded_bytes = 0
    candidates: list[Path] = []
    for input_path in inputs:
        if input_path.is_dir():
            candidates.extend(sorted(path for path in input_path.rglob("*") if path.is_file()))
        else:
            candidates.append(input_path)

    for index, path in enumerate(candidates):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.is_file():
            result.skipped_files += 1
            continue
        try:
            display_path = path.relative_to(input_root).as_posix() if input_root else path.name
        except ValueError:
            display_path = path.name
        archive_kind = _archive_kind(path)
        if archive_kind:
            destination = extraction_root / f"archive-{index:04d}-{stable_hash(display_path)[:10]}"
            try:
                archive_result = (
                    _extract_zip(
                        path,
                        destination,
                        display_path,
                        max_file_bytes=max_file_bytes,
                        max_total_bytes=max_archive_bytes,
                        max_files=max_files - len(result.files),
                    )
                    if archive_kind == ".zip"
                    else _extract_tar(
                        path,
                        destination,
                        display_path,
                        max_file_bytes=max_file_bytes,
                        max_total_bytes=max_archive_bytes,
                        max_files=max_files - len(result.files),
                    )
                )
            except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
                result.skipped_files += 1
                result.warnings.append(f"Could not open {display_path}: {exc}")
                continue
            expanded_bytes += sum(item.path.stat().st_size for item in archive_result.files)
            if expanded_bytes > max_archive_bytes:
                raise ValueError(f"Import expands beyond {max_archive_bytes // (1024 * 1024)} MB.")
            result.files.extend(archive_result.files)
            result.skipped_files += archive_result.skipped_files
            result.warnings.extend(archive_result.warnings)
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            result.skipped_files += 1
            continue
        if path.stat().st_size > max_file_bytes:
            result.skipped_files += 1
            result.warnings.append(f"Skipped oversized file: {display_path}")
            continue
        expanded_bytes += path.stat().st_size
        if expanded_bytes > max_archive_bytes:
            raise ValueError(f"Import contains more than {max_archive_bytes // (1024 * 1024)} MB of documents.")
        result.files.append(SourceFile(path, display_path))
        if len(result.files) > max_files:
            raise ValueError(f"Import contains more than {max_files} supported files.")
    return result


def split_long_message(message: ChatMessage, max_chars: int) -> list[ChatMessage]:
    if len(message.content) <= max_chars:
        return [message]
    parts: list[ChatMessage] = []
    paragraphs = re.split(r"(\n\s*\n)", message.content)
    current = ""
    for part in paragraphs:
        if len(current) + len(part) <= max_chars:
            current += part
            continue
        if current.strip():
            parts.append(ChatMessage(message.role, current.strip(), message.created_at))
        current = part
        while len(current) > max_chars:
            parts.append(ChatMessage(message.role, current[:max_chars].strip(), message.created_at))
            current = current[max_chars:]
    if current.strip():
        parts.append(ChatMessage(message.role, current.strip(), message.created_at))
    return parts


def chunk_messages(messages: list[ChatMessage], max_messages: int, max_chars: int) -> list[list[ChatMessage]]:
    """Legacy character chunker retained for callers outside the import API."""
    chunks: list[list[ChatMessage]] = []
    current: list[ChatMessage] = []
    current_chars = 0
    for message in messages:
        for part in split_long_message(message, max_chars):
            size = len(part.content)
            if current and (len(current) >= max_messages or current_chars + size > max_chars):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(part)
            current_chars += size
    if current:
        chunks.append(current)
    return chunks


_TOKEN_ENCODER: Any = None
_TOKEN_ENCODER_READY = False
_TOKEN_ENCODER_LOCK = threading.Lock()
_FALLBACK_TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?|[^\s]",
    re.UNICODE,
)
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u3400-\u9fff]{2,}", re.UNICODE)


def _token_encoder() -> Any:
    global _TOKEN_ENCODER, _TOKEN_ENCODER_READY
    if not _TOKEN_ENCODER_READY:
        with _TOKEN_ENCODER_LOCK:
            if not _TOKEN_ENCODER_READY:
                try:
                    import tiktoken

                    _TOKEN_ENCODER = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    _TOKEN_ENCODER = None
                _TOKEN_ENCODER_READY = True
    return _TOKEN_ENCODER


def estimate_tokens(value: str) -> int:
    """Return a model-agnostic token estimate, using tiktoken when available."""
    encoder = _token_encoder()
    if encoder is not None:
        try:
            return max(1, len(encoder.encode(value, disallowed_special=())))
        except Exception:
            pass

    count = 0
    for token in _FALLBACK_TOKEN_RE.findall(value or ""):
        if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?", token):
            count += max(1, math.ceil(len(token) / 4))
        else:
            count += 1
    return max(1, count)


def _copy_message(message: ChatMessage, content: str, part_index: int) -> ChatMessage:
    return ChatMessage(
        role=message.role,
        content=content,
        created_at=message.created_at,
        source_index=message.source_index,
        part_index=part_index,
    )


def _safe_utf8_boundary(raw: bytes, boundary: int) -> int:
    boundary = max(0, min(boundary, len(raw)))
    while 0 < boundary < len(raw) and raw[boundary] & 0xC0 == 0x80:
        boundary -= 1
    return boundary


def _split_encoded_text(text: str, max_tokens: int, encoder: Any) -> list[str]:
    token_ids = encoder.encode(text, disallowed_special=())
    if len(token_ids) <= max_tokens:
        return [text]

    raw = text.encode("utf-8")
    parts: list[str] = []
    previous_boundary = 0
    byte_offset = 0
    for token_index, token_id in enumerate(token_ids, start=1):
        byte_offset += len(encoder.decode_single_token_bytes(token_id))
        if token_index % max_tokens or token_index == len(token_ids):
            continue
        boundary = _safe_utf8_boundary(raw, byte_offset)
        if boundary <= previous_boundary:
            continue
        parts.append(raw[previous_boundary:boundary].decode("utf-8"))
        previous_boundary = boundary
    if previous_boundary < len(raw):
        parts.append(raw[previous_boundary:].decode("utf-8"))
    return parts


def _fallback_token_weight(token: str) -> int:
    if re.fullmatch(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?", token):
        return max(1, math.ceil(len(token) / 4))
    return 1


def _split_fallback_text(text: str, max_tokens: int) -> list[str]:
    parts: list[str] = []
    part_start = 0
    used = 0
    for match in _FALLBACK_TOKEN_RE.finditer(text):
        token = match.group(0)
        weight = _fallback_token_weight(token)
        if used and used + weight > max_tokens:
            parts.append(text[part_start : match.start()])
            part_start = match.start()
            used = 0
        if weight > max_tokens:
            if part_start < match.start():
                parts.append(text[part_start : match.start()])
                part_start = match.start()
            characters_per_part = max_tokens * 4 if token[0].isascii() and token[0].isalnum() else max_tokens
            while match.end() - part_start > characters_per_part:
                boundary = part_start + characters_per_part
                parts.append(text[part_start:boundary])
                part_start = boundary
            used = _fallback_token_weight(text[part_start : match.end()])
        else:
            used += weight
    if part_start < len(text):
        parts.append(text[part_start:])
    return parts or [text]


def _split_atomic_text(text: str, max_tokens: int) -> list[str]:
    encoder = _token_encoder()
    if encoder is not None:
        try:
            return _split_encoded_text(text, max_tokens, encoder)
        except Exception:
            pass
    return _split_fallback_text(text, max_tokens)


def _split_text_by_tokens(text: str, max_tokens: int) -> list[str]:
    """Split text in near-linear time while preserving every source character."""
    max_tokens = max(1, int(max_tokens))
    units = [item for item in re.split(r"(\n\s*\n|(?<=[.!?\u3002\uff01\uff1f])\s+)", text) if item]
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            parts.append("".join(current))
            current = []
            current_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if unit_tokens > max_tokens:
            flush()
            parts.extend(_split_atomic_text(unit, max_tokens))
            continue
        if current and current_tokens + unit_tokens > max_tokens:
            flush()
        current.append(unit)
        current_tokens += unit_tokens
    flush()
    return parts or [text]


def split_long_message_tokens(message: ChatMessage, max_tokens: int) -> list[ChatMessage]:
    return [
        _copy_message(message, content, part_index)
        for part_index, content in enumerate(_split_text_by_tokens(message.content, max_tokens))
    ]


@dataclass
class ConversationTurn:
    messages: list[ChatMessage]
    token_count: int


@dataclass
class MessageChunk:
    messages: list[ChatMessage]
    token_count: int
    source_indices: list[int]
    core_source_indices: list[int]
    overlap_turns: int = 0
    split_depth: int = 0
    parent_import_key: str | None = None


def _ensure_source_indices(messages: list[ChatMessage]) -> list[ChatMessage]:
    indexed: list[ChatMessage] = []
    for index, message in enumerate(messages):
        source_index = message.source_index if message.source_index is not None else index
        indexed.append(
            ChatMessage(
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                source_index=source_index,
                part_index=message.part_index,
            )
        )
    return indexed


def _build_turns(messages: list[ChatMessage], max_tokens: int) -> list[ConversationTurn]:
    expanded: list[ChatMessage] = []
    for message in _ensure_source_indices(messages):
        expanded.extend(split_long_message_tokens(message, max_tokens=max_tokens - 16))

    turns: list[ConversationTurn] = []
    current: list[ChatMessage] = []
    current_user_index: int | None = None
    for message in expanded:
        starts_turn = message.role == "user" and current and message.source_index != current_user_index
        if starts_turn:
            turns.append(ConversationTurn(current, sum(estimate_tokens(item.content) + 6 for item in current)))
            current = []
            current_user_index = None
        current.append(message)
        if message.role == "user":
            current_user_index = message.source_index
    if current:
        turns.append(ConversationTurn(current, sum(estimate_tokens(item.content) + 6 for item in current)))

    bounded: list[ConversationTurn] = []
    for turn in turns:
        if turn.token_count <= max_tokens:
            bounded.append(turn)
            continue
        batch: list[ChatMessage] = []
        batch_tokens = 0
        for message in turn.messages:
            message_tokens = estimate_tokens(message.content) + 6
            if batch and batch_tokens + message_tokens > max_tokens:
                bounded.append(ConversationTurn(batch, batch_tokens))
                batch = []
                batch_tokens = 0
            batch.append(message)
            batch_tokens += message_tokens
        if batch:
            bounded.append(ConversationTurn(batch, batch_tokens))
    return bounded


def _timestamp_seconds(value: Any) -> float | None:
    normalized = coerce_iso_datetime(value)
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _turn_terms(turn: ConversationTurn) -> set[str]:
    text = " ".join(message.content for message in turn.messages)
    return {term.lower() for term in _TERM_RE.findall(text) if len(term) <= 80}


def _strong_turn_boundary(previous: ConversationTurn, following: ConversationTurn, gap_minutes: int) -> bool:
    previous_time = _timestamp_seconds(previous.messages[-1].created_at)
    following_time = _timestamp_seconds(following.messages[0].created_at)
    if previous_time is not None and following_time is not None:
        if following_time - previous_time >= gap_minutes * 60:
            return True

    previous_terms = _turn_terms(previous)
    following_terms = _turn_terms(following)
    if previous_terms and following_terms:
        similarity = len(previous_terms & following_terms) / max(1, len(previous_terms | following_terms))
        if similarity < 0.05:
            return True
    return bool(re.search(r"(?m)^#{1,6}\s+", following.messages[0].content))


def _chunk_from_turns(
    turns: list[ConversationTurn],
    *,
    leading_overlap: int,
    split_depth: int = 0,
    parent_import_key: str | None = None,
    core_source_indices: Iterable[int] | None = None,
) -> MessageChunk:
    fragmented_messages = [message for turn in turns for message in turn.messages]
    messages: list[ChatMessage] = []
    message_positions: dict[int, int] = {}
    for message in fragmented_messages:
        source_index = message.source_index
        if source_index is None or source_index not in message_positions:
            if source_index is not None:
                message_positions[source_index] = len(messages)
            messages.append(
                ChatMessage(
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                    source_index=source_index,
                    part_index=message.part_index,
                )
            )
            continue

        existing = messages[message_positions[source_index]]
        if existing.role != message.role:
            raise ValueError(f"source_index {source_index} is assigned to multiple roles")
        existing.content += message.content

    all_indices = list(
        dict.fromkeys(int(message.source_index) for message in messages if message.source_index is not None)
    )
    if core_source_indices is None:
        core_turns = turns[min(leading_overlap, len(turns)) :]
        core_indices = list(
            dict.fromkeys(
                int(message.source_index)
                for turn in core_turns
                for message in turn.messages
                if message.source_index is not None
            )
        )
    else:
        allowed_core_indices = {int(index) for index in core_source_indices}
        core_indices = [index for index in all_indices if index in allowed_core_indices]
    return MessageChunk(
        messages=messages,
        token_count=sum(estimate_tokens(message.content) + 6 for message in messages),
        source_indices=all_indices,
        core_source_indices=core_indices if core_source_indices is not None else (core_indices or all_indices),
        overlap_turns=leading_overlap,
        split_depth=split_depth,
        parent_import_key=parent_import_key,
    )


def adaptive_chunk_messages(messages: list[ChatMessage], options: ImportOptions) -> list[MessageChunk]:
    turns = _build_turns(messages, options.chunk_max_tokens)
    if not turns:
        return []

    chunks: list[MessageChunk] = []
    current: list[ConversationTurn] = []
    current_tokens = 0
    leading_overlap = 0

    def emit() -> None:
        nonlocal current, current_tokens, leading_overlap
        if not current or len(current) <= leading_overlap:
            return
        chunks.append(_chunk_from_turns(current, leading_overlap=leading_overlap))
        overlap = current[-options.chunk_overlap_turns :] if options.chunk_overlap_turns else []
        current = list(overlap)
        current_tokens = sum(turn.token_count for turn in current)
        leading_overlap = len(current)

    for turn in turns:
        while current and current_tokens + turn.token_count > options.chunk_max_tokens:
            if len(current) > leading_overlap:
                emit()
            elif current:
                current.pop(0)
                leading_overlap = max(0, leading_overlap - 1)
                current_tokens = sum(item.token_count for item in current)
            else:
                break

        if (
            current
            and len(current) > leading_overlap
            and current_tokens >= 4000
            and _strong_turn_boundary(current[-1], turn, options.chunk_gap_minutes)
        ):
            emit()
            while current and current_tokens + turn.token_count > options.chunk_max_tokens:
                current.pop(0)
                leading_overlap = max(0, leading_overlap - 1)
                current_tokens = sum(item.token_count for item in current)

        current.append(turn)
        current_tokens += turn.token_count
        if current_tokens >= options.chunk_target_tokens and current_tokens >= 4000:
            emit()

    if current and len(current) > leading_overlap:
        chunks.append(_chunk_from_turns(current, leading_overlap=leading_overlap))
    return chunks


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_entity_scope(entities: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(entities[key]).strip()
        for key in ("user_id", "agent_id", "app_id", "run_id")
        if entities.get(key) is not None and str(entities[key]).strip()
    }


def entity_scope_hash(entities: Mapping[str, Any]) -> str:
    return stable_hash(canonical_entity_scope(entities))


def scoped_conversation_hash(conversation_id: str, entities: Mapping[str, Any]) -> str:
    return stable_hash(
        {
            "conversation_id": str(conversation_id),
            "entity_scope": canonical_entity_scope(entities),
        }
    )


def sanitize_run_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-._:")
    suffix = stable_hash(value)[:10]
    if not cleaned:
        return f"chat-import-{suffix}"
    return f"{cleaned[:70]}-{suffix}"


def import_key_material(
    conversation: Conversation,
    messages: list[ChatMessage],
    core_source_indices: list[int],
    options: ImportOptions,
) -> dict[str, Any]:
    # v1 is frozen from persisted benchmark/import artifacts: these six fields,
    # serialized by stable_hash, with no entity scope. v2 adds canonical scope.
    material: dict[str, Any] = {
        "source_app": conversation.source_app,
        "source_url": conversation.source_url,
        "conversation_id": conversation.id,
        "conversation_title": conversation.title,
        "core_source_indices": core_source_indices,
        "messages": [asdict(message) for message in messages],
    }
    if options.import_key_schema_version == CURRENT_IMPORT_KEY_SCHEMA_VERSION:
        material["entity_scope"] = canonical_entity_scope(options.entities)
    return material


def build_payload(
    conversation: Conversation,
    messages: list[ChatMessage] | MessageChunk,
    chunk_index: int,
    chunk_count: int,
    options: ImportOptions,
) -> dict[str, Any]:
    chunk = messages if isinstance(messages, MessageChunk) else None
    chunk_messages_value = _ensure_source_indices(chunk.messages if chunk is not None else messages)
    timestamp = coerce_iso_datetime(chunk_messages_value[0].created_at or conversation.created_at)
    source_indices = list(
        dict.fromkeys(int(message.source_index) for message in chunk_messages_value if message.source_index is not None)
    )
    core_source_indices = chunk.core_source_indices if chunk is not None else source_indices
    entity_scope = canonical_entity_scope(options.entities)
    import_key = stable_hash(import_key_material(conversation, chunk_messages_value, core_source_indices, options))
    metadata: dict[str, Any] = {
        "source": "chat-history-import",
        "source_app": conversation.source_app,
        "source_path": conversation.source_path,
        "conversation_id": conversation.id,
        "conversation_title": conversation.title,
        "chunk_index": chunk_index,
        "chunk_count": chunk_count,
        "message_count": len(chunk_messages_value),
        "source_message_indices": source_indices,
        "core_source_message_indices": core_source_indices,
        "token_count": chunk.token_count
        if chunk is not None
        else sum(estimate_tokens(message.content) + 6 for message in chunk_messages_value),
        "overlap_turns": chunk.overlap_turns if chunk is not None else 0,
        "chunk_strategy": "token-turn-v2",
        "import_key_schema_version": options.import_key_schema_version,
        "entity_scope_hash": entity_scope_hash(entity_scope),
        "import_batch": options.batch_id,
        "import_key": import_key,
    }
    if conversation.source_url:
        metadata["source_url"] = conversation.source_url
    if timestamp:
        metadata["source_created_at"] = timestamp
        metadata["created_at"] = timestamp
    updated_at = coerce_iso_datetime(conversation.updated_at)
    if updated_at:
        metadata["source_updated_at"] = updated_at

    payload_messages = [
        {
            "role": message.role,
            "content": redact_secrets(message.content) if options.redact_secrets else message.content,
        }
        for message in chunk_messages_value
    ]
    source_messages: list[dict[str, Any]] = []
    for message, payload_message in zip(chunk_messages_value, payload_messages):
        source_message = dict(payload_message)
        if message.source_index is not None:
            source_message["source_index"] = int(message.source_index)
        message_timestamp = coerce_iso_datetime(message.created_at)
        if message_timestamp:
            source_message["created_at"] = message_timestamp
        if (
            source_messages
            and source_message.get("source_index") is not None
            and source_messages[-1].get("source_index") == source_message["source_index"]
            and source_messages[-1].get("role") == source_message.get("role")
        ):
            source_messages[-1]["content"] = f"{source_messages[-1]['content']}\n\n{source_message['content']}"
        else:
            source_messages.append(source_message)
    metadata["source_messages"] = source_messages
    payload: dict[str, Any] = {
        "messages": payload_messages,
        "metadata": metadata,
        "infer": options.infer,
        **options.entities,
    }
    if timestamp:
        payload["timestamp"] = timestamp
    return payload


JobStatus = Literal[
    "uploading",
    "queued",
    "discovering",
    "parsing",
    "importing",
    "syncing_graph",
    "cancelling",
    "cancelled",
    "completed",
    "completed_with_errors",
    "failed",
]


@dataclass
class ImportJob:
    id: str
    project_id: str
    status: JobStatus
    created_at: str
    updated_at: str
    input_files: list[str]
    entities: dict[str, str]
    source_app: str
    infer: bool
    total_input_files: int
    workspace: str | None = None
    workspace_bytes: int = 0
    source_retry_required: bool = True
    options_snapshot: dict[str, Any] = field(default_factory=dict)
    storage_quota_snapshot: dict[str, Any] = field(default_factory=dict, repr=False)
    phase: str = "queued"
    started_at: str | None = None
    completed_at: str | None = None
    discovered_files: int = 0
    parsed_files: int = 0
    skipped_files: int = 0
    total_conversations: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    imported_chunks: int = 0
    skipped_chunks: int = 0
    failed_chunks: int = 0
    retried_chunks: int = 0
    split_chunks: int = 0
    memories_created: int = 0
    configured_workers: int = 3
    active_workers: int = 0
    current_concurrency: int = 3
    peak_workers: int = 0
    retry_count: int = 0
    total_tokens: int = 0
    graph_status: str = "pending"
    graph_error: str | None = None
    graph_attempts: int = 0
    graph_pending_items: int = 0
    graph_synced_items: int = 0
    graph_failed_items: int = 0
    current_file: str | None = None
    current_conversation: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    error_count: int = 0
    phase_durations: dict[str, float] = field(default_factory=dict)
    chunk_durations: list[float] = field(default_factory=list, repr=False)
    cancel_requested: bool = False


class ImportJobStore:
    def __init__(self, max_jobs: int = 100):
        self._jobs: dict[str, ImportJob] = {}
        self._lock = threading.RLock()
        self._max_jobs = max_jobs
        self._repository: Any = None

    def configure_repository(self, repository: Any | None) -> None:
        with self._lock:
            self._repository = repository

    @staticmethod
    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _from_record(self, record: Any) -> ImportJob:
        errors: list[dict[str, Any]] = []
        durations: list[float] = []
        options_snapshot = dict(record.options or {})
        if self._repository is not None:
            for item in self._repository.list_errors(record.id, limit=MAX_JOB_ERRORS, offset=0):
                error = {
                    "source": item.source,
                    "message": item.message,
                    "type": item.error_type or "import_error",
                    "retryable": bool(item.retryable),
                }
                if item.attempt is not None:
                    error["attempt"] = item.attempt
                if item.error_code:
                    error["code"] = item.error_code
                if isinstance(item.details, dict) and item.details:
                    error["details"] = dict(item.details)
                    if item.details.get("import_key"):
                        error["import_key"] = item.details["import_key"]
                errors.append(error)
            for chunk in self._repository.list_chunks(record.id):
                duration = getattr(chunk, "duration_seconds", None)
                if chunk.status in {"succeeded", "failed"} and duration is not None and float(duration) > 0:
                    durations.append(float(duration))
        return ImportJob(
            id=record.id,
            project_id=record.project_id,
            status=record.status,
            created_at=self._iso(record.created_at) or datetime.now(timezone.utc).isoformat(),
            updated_at=self._iso(record.updated_at) or datetime.now(timezone.utc).isoformat(),
            input_files=list(record.input_files or []),
            entities=dict(record.entities or {}),
            source_app=record.source_app,
            infer=bool(record.infer),
            total_input_files=int(record.total_input_files or 0),
            workspace=record.workspace,
            workspace_bytes=int(getattr(record, "workspace_bytes", 0) or 0),
            source_retry_required=bool(getattr(record, "source_retry_required", True)),
            options_snapshot=options_snapshot,
            storage_quota_snapshot=dict(getattr(record, "storage_quota_snapshot", None) or {}),
            phase=record.phase,
            started_at=self._iso(record.started_at),
            completed_at=self._iso(record.finished_at),
            discovered_files=int(record.discovered_files or 0),
            parsed_files=int(record.parsed_files or 0),
            skipped_files=int(record.skipped_files or 0),
            total_conversations=int(record.total_conversations or 0),
            total_chunks=int(record.total_chunks or 0),
            processed_chunks=int(record.processed_chunks or 0),
            imported_chunks=int(record.imported_chunks or 0),
            skipped_chunks=int(record.skipped_chunks or 0),
            failed_chunks=int(record.failed_chunks or 0),
            retried_chunks=int(record.retried_chunks or 0),
            split_chunks=int(record.split_chunks or 0),
            memories_created=int(record.memories_created or 0),
            configured_workers=int(record.worker_count or 1),
            active_workers=int(record.active_workers or 0),
            current_concurrency=int(getattr(record, "current_concurrency", None) or record.worker_count or 1),
            peak_workers=int(record.peak_workers or 0),
            retry_count=int(record.retry_count or 0),
            total_tokens=int(record.total_tokens or 0),
            graph_status=record.graph_status,
            graph_error=record.graph_error,
            graph_attempts=int(record.graph_attempts or 0),
            graph_pending_items=int(record.graph_pending_items or 0),
            graph_synced_items=int(record.graph_synced_items or 0),
            graph_failed_items=int(record.graph_failed_items or 0),
            current_file=record.current_file,
            current_conversation=record.current_conversation,
            errors=errors,
            error_count=int(record.error_count or 0),
            phase_durations={key: float(value) for key, value in (record.phase_durations or {}).items()},
            chunk_durations=durations,
            cancel_requested=bool(record.cancel_requested),
        )

    def create(
        self,
        project_id: str,
        input_files: list[str],
        options: ImportOptions,
        *,
        job_id: str | None = None,
        workspace: str | None = None,
        status: JobStatus = "queued",
        lease_owner: str | None = None,
        lease_expires_at: datetime | str | None = None,
        storage_quota_snapshot: Mapping[str, Any] | None = None,
        max_active_jobs: int | None = None,
        max_retained_workspace_bytes: int | None = None,
    ) -> ImportJob:
        now = datetime.now(timezone.utc).isoformat()
        job = ImportJob(
            id=job_id or str(uuid.uuid4()),
            project_id=project_id,
            status=status,
            created_at=now,
            updated_at=now,
            input_files=input_files,
            entities=dict(options.entities),
            source_app=options.source_app,
            infer=options.infer,
            total_input_files=len(input_files),
            configured_workers=options.workers,
            current_concurrency=options.workers,
            workspace=workspace,
            options_snapshot=asdict(options),
            storage_quota_snapshot=dict(storage_quota_snapshot or {}),
            phase=status,
        )
        with self._lock:
            if self._repository is not None:
                create_job = (
                    self._repository.create_job_with_active_limit
                    if max_active_jobs is not None
                    else self._repository.create_job
                )
                create_values = dict(
                    id=job.id,
                    project_id=project_id,
                    status=job.status,
                    phase=job.phase,
                    input_files=job.input_files,
                    entities=job.entities,
                    options=job.options_snapshot,
                    storage_quota_snapshot=job.storage_quota_snapshot,
                    workspace=workspace,
                    source_app=job.source_app,
                    infer=job.infer,
                    total_input_files=job.total_input_files,
                    skipped_files=job.skipped_files,
                    worker_count=options.workers,
                    current_concurrency=options.workers,
                    source_retry_required=True,
                    workspace_bytes=0,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
                if lease_owner is not None:
                    create_values["lease_owner"] = lease_owner
                if lease_expires_at is not None:
                    create_values["lease_expires_at"] = lease_expires_at
                if max_active_jobs is None:
                    create_job(**create_values)
                else:
                    create_job(
                        max_active_jobs,
                        max_retained_bytes=max_retained_workspace_bytes,
                        **create_values,
                    )
            elif max_active_jobs is not None:
                active_jobs = sum(
                    item.project_id == project_id
                    and item.status
                    in {"uploading", "queued", "discovering", "parsing", "importing", "syncing_graph", "cancelling"}
                    for item in self._jobs.values()
                )
                if active_jobs >= max_active_jobs:
                    raise ImportActiveJobLimitExceeded(project_id, max_active_jobs, active_jobs)
            self._jobs[job.id] = job
            terminal = [
                item
                for item in sorted(self._jobs.values(), key=lambda value: value.created_at)
                if item.status in {"cancelled", "completed", "completed_with_errors", "failed"}
            ]
            while len(self._jobs) > self._max_jobs and terminal:
                self._jobs.pop(terminal.pop(0).id, None)
        return job

    def forget(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)

    def get(
        self,
        job_id: str,
        project_id: str | None = None,
        *,
        refresh: bool = False,
    ) -> ImportJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if self._repository is not None and (refresh or job is None):
                record = self._repository.get_job(job_id, project_id)
                if record is not None:
                    job = self._from_record(record)
                    self._jobs[job.id] = job
                elif refresh:
                    self._jobs.pop(job_id, None)
                    job = None
            if job is None or (project_id is not None and job.project_id != project_id):
                return None
            return job

    def update(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        **changes: Any,
    ) -> ImportJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            status = changes.get("status", job.status)
            started_at = changes.get("started_at", job.started_at)
            if started_at is None and status not in {"queued", "cancelled"}:
                started_at = now
            completed_at = changes.get("completed_at", job.completed_at)
            if status in {"cancelled", "completed", "completed_with_errors", "failed"}:
                completed_at = completed_at or now
            if self._repository is not None:
                field_map = {
                    "completed_at": "finished_at",
                    "configured_workers": "worker_count",
                    "options_snapshot": "options",
                }
                values = {
                    field_map.get(key, key): value
                    for key, value in changes.items()
                    if key not in {"errors", "chunk_durations"}
                }
                values["updated_at"] = now
                if started_at and "started_at" not in values:
                    values["started_at"] = started_at
                if completed_at:
                    values["finished_at"] = completed_at
                if self._repository.update_job(job_id, lease_owner=lease_owner, **values) is None:
                    return None
            for key, value in changes.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.started_at = started_at
            job.completed_at = completed_at
            job.updated_at = now
            return job

    def increment(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        **changes: int,
    ) -> ImportJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if self._repository is not None:
                field_map = {"configured_workers": "worker_count"}
                if (
                    self._repository.increment_job(
                        job_id,
                        lease_owner=lease_owner,
                        **{field_map.get(key, key): value for key, value in changes.items()},
                    )
                    is None
                ):
                    return None
            for key, value in changes.items():
                setattr(job, key, int(getattr(job, key)) + value)
            job.updated_at = datetime.now(timezone.utc).isoformat()
            return job

    def record_phase(
        self,
        job_id: str,
        phase: str,
        seconds: float,
        *,
        lease_owner: str | None = None,
    ) -> None:
        self.record_phases(job_id, {phase: seconds}, lease_owner=lease_owner)

    def record_phases(
        self,
        job_id: str,
        durations: Mapping[str, float],
        *,
        lease_owner: str | None = None,
    ) -> None:
        valid = {key: float(value) for key, value in durations.items() if float(value) >= 0}
        if not valid:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            phase_durations = dict(job.phase_durations)
            for phase, seconds in valid.items():
                phase_durations[phase] = phase_durations.get(phase, 0.0) + seconds
            updated_at = datetime.now(timezone.utc).isoformat()
            if self._repository is not None:
                if (
                    self._repository.update_job(
                        job_id,
                        lease_owner=lease_owner,
                        phase_durations=phase_durations,
                        updated_at=updated_at,
                    )
                    is None
                ):
                    return
            job.phase_durations = phase_durations
            job.updated_at = updated_at

    def record_chunk_duration(self, job_id: str, seconds: float) -> None:
        if seconds < 0:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.chunk_durations.append(float(seconds))
            job.updated_at = datetime.now(timezone.utc).isoformat()

    def add_error(
        self,
        job_id: str,
        source: str,
        message: str,
        *,
        error_type: str = "import_error",
        retryable: bool = False,
        attempt: int | None = None,
        import_key: str | None = None,
        error_code: str | None = None,
        error_details: Mapping[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            details = dict(error_details or {})
            if import_key:
                details["import_key"] = import_key
            if self._repository is not None:
                chunk = self._repository.get_chunk(job_id, import_key) if import_key else None
                self._repository.add_error(
                    job_id,
                    source,
                    message,
                    lease_owner=lease_owner,
                    phase=job.phase,
                    error_type=error_type[:255],
                    error_code=error_code[:128] if error_code else None,
                    attempt=attempt,
                    retryable=retryable,
                    chunk_id=getattr(chunk, "id", None),
                    details=details,
                )
            job.error_count += 1
            if len(job.errors) < MAX_JOB_ERRORS:
                error: dict[str, Any] = {
                    "source": source[:300],
                    "message": message[:1000],
                    "type": error_type[:80],
                    "retryable": bool(retryable),
                }
                if attempt is not None:
                    error["attempt"] = int(attempt)
                if error_code:
                    error["code"] = error_code[:128]
                if details:
                    error["details"] = details
                if import_key:
                    error["import_key"] = import_key[:64]
                job.errors.append(error)
            job.updated_at = datetime.now(timezone.utc).isoformat()

    def request_cancel(self, job_id: str, project_id: str) -> ImportJob | None:
        with self._lock:
            if self._repository is not None:
                record = self._repository.request_job_cancel(job_id, project_id)
                if record is None:
                    record = self._repository.get_job(job_id, project_id)
                if record is None:
                    self._jobs.pop(job_id, None)
                    return None
                job = self._from_record(record)
                self._jobs[job.id] = job
                return job

            job = self._jobs.get(job_id)
            if job is None or job.project_id != project_id:
                return None
            if job.status in {"queued", "discovering", "parsing", "importing", "syncing_graph"}:
                job.cancel_requested = True
                job.status = "cancelling"
                job.updated_at = datetime.now(timezone.utc).isoformat()
                if self._repository is not None:
                    self._repository.update_job(
                        job_id,
                        cancel_requested=True,
                        status="cancelling",
                        updated_at=job.updated_at,
                    )
            return job

    def list(self, project_id: str, limit: int = 20) -> list[ImportJob]:
        with self._lock:
            if self._repository is not None:
                jobs = [self._from_record(record) for record in self._repository.list_jobs(project_id, limit)]
                for job in jobs:
                    self._jobs[job.id] = job
                return jobs
            jobs = [job for job in self._jobs.values() if job.project_id == project_id]
            return sorted(jobs, key=lambda value: value.created_at, reverse=True)[:limit]

    @staticmethod
    def serialize(job: ImportJob) -> dict[str, Any]:
        data = asdict(job)
        data["source_retry_available"] = bool(
            job.source_retry_required and job.status in {"cancelled", "completed_with_errors", "failed"}
        )
        data.pop("project_id", None)
        data.pop("cancel_requested", None)
        data.pop("workspace", None)
        data.pop("workspace_bytes", None)
        data.pop("source_retry_required", None)
        data.pop("options_snapshot", None)
        data.pop("storage_quota_snapshot", None)
        durations = data.pop("chunk_durations", [])
        average = statistics.fmean(durations) if durations else 0.0
        if durations:
            ordered = sorted(durations)
            p95_index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.95) - 1))
            p95 = ordered[p95_index]
        else:
            p95 = 0.0
        started_at = _timestamp_seconds(job.started_at or job.created_at)
        terminal = job.status in {"cancelled", "completed", "completed_with_errors", "failed"}
        ended_at = _timestamp_seconds(job.completed_at or job.updated_at or job.created_at) if terminal else time.time()
        elapsed = max(0.0, ended_at - started_at) if started_at is not None and ended_at is not None else 0.0
        throughput = job.processed_chunks / elapsed if elapsed > 0 else 0.0
        remaining = max(0, job.total_chunks - job.processed_chunks)
        data["metrics"] = {
            "throughput_chunks_per_minute": round(throughput * 60, 3),
            "average_chunk_seconds": round(average, 3),
            "p95_chunk_seconds": round(p95, 3),
            "failure_rate": round(job.failed_chunks / job.processed_chunks, 4) if job.processed_chunks else 0.0,
            "eta_seconds": round(remaining / throughput, 1) if throughput > 0 and remaining else 0.0,
            "phase_durations_ms": {key: round(value * 1000, 2) for key, value in sorted(job.phase_durations.items())},
        }
        data["poll_after_ms"] = (
            3000 if job.status not in {"cancelled", "completed", "completed_with_errors", "failed"} else None
        )
        return data


import_jobs = ImportJobStore()


def _response_memory_count(response: Any) -> int:
    if isinstance(response, Mapping) and isinstance(response.get("response"), Mapping):
        response = response["response"]
    if isinstance(response, Mapping):
        results = response.get("results")
        return len(results) if isinstance(results, list) else 0
    return 0


@dataclass
class ChunkExecution:
    job_id: str
    conversation: Conversation
    chunk: MessageChunk
    chunk_index: int
    chunk_count: int
    import_key: str
    attempt: int
    phase_callback: Callable[[str, float], None]
    force_fallback_reason: str | None
    audit: bool
    obvious_facts: bool
    reconcile_existing: bool = False
    lease_owner: str | None = None


@dataclass
class ImportRuntimeHooks:
    load_existing_keys: Callable[[str, set[str]], set[str]] | None = None
    load_chunk_statuses: Callable[[str], Mapping[str, str]] | None = None
    claim_chunk: Callable[[ChunkExecution, dict[str, Any]], str] | None = None
    update_chunk: Callable[[ChunkExecution, str, dict[str, Any]], None] | None = None
    sync_graph: Callable[[str], str | None] | None = None
    finalize_workspace: Callable[[str, bool], None] | None = None


class ImportCancelled(RuntimeError):
    pass


class PermanentImportError(RuntimeError):
    """Explicitly mark a non-provider import failure that cannot succeed on retry."""


_PERMANENT_IMPORT_HTTP_STATUSES = {400, 401, 403, 404}
_PERMANENT_IMPORT_EXCEPTION_NAMES = {
    "AuthenticationError",
    "ConfigurationError",
    "ConfigError",
    "PermissionDeniedError",
    "ValidationError",
}
_STRUCTURAL_EXTRACTION_REASONS = {
    "invalid_json",
    "invalid_schema",
    "missing_core_evidence",
}


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def is_permanent_import_error(exc: BaseException) -> bool:
    for current in _exception_chain(exc):
        if isinstance(current, PermanentImportError):
            return True
        if _status_code(current) in _PERMANENT_IMPORT_HTTP_STATUSES:
            return True
        if isinstance(current, (AssertionError, KeyError, PermissionError, TypeError, ValueError)):
            return True
        if type(current).__name__ in _PERMANENT_IMPORT_EXCEPTION_NAMES:
            return True
    return False


def is_import_capacity_error(exc: BaseException) -> bool:
    for current in _exception_chain(exc):
        if getattr(current, "memory_import_capacity_error", False) is True:
            return True
    return False


def _status_code(exc: BaseException) -> int | None:
    for current in _exception_chain(exc):
        for candidate in (
            getattr(current, "status_code", None),
            getattr(getattr(current, "response", None), "status_code", None),
        ):
            try:
                if candidate is not None:
                    return int(candidate)
            except (TypeError, ValueError):
                pass
    return None


def _error_text(exc: Exception) -> str:
    messages = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if str(current).strip():
            messages.append(str(current).strip().lower())
        current = current.__cause__ or current.__context__
    return " | ".join(messages)


def _diagnostic_identifier(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", candidate):
        return None
    return candidate


def _snake_case_identifier(value: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", value).replace("-", "_").replace(".", "_")
    return re.sub(r"_+", "_", separated).strip("_").lower()[:128]


def _safe_import_error_message(exc: BaseException) -> str:
    message = str(exc).strip() or type(exc).__name__
    message = redact_secrets(message)
    return re.sub(r"\s+", " ", message).strip()[:1000]


def _import_error_diagnostics(exc: BaseException) -> tuple[str, dict[str, Any]]:
    chain = list(_exception_chain(exc))
    root = chain[-1]
    validation_reason = next(
        (value for current in chain if (value := _diagnostic_identifier(getattr(current, "reason", None))) is not None),
        None,
    )
    operation_phase = next(
        (
            value
            for current in chain
            if (value := _diagnostic_identifier(getattr(current, "import_subphase", None))) is not None
        ),
        None,
    )
    provider_error_code = next(
        (
            value
            for current in chain
            for candidate in (
                getattr(current, "code", None),
                getattr(getattr(current, "error", None), "code", None),
            )
            if (value := _diagnostic_identifier(candidate)) is not None
        ),
        None,
    )
    status_code = _status_code(exc)
    sqlite_error_code = getattr(root, "sqlite_errorcode", None)
    sqlite_error_name = _diagnostic_identifier(getattr(root, "sqlite_errorname", None))

    outer_text = str(exc).lower()
    failure_point = None
    for marker, candidate in (
        ("query embedding", "query_embedding"),
        ("memory embedding", "memory_embedding"),
        ("entity embedding", "entity_embedding"),
        ("model call", "model_call"),
    ):
        if marker in outer_text:
            failure_point = candidate
            break
    if failure_point is None and operation_phase:
        failure_point = operation_phase

    details: dict[str, Any] = {
        "root_exception_module": type(root).__module__[:255],
        "root_exception_type": type(root).__name__[:255],
    }
    if operation_phase:
        details["operation_phase"] = operation_phase
    if failure_point:
        details["failure_point"] = failure_point
    if validation_reason:
        details["validation_reason"] = validation_reason
    if status_code is not None:
        details["status_code"] = status_code
    if provider_error_code:
        details["provider_error_code"] = provider_error_code
    if isinstance(sqlite_error_code, int) and not isinstance(sqlite_error_code, bool):
        details["sqlite_errorcode"] = sqlite_error_code
    if sqlite_error_name:
        details["sqlite_errorname"] = sqlite_error_name

    error_code = (
        validation_reason
        or provider_error_code
        or (sqlite_error_name.lower() if sqlite_error_name else None)
        or (f"http_{status_code}" if status_code is not None else None)
        or failure_point
        or _snake_case_identifier(type(root).__name__)
        or "import_error"
    )
    return error_code[:128], details


def is_pressure_error(exc: Exception) -> bool:
    status = _status_code(exc)
    text = _error_text(exc)
    return status in {408, 429, 502, 503, 504} or any(
        marker in text for marker in ("429", "rate limit", "too many requests", "timed out", "timeout")
    )


def is_timeout_error(exc: Exception) -> bool:
    status = _status_code(exc)
    text = _error_text(exc)
    return status in {408, 504} or any(marker in text for marker in ("timed out", "timeout"))


def is_structural_chunk_error(exc: Exception) -> bool:
    if any(
        str(getattr(current, "reason", "")).strip().lower() in _STRUCTURAL_EXTRACTION_REASONS
        for current in _exception_chain(exc)
    ):
        return True
    text = _error_text(exc)
    return any(
        marker in text
        for marker in (
            "truncated",
            "output limit",
            "max_tokens",
            "invalid json",
            "schema",
            "missing the 'memory'",
            "cites only overlap evidence",
        )
    )


class AdaptiveConcurrency:
    def __init__(
        self,
        target: int,
        *,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 30.0,
        retry_jitter: float = 0.35,
        random_source: Callable[[], float] = random.random,
    ):
        self.target = max(1, min(int(target), 4))
        self.current = self.target
        self.active = 0
        self.consecutive_failures = 0
        self.success_streak = 0
        self.cooldown_until = 0.0
        self._cooldown_generation = 0
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self.retry_jitter = max(0.0, float(retry_jitter))
        self.random_source = random_source
        self._condition = threading.Condition()

    def acquire(self, cancelled: Callable[[], bool]) -> None:
        with self._condition:
            while True:
                if cancelled():
                    raise ImportCancelled()
                cooldown_remaining = max(0.0, self.cooldown_until - time.monotonic())
                if self.active < self.current and cooldown_remaining <= 0:
                    self.active += 1
                    return
                self._condition.wait(timeout=min(0.1, cooldown_remaining) if cooldown_remaining else 0.1)

    def release(self) -> None:
        with self._condition:
            self.active = max(0, self.active - 1)
            self._condition.notify_all()

    def record_failure(self, exc: Exception) -> int | None:
        with self._condition:
            self.consecutive_failures += 1
            self.success_streak = 0
            cooldown_token = None
            if is_pressure_error(exc) or self.consecutive_failures >= 2:
                self.current = max(1, self.current - 1)
                penalty = min(
                    self.retry_max_seconds, self.retry_base_seconds * (2 ** min(6, self.consecutive_failures - 1))
                )
                candidate = time.monotonic() + penalty
                if candidate >= self.cooldown_until:
                    self.cooldown_until = candidate
                    self._cooldown_generation += 1
                    cooldown_token = self._cooldown_generation
            self._condition.notify_all()
            return cooldown_token

    def record_pressure(self) -> int | None:
        return self.record_failure(TimeoutError("provider timeout recovered by fallback"))

    def cooldown_seconds(self) -> float:
        with self._condition:
            return max(0.0, self.cooldown_until - time.monotonic())

    def complete_backoff(self, cooldown_token: int | None) -> None:
        if cooldown_token is None:
            return
        with self._condition:
            if cooldown_token == self._cooldown_generation:
                self.cooldown_until = min(self.cooldown_until, time.monotonic())
                self._condition.notify_all()

    def record_success(self) -> None:
        with self._condition:
            self.consecutive_failures = 0
            self.success_streak += 1
            threshold = max(4, self.current * 2)
            if (
                self.current < self.target
                and self.success_streak >= threshold
                and time.monotonic() >= self.cooldown_until
            ):
                self.current += 1
                self.success_streak = 0
            self._condition.notify_all()

    def backoff_seconds(self, failed_attempt: int) -> float:
        base = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** max(0, failed_attempt)))
        return base + base * self.retry_jitter * self.random_source()


def _chunk_complexity_reason(chunk: MessageChunk) -> str | None:
    if chunk.token_count >= COMPLEX_CHUNK_MIN_TOKENS:
        return "long_chunk"

    text = "\n".join(message.content for message in chunk.messages)
    lines = text.splitlines()
    table_rows = sum(line.count("|") >= 2 for line in lines)
    tabbed_lines = sum("\t" in line for line in lines)
    fenced_code_lines = 0
    active_fence: str | None = None
    for line in lines:
        stripped = line.lstrip()
        fence = "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else None
        if fence:
            active_fence = None if active_fence == fence else fence
        elif active_fence:
            fenced_code_lines += 1

    large_table = table_rows >= COMPLEX_TABLE_MIN_ROWS and text.count("|") >= COMPLEX_TABLE_MIN_PIPES
    substantial_code = fenced_code_lines >= COMPLEX_CODE_MIN_LINES
    tab_heavy = tabbed_lines >= COMPLEX_TABBED_MIN_LINES
    mixed_table_and_code = (
        table_rows >= COMPLEX_MIXED_TABLE_MIN_ROWS and fenced_code_lines >= COMPLEX_MIXED_CODE_MIN_LINES
    )
    if large_table or substantial_code or tab_heavy or mixed_table_and_code:
        return "complex_structure"
    return None


def _contains_obvious_facts(chunk: MessageChunk) -> bool:
    text = " ".join(message.content for message in chunk.messages if message.role != "system")
    if len(text.strip()) < 24:
        return False
    return bool(
        re.search(
            r"\b(i am|i'm|i have|i prefer|i like|i work|my |we |born|live in|started|finished)\b|"
            r"\d{4}|[\u4e00-\u9fff]{2,}(?:喜欢|偏好|工作|居住|计划|开始|完成|生日|名字)",
            text,
            re.IGNORECASE,
        )
    )


def _is_audit_sample(import_key: str, audit_ratio: float) -> bool:
    if audit_ratio <= 0:
        return False
    if audit_ratio >= 1:
        return True
    digest = hashlib.sha256(f"chat-import-audit-v1:{import_key}".encode("utf-8")).digest()
    sample = int.from_bytes(digest[:8], "big")
    return sample < int(audit_ratio * (1 << 64))


def split_message_chunk(chunk: MessageChunk, parent_import_key: str) -> list[MessageChunk]:
    turn_limit = max(1000, min(3000, max(1, chunk.token_count // 2)))
    parent_core = set(chunk.core_source_indices)
    core_messages = [
        message
        for message in chunk.messages
        if message.source_index is not None and int(message.source_index) in parent_core
    ]
    turns = _build_turns(core_messages, turn_limit)
    if len(turns) < 2:
        piece_limit = max(500, chunk.token_count // 2)
        pieces = [piece for message in core_messages for piece in split_long_message_tokens(message, piece_limit)]
        turns = [ConversationTurn([piece], estimate_tokens(piece.content) + 6) for piece in pieces]
    if len(turns) < 2:
        return []
    target = sum(turn.token_count for turn in turns) / 2
    turn_core_indices = [
        {
            int(message.source_index)
            for message in turn.messages
            if message.source_index is not None and int(message.source_index) in parent_core
        }
        for turn in turns
    ]
    suffix_core_indices = [set() for _ in range(len(turns) + 1)]
    for index in range(len(turns) - 1, -1, -1):
        suffix_core_indices[index] = suffix_core_indices[index + 1] | turn_core_indices[index]
    running = 0
    prefix_core_indices: set[int] = set()
    candidates: list[tuple[int, int, float, int]] = []
    for index, turn in enumerate(turns[:-1], start=1):
        running += turn.token_count
        prefix_core_indices.update(turn_core_indices[index - 1])
        suffix_core = suffix_core_indices[index]
        if prefix_core_indices and suffix_core:
            previous_role = turn.messages[-1].role if turn.messages else ""
            following_role = turns[index].messages[0].role if turns[index].messages else ""
            splits_question_from_answer = int(previous_role == "user" and following_role == "assistant")
            shared_sources = len(prefix_core_indices & suffix_core)
            candidates.append((splits_question_from_answer, shared_sources, abs(running - target), index))
    if not candidates:
        return []
    split_at = min(candidates)[3]
    groups = [turns[:split_at], turns[split_at:]]
    overlap_indices_by_group: list[set[int]] = [set(), set()]

    # Preserve a short question as context when an emergency split must start
    # the second child in the middle of an assistant response.
    if groups[1] and groups[1][0].messages and groups[1][0].messages[0].role == "assistant":
        suffix_indices = {
            int(message.source_index)
            for turn in groups[1]
            for message in turn.messages
            if message.source_index is not None
        }
        previous_user = next(
            (
                message
                for turn in reversed(groups[0])
                for message in reversed(turn.messages)
                if message.role == "user"
                and message.source_index is not None
                and int(message.source_index) not in suffix_indices
                and estimate_tokens(message.content) <= 500
            ),
            None,
        )
        if previous_user is not None:
            groups[1] = [ConversationTurn([previous_user], estimate_tokens(previous_user.content) + 6), *groups[1]]
            overlap_indices_by_group[1].add(int(previous_user.source_index))

    children: list[MessageChunk] = []
    for group_index, group in enumerate(groups):
        child_indices = {
            int(message.source_index) for turn in group for message in turn.messages if message.source_index is not None
        }
        child_core = (child_indices - overlap_indices_by_group[group_index]) & parent_core
        if not group or not child_core:
            return []
        leading_overlap = 0
        for turn in group:
            turn_indices = {int(message.source_index) for message in turn.messages if message.source_index is not None}
            if turn_indices & child_core:
                break
            leading_overlap += 1
        children.append(
            _chunk_from_turns(
                group,
                leading_overlap=leading_overlap,
                split_depth=chunk.split_depth + 1,
                parent_import_key=parent_import_key,
                core_source_indices=child_core,
            )
        )
    return children


def _message_chunk_import_key(
    conversation: Conversation,
    chunk: MessageChunk,
    options: ImportOptions,
) -> str:
    payload = build_payload(conversation, chunk, 0, 1, options)
    return str(payload["metadata"]["import_key"])


def resolve_missing_import_key_schema_version(
    initial_chunks: list[tuple[Conversation, list[MessageChunk]]],
    options: ImportOptions,
    persisted_statuses: Mapping[str, str],
) -> bool:
    """Resolve an unversioned snapshot from persisted deterministic root keys."""
    if not getattr(options, "_import_key_schema_version_missing", False):
        return False

    persisted_keys = {str(import_key) for import_key in persisted_statuses}
    selected_version = CURRENT_IMPORT_KEY_SCHEMA_VERSION
    if persisted_keys:
        matching_versions: list[int] = []
        for version in (LEGACY_IMPORT_KEY_SCHEMA_VERSION, CURRENT_IMPORT_KEY_SCHEMA_VERSION):
            candidate_options = replace(options, import_key_schema_version=version)
            root_keys = {
                _message_chunk_import_key(conversation, chunk, candidate_options)
                for conversation, chunks in initial_chunks
                for chunk in chunks
            }
            if root_keys & persisted_keys:
                matching_versions.append(version)

        if len(matching_versions) != 1:
            match_description = "both v1 and v2" if matching_versions else "neither v1 nor v2"
            raise RuntimeError(
                "Cannot safely infer the import-key schema version: persisted chunk keys match "
                f"{match_description} deterministic root keys."
            )
        selected_version = matching_versions[0]

    options.import_key_schema_version = selected_version
    delattr(options, "_import_key_schema_version_missing")
    return True


def expand_persisted_split_chunks(
    conversation: Conversation,
    chunks: list[MessageChunk],
    options: ImportOptions,
    statuses: Mapping[str, str],
    superseded_leaf_keys: set[str] | None = None,
) -> list[MessageChunk]:
    """Rebuild persisted split trees as ordered leaves without re-running their parents."""

    superseded_leaves = superseded_leaf_keys if superseded_leaf_keys is not None else set()

    def expand(
        chunk: MessageChunk,
        ancestors: frozenset[str],
        inherited_superseded: bool,
    ) -> list[MessageChunk]:
        import_key = _message_chunk_import_key(conversation, chunk, options)
        status = str(statuses.get(import_key, "")).lower()
        superseded = inherited_superseded or status == "superseded_split"
        if status not in {"split", "superseded_split"}:
            if superseded and status != "succeeded":
                superseded_leaves.add(import_key)
            return [chunk]
        if import_key in ancestors:
            raise RuntimeError("Persisted import split tree contains a cycle.")
        children = split_message_chunk(chunk, import_key)
        if len(children) != 2:
            raise RuntimeError("Persisted import split parent can no longer be expanded deterministically.")
        expanded: list[MessageChunk] = []
        next_ancestors = ancestors | {import_key}
        for child in children:
            expanded.extend(expand(child, next_ancestors, superseded))
        return expanded

    leaves: list[MessageChunk] = []
    for chunk in chunks:
        leaves.extend(expand(chunk, frozenset(), False))
    return leaves


def _interruptible_sleep(
    job_id: str,
    delay: float,
    external_cancelled: Callable[[], bool] | None = None,
) -> bool:
    job = import_jobs.get(job_id)
    if job is None or job.cancel_requested or (external_cancelled is not None and external_cancelled()):
        return False
    remaining = max(0.0, delay)
    if external_cancelled is None:
        time.sleep(remaining)
    else:
        deadline = time.monotonic() + remaining
        while remaining > 0:
            if external_cancelled():
                return False
            time.sleep(min(0.1, remaining))
            remaining = deadline - time.monotonic()
    job = import_jobs.get(job_id)
    return job is not None and not job.cancel_requested and (external_cancelled is None or not external_cancelled())


def run_import_job(
    job_id: str,
    input_paths: list[Path],
    input_root: Path,
    extraction_root: Path,
    options: ImportOptions,
    store_payload: Callable[[dict[str, Any]], Any],
    is_duplicate: Callable[[str], bool] | None = None,
    display_root: Path | None = None,
    *,
    store_payload_with_context: Callable[[dict[str, Any], ChunkExecution], Any] | None = None,
    hooks: ImportRuntimeHooks | None = None,
    retain_workspace: bool = False,
    external_cancelled: Callable[[], bool] | None = None,
    lease_owner: str | None = None,
) -> None:
    hooks = hooks or ImportRuntimeHooks()
    parse_started = time.perf_counter()
    had_retryable_parse_failures = False
    fence_lost = threading.Event()

    def externally_cancelled() -> bool:
        return fence_lost.is_set() or (external_cancelled is not None and external_cancelled())

    def update_job(**changes: Any) -> ImportJob | None:
        return import_jobs.update(job_id, lease_owner=lease_owner, **changes)

    def increment_job(**changes: int) -> ImportJob | None:
        return import_jobs.increment(job_id, lease_owner=lease_owner, **changes)

    def add_job_error(source: str, message: str, **details: Any) -> None:
        import_jobs.add_error(job_id, source, message, lease_owner=lease_owner, **details)

    def add_job_exception(
        source: str,
        exc: BaseException,
        *,
        operation_phase: str | None = None,
        **details: Any,
    ) -> None:
        error_code, error_details = _import_error_diagnostics(exc)
        if operation_phase:
            error_details.setdefault("operation_phase", operation_phase)
            error_details.setdefault("failure_point", operation_phase)
        add_job_error(
            source,
            _safe_import_error_message(exc),
            error_code=error_code,
            error_details=error_details,
            **details,
        )

    def record_job_phase(phase: str, seconds: float) -> None:
        import_jobs.record_phase(job_id, phase, seconds, lease_owner=lease_owner)

    def record_job_phases(durations: Mapping[str, float]) -> None:
        import_jobs.record_phases(job_id, durations, lease_owner=lease_owner)

    interrupt_cancelled = externally_cancelled if lease_owner is not None or external_cancelled is not None else None

    def cancelled() -> bool:
        current_job = import_jobs.get(job_id)
        return current_job is None or current_job.cancel_requested or externally_cancelled()

    def mark_job_cancelled(**changes: Any) -> None:
        if not externally_cancelled():
            changes.setdefault("source_retry_required", True)
            update_job(status="cancelled", **changes)

    try:
        job = import_jobs.get(job_id)
        if job is None:
            return
        if cancelled():
            mark_job_cancelled()
            return
        update_job(status="discovering", phase="parsing")
        discovery = discover_input_files(input_paths, extraction_root, input_root=display_root or input_root)
        job = import_jobs.get(job_id)
        if job is None or cancelled():
            mark_job_cancelled()
            return
        update_job(
            discovered_files=len(discovery.files),
            skipped_files=(job.skipped_files if job else 0) + discovery.skipped_files,
        )
        for warning in discovery.warnings:
            add_job_error("discovery", warning)
        if not discovery.files:
            raise ValueError("No supported chat history files were found.")

        conversations: list[Conversation] = []
        update_job(status="parsing")
        for source_file in discovery.files:
            job = import_jobs.get(job_id)
            if job is None or cancelled():
                mark_job_cancelled(current_file=None, current_conversation=None)
                return
            update_job(current_file=source_file.display_path)
            try:
                parsed = parse_file(source_file.path, options.source_app, source_file.display_path)
                if not parsed:
                    had_retryable_parse_failures = True
                    increment_job(skipped_files=1)
                    add_job_error(
                        source_file.display_path,
                        "No conversations were found in this file.",
                        error_type="no_conversations",
                        retryable=True,
                    )
                else:
                    conversations.extend(parsed)
            except Exception as exc:
                if not externally_cancelled():
                    had_retryable_parse_failures = True
                    increment_job(skipped_files=1)
                    add_job_exception(
                        source_file.display_path,
                        exc,
                        operation_phase="parsing",
                        error_type="parse_error",
                        retryable=True,
                    )
            finally:
                if not externally_cancelled():
                    increment_job(parsed_files=1)

            if externally_cancelled():
                return

        persisted_chunk_statuses: Mapping[str, str] = {}
        if hooks.load_chunk_statuses is not None:
            persisted_chunk_statuses = dict(hooks.load_chunk_statuses(job_id))

        initial_chunks = [
            (conversation, adaptive_chunk_messages(conversation.messages, options)) for conversation in conversations
        ]
        if resolve_missing_import_key_schema_version(initial_chunks, options, persisted_chunk_statuses):
            update_job(options_snapshot=asdict(options))

        prepared: list[tuple[Conversation, list[MessageChunk]]] = []
        superseded_leaf_keys: set[str] = set()
        for conversation, chunks in initial_chunks:
            if chunks and persisted_chunk_statuses:
                chunks = expand_persisted_split_chunks(
                    conversation,
                    chunks,
                    options,
                    persisted_chunk_statuses,
                    superseded_leaf_keys,
                )
            if chunks:
                prepared.append((conversation, chunks))
        if externally_cancelled():
            return
        total_chunks = sum(len(chunks) for _, chunks in prepared)
        total_tokens = sum(chunk.token_count for _, chunks in prepared for chunk in chunks)
        update_job(
            status="importing",
            phase="extracting",
            total_conversations=len(conversations),
            total_chunks=total_chunks,
            total_tokens=total_tokens,
            current_file=None,
        )
        record_job_phase("parsing", time.perf_counter() - parse_started)
        if not prepared:
            if not had_retryable_parse_failures:
                had_retryable_parse_failures = True
                add_job_error(
                    "parsing",
                    "The selected files did not contain any importable messages.",
                    error_type="no_conversations",
                    retryable=True,
                )
            if hooks.finalize_workspace is not None:
                hooks.finalize_workspace(job_id, True)
            update_job(
                status="completed_with_errors",
                phase="completed",
                graph_status="skipped",
                source_retry_required=True,
                current_file=None,
                current_conversation=None,
                active_workers=0,
            )
            return

        payloads: dict[tuple[str, str, int], dict[str, Any]] = {}
        all_keys: set[str] = set()
        for conversation, chunks in prepared:
            for chunk_index, chunk in enumerate(chunks):
                payload = build_payload(conversation, chunk, chunk_index, len(chunks), options)
                payloads[(conversation.source_path, conversation.id, chunk_index)] = payload
                all_keys.add(str(payload["metadata"]["import_key"]))

        existing_keys: set[str] = set()
        dedup_started = time.perf_counter()
        if options.skip_duplicates and hooks.load_existing_keys is not None:
            existing_keys = set(hooks.load_existing_keys(job.project_id, all_keys))
        existing_keys.update(superseded_leaf_keys & all_keys)
        record_job_phase("deduplication", time.perf_counter() - dedup_started)

        controller = AdaptiveConcurrency(
            options.workers,
            retry_base_seconds=options.retry_base_seconds,
            retry_max_seconds=options.retry_max_seconds,
            retry_jitter=options.retry_jitter,
        )
        work_queue: queue.Queue[list[tuple[Conversation, list[MessageChunk]]]] = queue.Queue()
        conversation_groups: dict[str, list[tuple[Conversation, list[MessageChunk]]]] = {}
        for item in prepared:
            conversation_groups.setdefault(str(item[0].id), []).append(item)
        for group in conversation_groups.values():
            work_queue.put(group)

        def update_concurrency() -> None:
            current_job = import_jobs.get(job_id)
            peak_workers = max(current_job.peak_workers if current_job else 0, controller.active)
            update_job(
                current_concurrency=controller.current,
                active_workers=controller.active,
                peak_workers=peak_workers,
            )

        def process_chunk(
            conversation: Conversation,
            chunk: MessageChunk,
            chunk_index: int,
            chunk_count: int,
            initial_payload: dict[str, Any] | None = None,
        ) -> bool:
            if cancelled():
                return False
            payload = initial_payload or build_payload(conversation, chunk, chunk_index, chunk_count, options)
            import_key = str(payload["metadata"]["import_key"])
            base_execution = ChunkExecution(
                job_id=job_id,
                conversation=conversation,
                chunk=chunk,
                chunk_index=chunk_index,
                chunk_count=chunk_count,
                import_key=import_key,
                attempt=1,
                phase_callback=lambda _phase, _seconds: None,
                force_fallback_reason=_chunk_complexity_reason(chunk),
                audit=options.model_tiering_enabled and _is_audit_sample(import_key, options.audit_ratio),
                obvious_facts=_contains_obvious_facts(chunk),
                lease_owner=lease_owner,
            )

            def process_split_children(
                execution: ChunkExecution,
                children: list[MessageChunk],
                error: str,
            ) -> bool:
                if hooks.update_chunk:
                    hooks.update_chunk(
                        execution,
                        "split",
                        {"children": len(children), "error": error},
                    )
                increment_job(total_chunks=1, split_chunks=1)
                children_succeeded = True
                for child_index, child in enumerate(children):
                    if cancelled():
                        return False
                    child_succeeded = process_chunk(
                        conversation,
                        child,
                        child_index,
                        len(children),
                    )
                    children_succeeded = child_succeeded and children_succeeded
                return children_succeeded

            if import_key in existing_keys:
                increment_job(processed_chunks=1, skipped_chunks=1)
                if hooks.update_chunk:
                    hooks.update_chunk(
                        base_execution,
                        "skipped",
                        {"reason": ("superseded_split" if import_key in superseded_leaf_keys else "duplicate")},
                    )
                return True

            chunk_claimed = False
            if options.skip_duplicates and hooks.claim_chunk is not None:
                for claim_attempt in range(options.max_attempts):
                    if cancelled():
                        return False
                    claim_execution = ChunkExecution(
                        **{
                            **base_execution.__dict__,
                            "attempt": claim_attempt + 1,
                        }
                    )
                    claim = hooks.claim_chunk(claim_execution, payload)
                    if claim in {"succeeded", "duplicate"}:
                        increment_job(processed_chunks=1, skipped_chunks=1)
                        if hooks.update_chunk:
                            hooks.update_chunk(claim_execution, "skipped", {"reason": "duplicate"})
                        return True
                    if claim == "resume_succeeded":
                        return True
                    if claim == "split":
                        children = split_message_chunk(chunk, import_key)
                        if len(children) != 2:
                            raise RuntimeError(
                                "Persisted import split parent can no longer be expanded deterministically."
                            )
                        return process_split_children(
                            claim_execution,
                            children,
                            "Persisted split manifest required child expansion.",
                        )
                    if claim == "claimed":
                        base_execution.reconcile_existing = claim_execution.reconcile_existing
                        chunk_claimed = True
                        break
                    if claim != "busy":
                        raise RuntimeError(f"Unsupported import manifest claim result: {claim!r}")

                    claim_error = "Chunk is currently claimed by another import worker."
                    if claim_attempt + 1 >= options.max_attempts:
                        increment_job(processed_chunks=1, failed_chunks=1)
                        add_job_error(
                            conversation.source_path,
                            claim_error,
                            error_type="import_claim_busy",
                            retryable=True,
                            attempt=claim_attempt + 1,
                            import_key=import_key,
                        )
                        if hooks.update_chunk:
                            hooks.update_chunk(
                                claim_execution,
                                "busy",
                                {
                                    "error": claim_error,
                                    "error_type": "import_claim_busy",
                                    "retryable": True,
                                },
                            )
                        return False

                    increment_job(retried_chunks=1, retry_count=1)
                    if hooks.update_chunk:
                        hooks.update_chunk(
                            claim_execution,
                            "retrying",
                            {
                                "error": claim_error,
                                "error_type": "import_claim_busy",
                                "retryable": True,
                            },
                        )
                    if not _interruptible_sleep(
                        job_id,
                        controller.backoff_seconds(claim_attempt),
                        interrupt_cancelled,
                    ):
                        return False
            elif options.skip_duplicates and is_duplicate is not None and hooks.load_existing_keys is None:
                try:
                    if is_duplicate(import_key):
                        increment_job(processed_chunks=1, skipped_chunks=1)
                        return True
                except Exception as exc:
                    add_job_exception(
                        conversation.source_path,
                        exc,
                        operation_phase="deduplication",
                        error_type="deduplication_error",
                        retryable=True,
                        import_key=import_key,
                    )

            chunk_started = time.perf_counter()
            last_error: Exception | None = None
            last_error_code: str | None = None
            last_error_details: dict[str, Any] = {}
            last_retryable = True
            last_execution = base_execution
            local_timings: dict[str, float] = {}
            unflushed_timings: dict[str, float] = {}

            def record_phase(phase: str, seconds: float) -> None:
                if seconds < 0:
                    return
                local_timings[phase] = local_timings.get(phase, 0.0) + float(seconds)
                unflushed_timings[phase] = unflushed_timings.get(phase, 0.0) + float(seconds)

            def flush_phase_timings() -> None:
                if unflushed_timings:
                    record_job_phases(unflushed_timings)
                    unflushed_timings.clear()

            for attempt in range(options.max_attempts):
                if cancelled():
                    if chunk_claimed and hooks.update_chunk and not externally_cancelled():
                        hooks.update_chunk(base_execution, "cancelled", {})
                    return False
                execution = ChunkExecution(
                    **{
                        **base_execution.__dict__,
                        "attempt": attempt + 1,
                        "phase_callback": record_phase,
                    }
                )
                last_execution = execution
                if hooks.update_chunk:
                    hooks.update_chunk(execution, "processing", {"attempt": attempt + 1})
                try:
                    controller.acquire(cancelled)
                    update_concurrency()
                    try:
                        response = (
                            store_payload_with_context(payload, execution)
                            if store_payload_with_context is not None
                            else store_payload(payload)
                        )
                    finally:
                        controller.release()
                    if externally_cancelled():
                        return False
                    flush_phase_timings()
                    if isinstance(response, Mapping) and response.get("pressure_fallback") is True:
                        controller.record_pressure()
                    else:
                        controller.record_success()
                    update_concurrency()
                    duration = time.perf_counter() - chunk_started
                    import_jobs.record_chunk_duration(job_id, duration)
                    increment_job(
                        processed_chunks=1,
                        imported_chunks=1,
                        memories_created=_response_memory_count(response),
                    )
                    if hooks.update_chunk:
                        result_details = {
                            key: response.get(key)
                            for key in (
                                "memory_ids",
                                "model_used",
                                "fallback_reason",
                                "audit_result",
                                "audit_metadata",
                                "claimed_memory_hashes",
                            )
                            if isinstance(response, Mapping) and response.get(key) is not None
                        }
                        hooks.update_chunk(
                            execution,
                            "succeeded",
                            {
                                "duration_seconds": duration,
                                "timings": local_timings,
                                "memories_created": _response_memory_count(response),
                                **result_details,
                            },
                        )
                    return True
                except ImportLeaseLost:
                    fence_lost.set()
                    raise
                except ImportCancelled:
                    flush_phase_timings()
                    if chunk_claimed and hooks.update_chunk and not externally_cancelled():
                        hooks.update_chunk(execution, "cancelled", {})
                    return False
                except Exception as exc:
                    flush_phase_timings()
                    if externally_cancelled():
                        return False
                    last_error = exc
                    last_error_code, last_error_details = _import_error_diagnostics(exc)
                    safe_error_message = _safe_import_error_message(exc)
                    pressure_error = is_pressure_error(exc)
                    structural_error = is_structural_chunk_error(exc)
                    capacity_error = is_import_capacity_error(exc)
                    permanent_error = is_permanent_import_error(exc) and not (pressure_error or structural_error)
                    retryable = capacity_error or not permanent_error
                    retry_immediately = retryable and not capacity_error
                    last_retryable = retryable
                    cooldown_token = controller.record_failure(exc) if retry_immediately else None
                    update_concurrency()
                    error_text = _error_text(exc)
                    structural_split = structural_error and (
                        "truncat" in error_text or "output limit" in error_text or attempt + 1 >= options.max_attempts
                    )
                    repeated_timeout_split = (
                        is_timeout_error(exc)
                        and chunk.token_count >= TIMEOUT_SPLIT_MIN_TOKENS
                        and attempt + 1 >= min(TIMEOUT_SPLIT_ATTEMPTS, options.max_attempts)
                    )
                    should_split = structural_split or repeated_timeout_split
                    if should_split and chunk.split_depth < options.max_split_depth:
                        children = split_message_chunk(chunk, import_key)
                        if len(children) == 2:
                            return process_split_children(execution, children, safe_error_message)

                    if retry_immediately and attempt + 1 < options.max_attempts:
                        if hooks.update_chunk:
                            hooks.update_chunk(
                                execution,
                                "retrying",
                                {
                                    "error": safe_error_message,
                                    "error_type": "provider_pressure" if pressure_error else "extraction_error",
                                    "error_code": last_error_code,
                                    "error_details": last_error_details,
                                    "retryable": retryable,
                                },
                            )
                        increment_job(retried_chunks=1, retry_count=1)
                        delay = max(controller.backoff_seconds(attempt), controller.cooldown_seconds())
                        if not _interruptible_sleep(job_id, delay, interrupt_cancelled):
                            if chunk_claimed and hooks.update_chunk and not externally_cancelled():
                                hooks.update_chunk(execution, "cancelled", {})
                            return False
                        controller.complete_backoff(cooldown_token)
                    if not retry_immediately:
                        break

            if cancelled():
                if chunk_claimed and hooks.update_chunk and not externally_cancelled():
                    hooks.update_chunk(base_execution, "cancelled", {})
                return False
            duration = time.perf_counter() - chunk_started
            import_jobs.record_chunk_duration(job_id, duration)
            increment_job(processed_chunks=1, failed_chunks=1)
            if last_error is not None:
                error_type = (
                    "storage_quota_exceeded"
                    if is_import_capacity_error(last_error)
                    else "provider_pressure"
                    if is_pressure_error(last_error)
                    else "permanent_import_error"
                    if not last_retryable
                    else "extraction_error"
                )
                add_job_error(
                    conversation.source_path,
                    _safe_import_error_message(last_error),
                    error_type=error_type,
                    error_code=last_error_code,
                    error_details=last_error_details,
                    retryable=last_retryable,
                    attempt=last_execution.attempt,
                    import_key=import_key,
                )
                if hooks.update_chunk:
                    hooks.update_chunk(
                        last_execution,
                        "failed",
                        {
                            "duration_seconds": duration,
                            "timings": local_timings,
                            "error": _safe_import_error_message(last_error),
                            "error_type": error_type,
                            "error_code": last_error_code,
                            "error_details": last_error_details,
                            "retryable": last_retryable,
                        },
                    )
            return False

        def worker() -> None:
            while not cancelled():
                try:
                    conversation_group = work_queue.get_nowait()
                except queue.Empty:
                    return
                try:
                    for conversation, chunks in conversation_group:
                        update_job(
                            current_file=conversation.source_path,
                            current_conversation=conversation.title,
                            phase="extracting",
                        )
                        for chunk_index, chunk in enumerate(chunks):
                            if cancelled():
                                return
                            process_chunk(
                                conversation,
                                chunk,
                                chunk_index,
                                len(chunks),
                                payloads.get((conversation.source_path, conversation.id, chunk_index)),
                            )
                except ImportLeaseLost:
                    fence_lost.set()
                    raise
                finally:
                    work_queue.task_done()

        with ThreadPoolExecutor(
            max_workers=options.workers, thread_name_prefix=f"memory-import-{job_id[:8]}"
        ) as executor:
            futures = [executor.submit(worker) for _ in range(options.workers)]
            for future in futures:
                future.result()

        if cancelled():
            mark_job_cancelled(
                phase="cancelled",
                current_file=None,
                current_conversation=None,
                active_workers=0,
            )
            return

        job = import_jobs.get(job_id)
        if job and job.imported_chunks and hooks.sync_graph is not None:
            update_job(status="syncing_graph", phase="graph_sync", graph_status="syncing")
            increment_job(graph_attempts=1)
            graph_started = time.perf_counter()
            try:
                graph_result = hooks.sync_graph(job_id)
                graph_status = graph_result if graph_result in {"completed", "disabled", "skipped"} else "completed"
                update_job(graph_status=graph_status, graph_error=None)
            except ImportLeaseLost:
                fence_lost.set()
                raise
            except Exception as exc:
                update_job(graph_status="failed", graph_error=_safe_import_error_message(exc))
                add_job_exception(
                    "graph_sync",
                    exc,
                    operation_phase="graph_sync",
                    error_type="graph_sync_error",
                    retryable=True,
                )
            finally:
                record_job_phase("neo4j", time.perf_counter() - graph_started)
        elif job:
            graph_status = "disabled" if hooks.sync_graph is None else "skipped"
            update_job(graph_status=graph_status, graph_error=None)

        if cancelled():
            mark_job_cancelled(
                phase="cancelled",
                current_file=None,
                current_conversation=None,
                active_workers=0,
            )
            return

        job = import_jobs.get(job_id)
        has_errors = had_retryable_parse_failures or bool(job and (job.failed_chunks or job.graph_status == "failed"))
        source_retry_required = had_retryable_parse_failures or bool(job and job.failed_chunks)
        status: JobStatus = "completed_with_errors" if has_errors else "completed"
        if hooks.finalize_workspace is not None:
            hooks.finalize_workspace(job_id, source_retry_required)
        update_job(
            status=status,
            phase="completed",
            source_retry_required=source_retry_required,
            current_file=None,
            current_conversation=None,
            active_workers=0,
        )
    except ImportLeaseLost:
        fence_lost.set()
        raise
    except Exception as exc:
        if not externally_cancelled():
            current_job = import_jobs.get(job_id)
            add_job_exception(
                "import",
                exc,
                operation_phase=current_job.phase if current_job is not None else "import",
            )
            update_job(
                status="failed",
                phase="failed",
                source_retry_required=True,
                current_file=None,
                current_conversation=None,
            )
    finally:
        if not retain_workspace:
            shutil.rmtree(input_root, ignore_errors=True)
