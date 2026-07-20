#!/usr/bin/env python3
"""Import exported AI chat histories into a YiQiao server.

Supported inputs:
- ChatGPT official conversations.json exports
- Claude-style JSON exports with chat_messages/messages
- Generic JSON or JSONL records with messages
- Markdown/text transcripts with role prefixes such as "User:" and "Assistant:"

The importer sends chunked conversations to POST /v3/memories/add/ with
infer=true by default, so YiQiao extracts durable memories instead of storing
one huge transcript blob.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import chat_import as shared_chat_import  # noqa: E402

SUPPORTED_SUFFIXES = {".json", ".jsonl", ".md", ".markdown", ".mdx", ".txt"}
DEFAULT_BASE_URL = "http://127.0.0.1:8888"

ROLE_MAP = {
    "assistant": "assistant",
    "ai": "assistant",
    "bot": "assistant",
    "chatgpt": "assistant",
    "claude": "assistant",
    "gemini": "assistant",
    "model": "assistant",
    "human": "user",
    "me": "user",
    "sender": "user",
    "user": "user",
    "developer": "system",
    "system": "system",
}

ROLE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
    r"(User|Human|Me|Assistant|AI|Claude|ChatGPT|Gemini|System|Developer)"
    r"(?:\*\*)?\s*[:\uff1a]\s*(.*)$",
    re.IGNORECASE,
)

SECRET_KEY_VALUE_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*([^\s,;]+)")
SECRET_TOKEN_PATTERNS = [
    re.compile(r"\byqsk_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
]


@dataclass
class ChatMessage:
    role: str
    content: str
    created_at: str | int | float | None = None


@dataclass
class Conversation:
    id: str
    title: str
    messages: list[ChatMessage]
    created_at: str | int | float | None = None
    updated_at: str | int | float | None = None
    source_path: str = ""
    source_app: str = "unknown"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def default_env_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    files = [repo_root / "server" / ".env", Path.cwd() / ".env"]
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            files.append(Path(local_app_data) / "hermes" / ".env")
        files.append(Path.home() / "AppData" / "Local" / "hermes" / ".env")
    else:
        files.append(Path.home() / ".hermes" / ".env")
    return files


def coerce_iso_datetime(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
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

    redacted = SECRET_KEY_VALUE_RE.sub(replace_key_value, text)
    for pattern in SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def detect_source_app(path: Path, explicit: str | None = None) -> str:
    if explicit and explicit != "auto":
        return explicit
    haystack = str(path).lower()
    for name in ("chatgpt", "openai", "claude", "gemini", "cursor", "codex", "trae", "hermes", "qclaw"):
        if name in haystack:
            return "chatgpt" if name == "openai" else name
    return "generic"


def content_from_chatgpt_message(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    if isinstance(content, dict):
        if "parts" in content:
            return stringify_content(content["parts"])
        if "text" in content:
            return stringify_content(content["text"])
    return stringify_content(content)


def parse_chatgpt_export(data: Any, path: Path, source_app: str) -> list[Conversation]:
    if not isinstance(data, list) or not any(isinstance(item, dict) and "mapping" in item for item in data):
        return []
    conversations: list[Conversation] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("mapping"), dict):
            continue
        messages: list[ChatMessage] = []
        nodes = shared_chat_import.chatgpt_active_branch_nodes(item["mapping"], item.get("current_node"))
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
                    source_path=str(path),
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


def conversation_from_generic(raw: Any, path: Path, source_app: str, fallback_id: str) -> Conversation | None:
    if not isinstance(raw, dict):
        return None
    raw_messages = raw.get("messages") or raw.get("chat_messages") or raw.get("turns") or raw.get("items")
    if not isinstance(raw_messages, list):
        return None
    messages = [msg for msg in (message_from_generic(item) for item in raw_messages) if msg]
    if not messages:
        return None
    conv_id = str(raw.get("id") or raw.get("uuid") or raw.get("conversation_id") or fallback_id)
    title = str(raw.get("title") or raw.get("name") or raw.get("summary") or path.stem)
    created_at = raw.get("created_at") or raw.get("create_time") or raw.get("timestamp") or messages[0].created_at
    updated_at = raw.get("updated_at") or raw.get("update_time")
    return Conversation(conv_id, title, messages, created_at, updated_at, str(path), source_app)


def parse_json_data(data: Any, path: Path, source_app: str) -> list[Conversation]:
    chatgpt = parse_chatgpt_export(data, path, source_app)
    if chatgpt:
        return chatgpt

    candidates: list[Any]
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
        conv = conversation_from_generic(item, path, source_app, f"{path.stem}-{index}")
        if conv:
            conversations.append(conv)
            continue
        msg = message_from_generic(item)
        if msg:
            loose_messages.append(msg)

    if loose_messages:
        conversations.append(
            Conversation(
                path.stem, path.stem, loose_messages, loose_messages[0].created_at, None, str(path), source_app
            )
        )
    return conversations


def parse_json_file(path: Path, source_app: str) -> list[Conversation]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    data = json.loads(text)
    return parse_json_data(data, path, source_app)


def parse_jsonl_file(path: Path, source_app: str) -> list[Conversation]:
    conversations: list[Conversation] = []
    loose_messages: list[ChatMessage] = []
    for index, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines()):
        if not line.strip():
            continue
        raw = json.loads(line)
        conv = conversation_from_generic(raw, path, source_app, f"{path.stem}-{index}")
        if conv:
            conversations.append(conv)
            continue
        msg = message_from_generic(raw)
        if msg:
            loose_messages.append(msg)
    if loose_messages:
        conversations.append(
            Conversation(
                path.stem, path.stem, loose_messages, loose_messages[0].created_at, None, str(path), source_app
            )
        )
    return conversations


def parse_text_file(path: Path, source_app: str) -> list[Conversation]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    messages: list[ChatMessage] = []
    current_role: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_role, current_lines
        content = "\n".join(current_lines).strip()
        if current_role and content:
            messages.append(ChatMessage(role=current_role, content=content))
        current_role = None
        current_lines = []

    for line in text.splitlines():
        match = ROLE_RE.match(line)
        if match:
            flush()
            current_role = normalize_role(match.group(1))
            rest = match.group(2).strip()
            current_lines = [rest] if rest else []
        else:
            if current_role:
                current_lines.append(line)
    flush()

    if not messages and text.strip():
        messages = [ChatMessage(role="user", content=text.strip())]
    return [Conversation(path.stem, path.stem, messages, None, None, str(path), source_app)] if messages else []


def parse_file(path: Path, source_app_arg: str | None) -> list[Conversation]:
    try:
        return shared_chat_import.parse_file(path, source_app_arg)
    except Exception as exc:
        print(f"[warn] failed to parse {path}: {exc}", file=sys.stderr)
    return []


def iter_input_files(inputs: Iterable[str], max_file_mb: float) -> Iterable[Path]:
    max_bytes = int(max_file_mb * 1024 * 1024)
    for raw in inputs:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            candidates = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES)
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.exists():
                print(f"[warn] missing input: {candidate}", file=sys.stderr)
                continue
            if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if candidate.stat().st_size > max_bytes:
                print(f"[warn] skipped large file over {max_file_mb:g} MB: {candidate}", file=sys.stderr)
                continue
            yield candidate


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


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sanitize_run_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-._:")
    suffix = stable_hash(value)[:10]
    if not cleaned:
        return f"chat-import-{suffix}"
    return f"{cleaned[:70]}-{suffix}"


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"imported": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("imported"), dict):
            return data
    except Exception:
        pass
    return {"imported": {}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_payload(
    conversation: Conversation,
    messages: list[ChatMessage],
    chunk_index: int,
    chunk_count: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    options = shared_chat_import.ImportOptions(
        entities={key: value for key, value in {"user_id": args.user_id, "agent_id": args.agent_id}.items() if value},
        source_app=getattr(args, "source_app", "auto"),
        infer=args.infer,
        redact_secrets=not args.no_redact,
        skip_duplicates=not getattr(args, "force", False),
        batch_id=args.batch_id,
    )
    payload = shared_chat_import.build_payload(conversation, messages, chunk_index, chunk_count, options)
    if args.use_run_id:
        payload["run_id"] = sanitize_run_id(f"import:{conversation.source_app}:{conversation.id or conversation.title}")
    return payload


def post_json(base_url: str, api_key: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v3/memories/add/"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Token {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body) if body else {"status": response.status}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc


def key_preview(api_key: str) -> str:
    if len(api_key) <= 10:
        return "[set]"
    return api_key[:10] + "..."


def parse_args(argv: list[str]) -> argparse.Namespace:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--env-file", action="append", default=[])
    pre_args, _ = pre.parse_known_args(argv)
    for env_file in [*default_env_files(), *(Path(item).expanduser() for item in pre_args.env_file)]:
        load_env_file(env_file)

    parser = argparse.ArgumentParser(description="Import AI chat history exports into YiQiao.")
    parser.add_argument("--input", action="append", required=True, help="Input file or directory. Can be repeated.")
    parser.add_argument("--env-file", action="append", default=[], help="Optional .env file to load before importing.")
    parser.add_argument(
        "--source-app", default="auto", help="Source app label, or auto. Example: chatgpt, claude, cursor."
    )
    parser.add_argument(
        "--base-url", default=os.getenv("YIQIAO_BASE_URL") or os.getenv("MEM0_BASE_URL") or DEFAULT_BASE_URL
    )
    parser.add_argument("--api-key", default=os.getenv("YIQIAO_API_KEY") or os.getenv("MEM0_API_KEY"))
    parser.add_argument("--user-id", default=os.getenv("YIQIAO_USER_ID") or os.getenv("MEM0_USER_ID") or "me")
    parser.add_argument(
        "--agent-id", default=os.getenv("YIQIAO_AGENT_ID") or os.getenv("MEM0_AGENT_ID") or "chat-history-import"
    )
    parser.add_argument("--batch-id", default=datetime.now(timezone.utc).strftime("chat-import-%Y%m%dT%H%M%SZ"))
    parser.add_argument("--state-file", default=str(Path.home() / ".yiqiao" / "chat_import_state.json"))
    parser.add_argument("--chunk-messages", type=int, default=20)
    parser.add_argument("--chunk-chars", type=int, default=12000)
    parser.add_argument("--max-file-mb", type=float, default=100)
    parser.add_argument("--sleep", type=float, default=0.2, help="Seconds to sleep between API calls.")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--limit-conversations", type=int, default=0)
    parser.add_argument("--limit-chunks", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force", action="store_true", help="Re-import chunks already present in the local state file."
    )
    parser.add_argument(
        "--no-redact", action="store_true", help="Do not redact obvious API keys/tokens/passwords before sending."
    )
    parser.add_argument(
        "--no-run-id", dest="use_run_id", action="store_false", help="Do not add a per-conversation run_id."
    )
    parser.set_defaults(use_run_id=True, infer=True)
    infer_group = parser.add_mutually_exclusive_group()
    infer_group.add_argument(
        "--infer", dest="infer", action="store_true", help="Extract durable memories from transcripts. Default."
    )
    infer_group.add_argument(
        "--no-infer", dest="infer", action="store_false", help="Store chunk text directly instead of extraction."
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if not args.api_key and not args.dry_run:
        print("error: missing API key. Set YIQIAO_API_KEY/MEM0_API_KEY or pass --api-key.", file=sys.stderr)
        return 2

    files = list(iter_input_files(args.input, args.max_file_mb))
    conversations: list[Conversation] = []
    for file_path in files:
        conversations.extend(parse_file(file_path, args.source_app))
    if args.limit_conversations:
        conversations = conversations[: args.limit_conversations]

    state_path = Path(args.state_file).expanduser()
    state = load_state(state_path)
    imported_state = state.setdefault("imported", {})

    print(f"YiQiao: {args.base_url} key={key_preview(args.api_key or '')} user={args.user_id} agent={args.agent_id}")
    print(f"Parsed files={len(files)} conversations={len(conversations)} batch={args.batch_id}")

    total_chunks = 0
    imported = 0
    skipped = 0
    failed = 0

    for conv_index, conversation in enumerate(conversations, start=1):
        chunks = chunk_messages(conversation.messages, args.chunk_messages, args.chunk_chars)
        for chunk_index, messages in enumerate(chunks):
            if args.limit_chunks and total_chunks >= args.limit_chunks:
                break
            total_chunks += 1
            payload = build_payload(conversation, messages, chunk_index, len(chunks), args)
            import_key = payload["metadata"]["import_key"]
            label = f"{conv_index}/{len(conversations)} {conversation.source_app}:{conversation.title[:60]} chunk {chunk_index + 1}/{len(chunks)}"
            if not args.force and import_key in imported_state:
                skipped += 1
                print(f"[skip] {label}")
                continue
            if args.dry_run:
                print(f"[dry] {label} messages={len(messages)} chars={sum(len(m.content) for m in messages)}")
                continue
            try:
                response = post_json(args.base_url, args.api_key, payload, args.timeout)
                imported += 1
                imported_state[import_key] = {
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                    "source_path": conversation.source_path,
                    "conversation_id": conversation.id,
                    "chunk_index": chunk_index,
                    "response_status": response.get("status"),
                }
                save_state(state_path, state)
                print(f"[ok] {label}")
                if args.sleep > 0:
                    time.sleep(args.sleep)
            except Exception as exc:
                failed += 1
                print(f"[fail] {label}: {exc}", file=sys.stderr)
        if args.limit_chunks and total_chunks >= args.limit_chunks:
            break

    if args.dry_run:
        print(f"Dry run complete: chunks={total_chunks} skipped_existing={skipped}")
    else:
        print(f"Import complete: imported={imported} skipped={skipped} failed={failed} chunks_seen={total_chunks}")
        print(f"State file: {state_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
