import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import chat_import  # noqa: E402


def _chatgpt_node(content, *, role, parent=None, created_at=0, children=None):
    return {
        "parent": parent,
        "children": list(children or []),
        "message": {
            "create_time": created_at,
            "author": {"role": role},
            "content": {"parts": [content]},
        },
    }


def test_parse_doubao_markdown_roles_and_source(tmp_path):
    transcript = tmp_path / "doubao-chat.md"
    transcript.write_text(
        """# Test chat

> Source: https://www.doubao.com/chat/bot/chat/123456

---

### **[用户]**

I prefer graph memory.

---

### **[AI]**

Understood.

---
""",
        encoding="utf-8",
    )

    conversations = chat_import.parse_file(transcript, "auto", "folder/chat.md")

    assert len(conversations) == 1
    conversation = conversations[0]
    assert conversation.id == "123456"
    assert conversation.title == "Test chat"
    assert conversation.source_path == "folder/chat.md"
    assert conversation.source_app == "doubao"
    assert [message.role for message in conversation.messages] == ["user", "assistant"]
    assert [message.content for message in conversation.messages] == ["I prefer graph memory.", "Understood."]


def test_parse_chatgpt_markdown_roles_and_timestamps(tmp_path):
    transcript = tmp_path / "preferences.md"
    transcript.write_text(
        """# Preferences

#### You:
<time datetime="2025-04-07T08:31:42.016Z">16:31</time>

I prefer tea. See https://example.com/reference.

#### ChatGPT:
<time datetime="2025-04-07T08:31:43.802Z">16:31</time>

url: https://example.com/not-source
Noted.
""",
        encoding="utf-8",
    )

    conversation = chat_import.parse_file(transcript, "auto")[0]

    assert conversation.source_app == "chatgpt"
    assert [message.role for message in conversation.messages] == ["user", "assistant"]
    assert conversation.messages[0].created_at == "2025-04-07T08:31:42.016Z"
    assert conversation.messages[1].created_at == "2025-04-07T08:31:43.802Z"
    assert conversation.messages[0].content == "I prefer tea. See https://example.com/reference."
    assert conversation.source_url is None


def test_chatgpt_export_uses_current_node_ancestry_without_abandoned_siblings(tmp_path):
    mapping = {
        "root": _chatgpt_node("question", role="user", created_at=1, children=["draft", "active"]),
        "draft": _chatgpt_node("abandoned answer", role="assistant", parent="root", created_at=99),
        "active": _chatgpt_node("chosen answer", role="assistant", parent="root", created_at=2, children=["followup"]),
        "followup": _chatgpt_node("chosen followup", role="user", parent="active", created_at=3),
    }

    conversations = chat_import.parse_chatgpt_export(
        [{"id": "branched", "mapping": mapping, "current_node": "followup"}],
        tmp_path / "conversations.json",
        "chatgpt",
        "conversations.json",
    )

    assert [message.content for message in conversations[0].messages] == [
        "question",
        "chosen answer",
        "chosen followup",
    ]


@pytest.mark.parametrize("current_node", [None, "missing-node"])
def test_chatgpt_export_missing_or_invalid_current_node_uses_latest_tip(tmp_path, current_node):
    mapping = {
        "root": _chatgpt_node("question", role="user", created_at=1, children=["old", "latest"]),
        "old": _chatgpt_node("older branch", role="assistant", parent="root", created_at=2),
        "latest": _chatgpt_node(
            "latest branch",
            role="assistant",
            parent="root",
            created_at=3,
            children=["tool-tip"],
        ),
        "tool-tip": {"parent": "latest", "children": [], "message": None},
    }

    conversations = chat_import.parse_chatgpt_export(
        [{"id": "fallback", "mapping": mapping, "current_node": current_node}],
        tmp_path / "conversations.json",
        "chatgpt",
        "conversations.json",
    )

    assert [message.content for message in conversations[0].messages] == ["question", "latest branch"]


def test_chatgpt_export_current_node_cycle_is_bounded_and_deterministic(tmp_path):
    mapping = {
        "a": _chatgpt_node("cycle tip", role="assistant", parent="b", created_at=2),
        "b": _chatgpt_node("cycle parent", role="user", parent="a", created_at=1),
        "unrelated": _chatgpt_node("not active", role="assistant", created_at=100),
    }

    conversations = chat_import.parse_chatgpt_export(
        [{"id": "cycle", "mapping": mapping, "current_node": "a"}],
        tmp_path / "conversations.json",
        "chatgpt",
        "conversations.json",
    )

    assert [message.content for message in conversations[0].messages] == ["cycle parent", "cycle tip"]


def test_chatgpt_plugin_marker_and_two_megabyte_data_uri_are_normalized(tmp_path):
    transcript = tmp_path / "chatgpt-with-plugin.md"
    encoded_image = "A" * (2 * 1024 * 1024)
    transcript.write_text(
        "# Plugin transcript\n\n"
        "#### You:\n"
        '<time datetime="2025-04-07T08:31:42Z">16:31</time>\n\n'
        "Inspect this image.\n\n"
        "#### Plugin (browser):\n"
        '<time datetime="2025-04-07T08:31:43Z">16:31</time>\n\n'
        f"![upload](data:image/png;base64,{encoded_image})\n\n"
        "#### ChatGPT:\n\n"
        "Done.\n",
        encoding="utf-8",
    )

    conversation = chat_import.parse_file(transcript, "auto")[0]
    chunks = chat_import.adaptive_chunk_messages(
        conversation.messages,
        chat_import.ImportOptions(entities={"user_id": "me"}),
    )
    payload = chat_import.build_payload(
        conversation,
        chunks[0],
        0,
        len(chunks),
        chat_import.ImportOptions(entities={"user_id": "me"}),
    )

    assert transcript.stat().st_size > 2 * 1024 * 1024
    assert conversation.source_app == "chatgpt"
    assert [message.role for message in conversation.messages] == ["user", "assistant", "assistant"]
    assert conversation.messages[1].created_at == "2025-04-07T08:31:43Z"
    assert conversation.messages[1].content == "![upload]([inline-data-omitted])"
    assert chunks[0].token_count < 100
    assert payload["metadata"]["source_message_indices"] == [0, 1, 2]
    assert "base64" not in str(payload)


def test_token_splitter_is_near_linear_and_preserves_text(monkeypatch):
    class ByteEncoder:
        def __init__(self):
            self.encoded_characters = 0

        def encode(self, value, disallowed_special=()):
            del disallowed_special
            self.encoded_characters += len(value)
            return list(value.encode("utf-8"))

        @staticmethod
        def decode_single_token_bytes(token_id):
            return bytes([token_id])

    encoder = ByteEncoder()
    monkeypatch.setattr(chat_import, "_TOKEN_ENCODER", encoder)
    monkeypatch.setattr(chat_import, "_TOKEN_ENCODER_READY", True)
    text = ("alpha " * 20_000) + "\n\n" + ("界" * 20_000)

    parts = chat_import._split_text_by_tokens(text, 257)
    encoded_characters = encoder.encoded_characters

    assert "".join(parts) == text
    assert encoded_characters <= len(text) * 3
    assert len(parts) > 100


def test_parse_gb18030_and_common_role_prefixes(tmp_path):
    transcript = tmp_path / "legacy.markdown"
    transcript.write_bytes("**用户：** 我喜欢中文。\n**Assistant:** Noted.\n".encode("gb18030"))

    conversation = chat_import.parse_file(transcript, "generic")[0]

    assert [message.role for message in conversation.messages] == ["user", "assistant"]
    assert conversation.messages[0].content == "我喜欢中文。"


def test_zip_discovery_supports_documents_and_blocks_traversal(tmp_path):
    archive = tmp_path / "history.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("nested/chat.md", "User: hello\nAssistant: hi\n")
        handle.writestr("nested/notes.mdx", "# Notes\n\nUser: remember this\n")
        handle.writestr("images/photo.png", b"not-an-image")
        handle.writestr("../escape.md", "User: unsafe")

    extraction_root = tmp_path / "extracted"
    result = chat_import.discover_input_files([archive], extraction_root, input_root=tmp_path)

    assert [item.display_path for item in result.files] == [
        "history.zip!/nested/chat.md",
        "history.zip!/nested/notes.mdx",
    ]
    assert result.skipped_files == 2
    assert not (tmp_path / "escape.md").exists()


def test_payload_uses_custom_entities_timestamp_and_stable_import_key(tmp_path):
    first = chat_import.Conversation(
        id="conversation-1",
        title="Preferences",
        messages=[
            chat_import.ChatMessage("user", "API_KEY=sk-abcdefghijklmnopqrstuvwxyz and tea", "2025-01-02T03:04:05Z")
        ],
        source_path="first-name.md",
        source_app="chatgpt",
    )
    renamed = chat_import.Conversation(
        id=first.id,
        title=first.title,
        messages=first.messages,
        source_path="renamed.md",
        source_app=first.source_app,
    )
    options = chat_import.ImportOptions(entities={"user_id": "daz", "app_id": "personal-archive"})

    payload = chat_import.build_payload(first, first.messages, 0, 1, options)
    renamed_payload = chat_import.build_payload(renamed, renamed.messages, 0, 1, options)
    reordered_scope_payload = chat_import.build_payload(
        first,
        first.messages,
        0,
        1,
        chat_import.ImportOptions(entities={"app_id": "personal-archive", "user_id": "daz"}),
    )
    other_user_payload = chat_import.build_payload(
        first,
        first.messages,
        0,
        1,
        chat_import.ImportOptions(entities={"user_id": "other", "app_id": "personal-archive"}),
    )

    assert payload["user_id"] == "daz"
    assert payload["app_id"] == "personal-archive"
    assert payload["timestamp"] == "2025-01-02T03:04:05+00:00"
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in payload["messages"][0]["content"]
    assert payload["metadata"]["source_messages"] == [
        {
            "role": "user",
            "content": "API_KEY=[redacted] and tea",
            "source_index": 0,
            "created_at": "2025-01-02T03:04:05+00:00",
        }
    ]
    assert payload["metadata"]["import_key"] == renamed_payload["metadata"]["import_key"]
    assert payload["metadata"]["import_key"] == reordered_scope_payload["metadata"]["import_key"]
    assert payload["metadata"]["import_key"] == "354f1d263eda5bdc5f00cd71b83b07814fa74c039d6936159068a943b0280772"
    assert payload["metadata"]["import_key_schema_version"] == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    assert payload["metadata"]["entity_scope_hash"] == reordered_scope_payload["metadata"]["entity_scope_hash"]
    assert payload["metadata"]["import_key"] != other_user_payload["metadata"]["import_key"]
    assert payload["metadata"]["entity_scope_hash"] != other_user_payload["metadata"]["entity_scope_hash"]


def test_absent_persisted_import_key_version_defaults_to_current_pending_recovery():
    new_options = chat_import.ImportOptions(entities={"user_id": "daz", "app_id": "personal-archive"})
    legacy_snapshot = dict(new_options.__dict__)
    legacy_snapshot.pop("import_key_schema_version")
    restored = chat_import.ImportOptions.from_persisted_snapshot(legacy_snapshot)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Preferences",
        messages=[
            chat_import.ChatMessage(
                "user",
                "API_KEY=sk-abcdefghijklmnopqrstuvwxyz and tea",
                "2025-01-02T03:04:05Z",
            )
        ],
        source_path="first-name.md",
        source_app="chatgpt",
    )

    payload = chat_import.build_payload(conversation, conversation.messages, 0, 1, restored)

    assert restored.import_key_schema_version == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    assert restored._import_key_schema_version_missing is True
    assert payload["metadata"]["import_key"] == "354f1d263eda5bdc5f00cd71b83b07814fa74c039d6936159068a943b0280772"
    assert payload["metadata"]["import_key_schema_version"] == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION


@pytest.mark.parametrize("status", ["completed", "cancelled"])
def test_terminal_metrics_do_not_drift_with_wall_clock_refresh(monkeypatch, status):
    job = chat_import.ImportJob(
        id=f"terminal-{status}",
        project_id="project",
        status=status,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:03:00Z",
        input_files=["chat.md"],
        entities={"user_id": "me"},
        source_app="chatgpt",
        infer=True,
        total_input_files=1,
        started_at="2025-01-01T00:00:00Z",
        completed_at="2025-01-01T00:02:00Z",
        total_chunks=5,
        processed_chunks=4,
        failed_chunks=1,
        chunk_durations=[1.0, 2.0, 7.0],
    )
    wall_clock = [1_800_000_000.0]
    monkeypatch.setattr(chat_import.time, "time", lambda: wall_clock[0])

    first = chat_import.ImportJobStore.serialize(job)["metrics"]
    wall_clock[0] += 86_400
    refreshed = chat_import.ImportJobStore.serialize(job)["metrics"]

    assert refreshed == first
    assert first["throughput_chunks_per_minute"] == 2.0
    assert first["average_chunk_seconds"] == 3.333
    assert first["p95_chunk_seconds"] == 7.0
    assert first["failure_rate"] == 0.25

    job.completed_at = None
    fallback_first = chat_import.ImportJobStore.serialize(job)["metrics"]
    wall_clock[0] += 86_400
    fallback_refreshed = chat_import.ImportJobStore.serialize(job)["metrics"]
    assert fallback_refreshed == fallback_first
    assert fallback_first["throughput_chunks_per_minute"] == 1.333


@pytest.mark.parametrize(
    ("status", "source_retry_required", "expected"),
    [
        ("cancelled", True, True),
        ("failed", True, True),
        ("completed_with_errors", True, True),
        ("completed", True, False),
        ("cancelled", False, False),
        ("importing", True, False),
    ],
)
def test_serialized_job_exposes_only_actionable_source_retry_state(
    status,
    source_retry_required,
    expected,
):
    job = chat_import.ImportJob(
        id="retry-state",
        project_id="project",
        status=status,
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:01:00Z",
        input_files=["chat.md"],
        entities={"user_id": "me"},
        source_app="chatgpt",
        infer=True,
        total_input_files=1,
        source_retry_required=source_retry_required,
    )

    payload = chat_import.ImportJobStore.serialize(job)

    assert payload["source_retry_available"] is expected
    assert "source_retry_required" not in payload
    assert "workspace" not in payload


def test_background_job_reports_progress_and_skips_duplicates(tmp_path):
    upload_root = tmp_path / "job"
    upload_root.mkdir()
    first = upload_root / "first.md"
    second = upload_root / "second.md"
    first.write_text("User: first\nAssistant: one\n", encoding="utf-8")
    second.write_text("User: second\nAssistant: two\n", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=1)
    job = chat_import.import_jobs.create("project-1", [first.name, second.name], options)
    stored = []

    chat_import.run_import_job(
        job.id,
        [first, second],
        upload_root,
        upload_root / "extracted",
        options,
        lambda payload: stored.append(payload) or {"results": [{"id": "memory-1"}]},
        lambda import_key: len(stored) == 1,
    )

    completed = chat_import.import_jobs.get(job.id, "project-1")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.total_chunks == 2
    assert completed.processed_chunks == 2
    assert completed.imported_chunks == 1
    assert completed.skipped_chunks == 1
    assert completed.memories_created == 1
    assert not upload_root.exists()


def test_background_job_retries_transient_store_failures(tmp_path, monkeypatch):
    upload_root = tmp_path / "retry-job"
    upload_root.mkdir()
    transcript = upload_root / "chat.md"
    transcript.write_text("User: remember tea\nAssistant: noted\n", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    job = chat_import.import_jobs.create("project-retry", [transcript.name], options)
    attempts = 0
    delays = []

    def store(_payload):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("invalid extraction JSON")
        return {"results": [{"id": "memory-1"}]}

    monkeypatch.setattr(chat_import.time, "sleep", delays.append)
    chat_import.run_import_job(
        job.id,
        [transcript],
        upload_root,
        upload_root / "extracted",
        options,
        store,
    )

    completed = chat_import.import_jobs.get(job.id, "project-retry")
    assert completed is not None
    assert attempts == 3
    assert delays == [1.0, 2.0]
    assert completed.status == "completed"
    assert completed.imported_chunks == 1
    assert completed.failed_chunks == 0


def test_background_job_records_failure_after_retry_exhaustion(tmp_path, monkeypatch):
    upload_root = tmp_path / "failed-job"
    upload_root.mkdir()
    transcript = upload_root / "chat.md"
    transcript.write_text("User: remember tea\nAssistant: noted\n", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"})
    job = chat_import.import_jobs.create("project-failed", [transcript.name], options)
    attempts = 0
    states = []

    def store(_payload):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("invalid extraction JSON")

    monkeypatch.setattr(chat_import.time, "sleep", lambda _delay: None)
    chat_import.run_import_job(
        job.id,
        [transcript],
        upload_root,
        upload_root / "extracted",
        options,
        store,
        hooks=chat_import.ImportRuntimeHooks(
            update_chunk=lambda execution, state, _details: states.append((state, execution.attempt)),
            sync_graph=lambda _job_id: pytest.fail("graph sync must be skipped when no chunks were imported"),
        ),
    )

    completed = chat_import.import_jobs.get(job.id, "project-failed")
    assert completed is not None
    assert attempts == 3
    assert completed.status == "completed_with_errors"
    assert completed.processed_chunks == 1
    assert completed.imported_chunks == 0
    assert completed.failed_chunks == 1
    assert completed.graph_status == "skipped"
    assert completed.errors[-1]["message"] == "invalid extraction JSON"
    assert completed.errors[-1]["retryable"] is True
    assert [item for item in states if item[0] == "retrying"] == [("retrying", 1), ("retrying", 2)]
    assert states[-1] == ("failed", options.max_attempts)


def test_explicit_permanent_error_skips_immediate_retries_and_reports_non_retryable(tmp_path, monkeypatch):
    upload_root = tmp_path / "permanent-job"
    upload_root.mkdir()
    transcript = upload_root / "chat.md"
    transcript.write_text("User: remember tea\nAssistant: noted\n", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    job = chat_import.import_jobs.create("project-permanent", [transcript.name], options)
    attempts = 0
    states = []

    def store(_payload):
        nonlocal attempts
        attempts += 1
        raise chat_import.PermanentImportError("unsupported import configuration")

    monkeypatch.setattr(chat_import.time, "sleep", lambda _delay: pytest.fail("permanent errors must not back off"))
    chat_import.run_import_job(
        job.id,
        [transcript],
        upload_root,
        upload_root / "extracted",
        options,
        store,
        hooks=chat_import.ImportRuntimeHooks(
            update_chunk=lambda _execution, state, details: states.append((state, details)),
        ),
    )

    completed = chat_import.import_jobs.get(job.id, "project-permanent")
    assert completed is not None
    assert attempts == 1
    assert completed.retry_count == 0
    assert completed.failed_chunks == 1
    assert completed.errors[-1]["type"] == "permanent_import_error"
    assert completed.errors[-1]["retryable"] is False
    assert [state for state, _details in states] == ["processing", "failed"]
    assert states[-1][1]["retryable"] is False


class _HttpImportFailure(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"provider rejected the request with HTTP {status_code}")


@pytest.mark.parametrize(
    "failure",
    [
        _HttpImportFailure(400),
        _HttpImportFailure(401),
        _HttpImportFailure(403),
        _HttpImportFailure(404),
        TypeError("invalid provider contract"),
    ],
)
def test_deterministic_import_failures_skip_immediate_retries(tmp_path, monkeypatch, failure):
    upload_root = tmp_path / f"deterministic-{type(failure).__name__}-{getattr(failure, 'status_code', 'type')}"
    upload_root.mkdir()
    transcript = upload_root / "chat.md"
    transcript.write_text("User: remember tea\nAssistant: noted\n", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    job = chat_import.import_jobs.create("project-deterministic", [transcript.name], options)
    attempts = 0

    def store(_payload):
        nonlocal attempts
        attempts += 1
        raise failure

    monkeypatch.setattr(chat_import.time, "sleep", lambda _delay: pytest.fail("permanent errors must not back off"))
    chat_import.run_import_job(
        job.id,
        [transcript],
        upload_root,
        upload_root / "extracted",
        options,
        store,
    )

    completed = chat_import.import_jobs.get(job.id, "project-deterministic")
    assert completed is not None
    assert attempts == 1
    assert completed.retry_count == 0
    assert completed.failed_chunks == 1
    assert completed.errors[-1]["type"] == "permanent_import_error"
    assert completed.errors[-1]["retryable"] is False


def test_permanent_http_status_is_found_through_wrapped_exception():
    provider_error = _HttpImportFailure(401)
    wrapper = RuntimeError("LLM extraction failed")
    wrapper.__cause__ = provider_error

    assert chat_import.is_permanent_import_error(wrapper)
    assert chat_import._status_code(wrapper) == 401
    assert not chat_import.is_permanent_import_error(_HttpImportFailure(429))


def test_import_error_diagnostics_preserve_safe_root_cause_fields():
    provider_error = _HttpImportFailure(504)
    provider_error.code = "deadline_exceeded"
    wrapper = RuntimeError("Import extraction model call failed.")
    wrapper.reason = "model_error"
    wrapper.import_subphase = "llm"
    wrapper.__cause__ = provider_error

    error_code, details = chat_import._import_error_diagnostics(wrapper)

    assert error_code == "model_error"
    assert details == {
        "root_exception_module": __name__,
        "root_exception_type": "_HttpImportFailure",
        "operation_phase": "llm",
        "failure_point": "model_call",
        "validation_reason": "model_error",
        "status_code": 504,
        "provider_error_code": "deadline_exceeded",
    }


def test_import_error_message_always_redacts_credentials():
    secret_message = (
        'request failed: {"api_key": "sk-abcdefghijklmnopqrstuvwxyz", '
        '"authorization": "Bearer abcdefghijklmnop"}; '
        "postgresql://worker:database-password@example.test/db"
    )

    safe_message = chat_import._safe_import_error_message(RuntimeError(secret_message))

    assert "sk-abcdefghijklmnopqrstuvwxyz" not in safe_message
    assert "abcdefghijklmnop" not in safe_message
    assert "database-password" not in safe_message
    assert safe_message.count("[redacted]") >= 3


def test_retry_hook_receives_structured_error_diagnostics(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="diagnostic-retry",
        title="diagnostic-retry",
        messages=[chat_import.ChatMessage("user", "Remember tea.")],
        source_path="diagnostic-retry.md",
    )
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_attempts=2,
    )
    calls = 0
    states = []

    def store(_payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            provider_error = _HttpImportFailure(503)
            provider_error.code = "temporarily_unavailable"
            wrapper = RuntimeError("Import extraction model call failed.")
            wrapper.reason = "model_error"
            wrapper.import_subphase = "llm"
            wrapper.__cause__ = provider_error
            raise wrapper
        return {"results": [{"id": "memory-1"}]}

    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        store,
        hooks=chat_import.ImportRuntimeHooks(
            update_chunk=lambda _execution, state, details: states.append((state, details)),
        ),
    )

    retry = next(details for state, details in states if state == "retrying")
    assert completed.status == "completed"
    assert retry["error_code"] == "model_error"
    assert retry["error_details"]["root_exception_type"] == "_HttpImportFailure"
    assert retry["error_details"]["status_code"] == 503
    assert retry["error_details"]["provider_error_code"] == "temporarily_unavailable"


def _large_conversation(name: str, turns: int = 24, words: int = 220):
    messages = []
    for index in range(turns):
        messages.extend(
            [
                chat_import.ChatMessage("user", f"{name} fact {index} " + "detail " * words),
                chat_import.ChatMessage("assistant", f"{name} answer {index} " + "context " * words),
            ]
        )
    return chat_import.Conversation(
        id=name,
        title=name,
        messages=messages,
        source_path=f"{name}.md",
        source_app="generic",
    )


def _run_with_conversations(tmp_path, monkeypatch, conversations, options, store, **kwargs):
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    input_root = tmp_path / "job"
    input_root.mkdir()
    files = []
    by_name = {}
    for conversation in conversations:
        path = input_root / f"{conversation.id}.md"
        path.write_text("User: placeholder", encoding="utf-8")
        files.append(path)
        by_name[path.name] = conversation
    monkeypatch.setattr(
        chat_import,
        "parse_file",
        lambda path, _source_app, _source_path: [by_name[path.name]],
    )
    job = job_store.create("project", [path.name for path in files], options)
    chat_import.run_import_job(
        job.id,
        files,
        input_root,
        input_root / "extracted",
        options,
        store,
        **kwargs,
    )
    return job_store.get(job.id)


def test_audit_sample_is_reproducible_and_within_expected_range_at_scale():
    import_keys = [chat_import.stable_hash({"chunk": index}) for index in range(10_000)]

    first = [chat_import._is_audit_sample(import_key, 0.07) for import_key in import_keys]
    second = [chat_import._is_audit_sample(import_key, 0.07) for import_key in import_keys]

    assert first == second
    assert 0.05 <= sum(first) / len(first) <= 0.10
    assert not any(chat_import._is_audit_sample(import_key, 0) for import_key in import_keys)
    assert all(chat_import._is_audit_sample(import_key, 1) for import_key in import_keys)


def test_audit_sample_follows_persisted_import_key_across_job_reruns(tmp_path, monkeypatch):
    conversation = _large_conversation("stable-audit", turns=12)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        audit_ratio=0.5,
    )

    def run(root, random_value):
        observed = []
        root.mkdir()
        monkeypatch.setattr(chat_import.random, "random", lambda: random_value)
        completed = _run_with_conversations(
            root,
            monkeypatch,
            [conversation],
            options,
            lambda _payload: pytest.fail("context-aware store must be used"),
            store_payload_with_context=lambda _payload, execution: (
                observed.append((execution.import_key, execution.audit)) or {"results": []}
            ),
        )
        assert completed.status == "completed"
        return observed

    first = run(tmp_path / "first", 0.0)
    second = run(tmp_path / "second", 0.999999)

    assert first == second
    assert first
    assert all(audit == chat_import._is_audit_sample(import_key, options.audit_ratio) for import_key, audit in first)


def test_token_chunker_targets_four_to_six_thousand_tokens_with_turn_overlap():
    conversation = _large_conversation("tokens", turns=40, words=180)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        chunk_target_tokens=4000,
        chunk_max_tokens=6000,
        chunk_overlap_turns=2,
    )

    chunks = chat_import.adaptive_chunk_messages(conversation.messages, options)

    assert len(chunks) >= 3
    assert all(chunk.token_count <= 6000 for chunk in chunks)
    assert all(chunk.token_count >= 4000 for chunk in chunks[:-1])
    assert all(chunk.overlap_turns <= 2 for chunk in chunks)
    assert chunks[1].source_indices[:4] == chunks[0].source_indices[-4:]
    assert chunks[1].core_source_indices[0] > chunks[0].core_source_indices[0]


def _complexity_chunk(text, token_count=5000):
    return chat_import.MessageChunk(
        messages=[chat_import.ChatMessage("assistant", text, source_index=0)],
        token_count=token_count,
        source_indices=[0],
        core_source_indices=[0],
    )


def test_ordinary_markdown_lists_and_headings_stay_on_fast_model():
    text = "\n".join([*(f"## Section {index}" for index in range(40)), *(f"- Detail {index}" for index in range(80))])

    assert chat_import._chunk_complexity_reason(_complexity_chunk(text)) is None


@pytest.mark.parametrize(
    ("text", "token_count", "expected"),
    [
        ("plain text", 5900, "long_chunk"),
        ("\n".join("| a | b | c |" for _ in range(30)), 5000, "complex_structure"),
        (
            "```python\n" + "\n".join(f"value_{index} = {index}" for index in range(120)) + "\n```",
            5000,
            "complex_structure",
        ),
        ("\n".join(f"left\tright {index}" for index in range(60)), 5000, "complex_structure"),
        (
            "\n".join("| a | b | c |" for _ in range(15))
            + "\n```\n"
            + "\n".join(f"line {index}" for index in range(60))
            + "\n```",
            5000,
            "complex_structure",
        ),
    ],
)
def test_genuinely_complex_chunks_use_fallback_model(text, token_count, expected):
    assert chat_import._chunk_complexity_reason(_complexity_chunk(text, token_count)) == expected


@pytest.mark.parametrize(
    "text",
    [
        "\n".join("| a | b | c | d |" for _ in range(29)),
        "```python\n" + "\n".join(f"value_{index} = {index}" for index in range(119)) + "\n```",
        "\n".join(f"left\tright {index}" for index in range(59)),
        "\n".join("| a | b | c |" for _ in range(14))
        + "\n```\n"
        + "\n".join(f"line {index}" for index in range(60))
        + "\n```",
    ],
)
def test_complexity_thresholds_do_not_force_borderline_chunks(text):
    assert chat_import._chunk_complexity_reason(_complexity_chunk(text)) is None


def test_split_preserves_parent_core_indices_and_can_split_one_turn():
    conversation = _large_conversation("overlap-split", turns=40, words=180)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        chunk_target_tokens=4000,
        chunk_max_tokens=6000,
        chunk_overlap_turns=2,
    )
    parent = chat_import.adaptive_chunk_messages(conversation.messages, options)[1]
    overlap_indices = set(parent.source_indices) - set(parent.core_source_indices)

    children = chat_import.split_message_chunk(parent, "parent-key")

    assert len(children) == 2
    assert all(set(child.core_source_indices) <= set(parent.core_source_indices) for child in children)
    assert all(not (set(child.core_source_indices) & overlap_indices) for child in children)
    assert all(not (set(child.source_indices) & overlap_indices) for child in children)
    assert set().union(*(set(child.core_source_indices) for child in children)) == set(parent.core_source_indices)
    assert not (set(children[0].core_source_indices) & set(children[1].core_source_indices))

    one_turn = chat_import.MessageChunk(
        messages=[
            chat_import.ChatMessage("user", "fact " * 300, source_index=0),
            chat_import.ChatMessage("assistant", "answer " * 100, source_index=1),
        ],
        token_count=414,
        source_indices=[0, 1],
        core_source_indices=[0, 1],
    )
    one_turn_children = chat_import.split_message_chunk(one_turn, "one-turn-parent")
    assert len(one_turn_children) == 2
    assert [child.core_source_indices for child in one_turn_children] == [[0], [1]]
    assert one_turn_children[1].source_indices == [0, 1]
    assert one_turn_children[1].overlap_turns == 1

    single_source = chat_import.MessageChunk(
        messages=[chat_import.ChatMessage("user", "fact " * 1000, source_index=0)],
        token_count=1000,
        source_indices=[0],
        core_source_indices=[0],
    )
    single_source_children = chat_import.split_message_chunk(single_source, "single-source-parent")
    assert len(single_source_children) == 2
    assert [child.core_source_indices for child in single_source_children] == [[0], [0]]
    assert all(len(child.source_indices) == len(set(child.source_indices)) == 1 for child in single_source_children)
    assert "".join(child.messages[0].content for child in single_source_children) == single_source.messages[0].content


def test_same_conversation_is_ordered_while_conversations_run_in_parallel(tmp_path, monkeypatch):
    conversations = [_large_conversation(f"conversation-{index}") for index in range(3)]
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        workers=3,
        chunk_target_tokens=4000,
        retry_jitter=0,
    )
    lock = threading.Lock()
    active = 0
    max_active = 0
    active_by_conversation = {}
    seen = {conversation.id: [] for conversation in conversations}

    def store(payload):
        nonlocal active, max_active
        metadata = payload["metadata"]
        conversation_id = metadata["conversation_id"]
        with lock:
            assert active_by_conversation.get(conversation_id, 0) == 0
            active_by_conversation[conversation_id] = 1
            active += 1
            max_active = max(max_active, active)
            seen[conversation_id].append(metadata["chunk_index"])
        time.sleep(0.02)
        with lock:
            active -= 1
            active_by_conversation[conversation_id] = 0
        return {"results": [{"id": f"{conversation_id}-{metadata['chunk_index']}"}]}

    completed = _run_with_conversations(tmp_path, monkeypatch, conversations, options, store)

    assert completed.status == "completed"
    assert 2 <= max_active <= 3
    assert all(indices == list(range(len(indices))) for indices in seen.values())


def test_duplicate_conversation_ids_across_files_share_one_serial_worker(tmp_path, monkeypatch):
    first = chat_import.Conversation(
        id="shared-id",
        title="first",
        messages=[chat_import.ChatMessage("user", "first fact")],
        source_path="first.md",
    )
    second = chat_import.Conversation(
        id="shared-id",
        title="second",
        messages=[chat_import.ChatMessage("user", "second fact")],
        source_path="second.md",
    )
    input_root = tmp_path / "duplicate-conversation-id"
    input_root.mkdir()
    paths = [input_root / "first.md", input_root / "second.md"]
    for path in paths:
        path.write_text("placeholder", encoding="utf-8")
    by_name = {"first.md": first, "second.md": second}
    monkeypatch.setattr(chat_import, "parse_file", lambda path, *_args: [by_name[path.name]])
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=2)
    job = job_store.create("project", [path.name for path in paths], options)
    active = 0
    peak = 0
    order = []
    lock = threading.Lock()

    def store(payload):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            order.append(payload["metadata"]["conversation_title"])
        time.sleep(0.03)
        with lock:
            active -= 1
        return {"results": [{"id": payload["metadata"]["conversation_title"]}]}

    chat_import.run_import_job(
        job.id,
        paths,
        input_root,
        input_root / "extracted",
        options,
        store,
    )

    assert peak == 1
    assert order == ["first", "second"]
    assert job_store.get(job.id).status == "completed"


def test_repeated_429_reduces_concurrency_and_uses_exponential_backoff(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="limited",
        title="limited",
        messages=[chat_import.ChatMessage("user", "I prefer tea."), chat_import.ChatMessage("assistant", "Noted.")],
        source_path="limited.md",
    )
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        workers=3,
        retry_jitter=0,
    )
    attempts = 0
    delays = []

    def store(_payload):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("429 Too Many Requests")
        return {"results": [{"id": "memory"}]}

    monkeypatch.setattr(chat_import.time, "sleep", delays.append)
    completed = _run_with_conversations(tmp_path, monkeypatch, [conversation], options, store)

    assert completed.status == "completed"
    assert completed.current_concurrency == 1
    assert completed.retry_count == 2
    assert delays == [1.0, 2.0]


def test_adaptive_concurrency_acquire_honors_pressure_cooldown():
    controller = chat_import.AdaptiveConcurrency(
        1,
        retry_base_seconds=0.05,
        retry_max_seconds=0.05,
        retry_jitter=0,
    )
    controller.record_failure(TimeoutError("request timed out"))

    started = time.perf_counter()
    controller.acquire(lambda: False)
    elapsed = time.perf_counter() - started
    controller.release()

    assert elapsed >= 0.035


def test_pressure_fallback_success_still_reduces_concurrency(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="pressure-fallback",
        title="pressure-fallback",
        messages=[chat_import.ChatMessage("user", "Remember tea.")],
        source_path="pressure-fallback.md",
    )
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=3)

    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda _payload: {
            "results": [{"id": "memory"}],
            "pressure_fallback": True,
        },
    )

    assert completed.status == "completed"
    assert completed.current_concurrency == 2


def test_transient_provider_timeout_retries_without_expanding_chunk_tree(tmp_path, monkeypatch):
    conversation = _large_conversation("transient-provider-timeout", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_jitter=0,
        max_split_depth=2,
    )
    attempts = 0
    delays = []

    def store(_payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Fallback import extraction failed validation.") from TimeoutError("Request timed out.")
        return {"results": [{"id": "memory"}]}

    monkeypatch.setattr(chat_import.time, "sleep", delays.append)
    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        store,
    )

    assert attempts == 2
    assert completed.status == "completed"
    assert completed.total_chunks == 1
    assert completed.split_chunks == 0
    assert completed.failed_chunks == 0
    assert delays == [1.0]


def test_repeated_provider_timeout_splits_large_chunk_after_second_attempt(tmp_path, monkeypatch):
    conversation = _large_conversation("provider-timeout", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_jitter=0,
        max_split_depth=2,
    )
    attempts = 0
    delays = []

    def store(payload):
        nonlocal attempts
        attempts += 1
        if payload["metadata"]["token_count"] >= 2000:
            raise RuntimeError("Fallback import extraction failed validation.") from TimeoutError("Request timed out.")
        return {"results": [{"id": f"memory-{attempts}"}]}

    monkeypatch.setattr(chat_import.time, "sleep", delays.append)
    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        store,
    )

    assert attempts == 4
    assert completed.status == "completed"
    assert completed.total_chunks == 2
    assert completed.split_chunks == 1
    assert completed.imported_chunks == 2
    assert completed.failed_chunks == 0
    assert delays == [1.0]


def test_cancellation_does_not_submit_another_chunk(tmp_path, monkeypatch):
    conversations = [_large_conversation(f"cancel-{index}", turns=12) for index in range(2)]
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=1, retry_jitter=0)
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    input_root = tmp_path / "cancel-job"
    input_root.mkdir()
    files = []
    by_name = {}
    for conversation in conversations:
        path = input_root / f"{conversation.id}.md"
        path.write_text("User: placeholder", encoding="utf-8")
        files.append(path)
        by_name[path.name] = conversation
    monkeypatch.setattr(chat_import, "parse_file", lambda path, *_args: [by_name[path.name]])
    job = job_store.create("project", [path.name for path in files], options)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def store(_payload):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return {"results": [{"id": "memory"}]}

    runner = threading.Thread(
        target=chat_import.run_import_job,
        args=(job.id, files, input_root, input_root / "extracted", options, store),
    )
    runner.start()
    assert started.wait(2)
    job_store.request_cancel(job.id, "project")
    release.set()
    runner.join(5)

    assert not runner.is_alive()
    assert calls == 1
    assert job_store.get(job.id).status == "cancelled"


def test_external_lease_loss_stops_before_next_chunk_without_marking_job_cancelled(tmp_path, monkeypatch):
    conversations = [_large_conversation(f"lease-loss-{index}", turns=12) for index in range(2)]
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=1, retry_jitter=0)
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    input_root = tmp_path / "lease-loss-job"
    input_root.mkdir()
    files = []
    by_name = {}
    for conversation in conversations:
        path = input_root / f"{conversation.id}.md"
        path.write_text("User: placeholder", encoding="utf-8")
        files.append(path)
        by_name[path.name] = conversation
    monkeypatch.setattr(chat_import, "parse_file", lambda path, *_args: [by_name[path.name]])
    job = job_store.create("project", [path.name for path in files], options)
    started = threading.Event()
    release = threading.Event()
    lease_lost = threading.Event()
    calls = 0

    def store(_payload):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(2)
        return {"results": [{"id": "memory"}]}

    runner = threading.Thread(
        target=chat_import.run_import_job,
        args=(job.id, files, input_root, input_root / "extracted", options, store),
        kwargs={"external_cancelled": lease_lost.is_set},
    )
    runner.start()
    assert started.wait(2)
    lease_lost.set()
    release.set()
    runner.join(5)

    assert not runner.is_alive()
    assert calls == 1
    assert job_store.get(job.id).status == "importing"
    assert job_store.get(job.id).cancel_requested is False


def test_cancellation_after_chunk_claim_releases_owned_claim(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="cancel-claim",
        title="cancel-claim",
        messages=[chat_import.ChatMessage("user", "Remember tea."), chat_import.ChatMessage("assistant", "Noted.")],
        source_path="cancel-claim.md",
    )
    options = chat_import.ImportOptions(entities={"user_id": "me"}, workers=1)
    states = []
    stored = []

    def claim(execution, _payload):
        chat_import.import_jobs.request_cancel(execution.job_id, "project")
        return "claimed"

    hooks = chat_import.ImportRuntimeHooks(
        claim_chunk=claim,
        update_chunk=lambda _execution, state, _details: states.append(state),
    )
    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda payload: stored.append(payload),
        hooks=hooks,
    )

    assert completed.status == "cancelled"
    assert stored == []
    assert "cancelled" in states


def test_truncation_split_takes_precedence_over_pressure_and_permanent_markers(tmp_path, monkeypatch):
    conversation = _large_conversation("truncate", turns=2, words=800)
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0, max_split_depth=2)
    calls = []

    def store(payload):
        token_count = payload["metadata"]["token_count"]
        calls.append(token_count)
        if token_count > 2000:
            raise chat_import.PermanentImportError(
                "LLM response was truncated; configured output limit is 4096 tokens"
            ) from TimeoutError("Primary extraction request timed out.")
        return {"results": [{"id": f"memory-{len(calls)}"}]}

    completed = _run_with_conversations(tmp_path, monkeypatch, [conversation], options, store)

    assert completed.status == "completed"
    assert completed.split_chunks == 1
    assert completed.total_chunks == 2
    assert completed.imported_chunks == 2
    assert completed.failed_chunks == 0
    assert len(calls) == 3


def test_overlap_only_fallback_validation_splits_after_retry_exhaustion(tmp_path, monkeypatch):
    conversation = _large_conversation("overlap-evidence", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_attempts=3,
        max_split_depth=1,
    )
    calls = []

    def store(payload):
        token_count = payload["metadata"]["token_count"]
        calls.append(token_count)
        if token_count > 2000:
            validation_error = RuntimeError("Import extraction memory at index 0 cites only overlap evidence.")
            validation_error.reason = "missing_core_evidence"
            raise RuntimeError("Fallback import extraction failed validation.") from validation_error
        return {"results": [{"id": f"memory-{len(calls)}"}]}

    monkeypatch.setattr(chat_import.time, "sleep", lambda _delay: None)
    completed = _run_with_conversations(tmp_path, monkeypatch, [conversation], options, store)

    assert completed.status == "completed"
    assert calls[: options.max_attempts] == [calls[0]] * options.max_attempts
    assert len(calls) == options.max_attempts + 2
    assert completed.retry_count == options.max_attempts - 1
    assert completed.split_chunks == 1
    assert completed.total_chunks == 2
    assert completed.imported_chunks == 2
    assert completed.failed_chunks == 0


def test_overlap_only_split_does_not_exceed_max_depth(tmp_path, monkeypatch):
    conversation = _large_conversation("overlap-depth", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_attempts=1,
        max_split_depth=1,
    )
    calls = []

    def store(payload):
        calls.append(payload["metadata"]["token_count"])
        error = RuntimeError("Import extraction memory at index 0 cites only overlap evidence.")
        error.reason = "missing_core_evidence"
        raise error

    completed = _run_with_conversations(tmp_path, monkeypatch, [conversation], options, store)

    assert completed.status == "completed_with_errors"
    assert len(calls) == 3
    assert completed.split_chunks == 1
    assert completed.total_chunks == 2
    assert completed.processed_chunks == 2
    assert completed.failed_chunks == 2


def test_persisted_split_parents_expand_to_depth_first_leaves(tmp_path, monkeypatch):
    conversation = _large_conversation("persisted-split", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_jitter=0,
        max_split_depth=2,
    )
    root = chat_import.adaptive_chunk_messages(conversation.messages, options)[0]
    root_key = chat_import._message_chunk_import_key(conversation, root, options)
    root_children = chat_import.split_message_chunk(root, root_key)
    first_child_key = chat_import._message_chunk_import_key(conversation, root_children[0], options)
    statuses = {root_key: "split", first_child_key: "split"}
    expected = chat_import.expand_persisted_split_chunks(conversation, [root], options, statuses)
    expected_keys = [chat_import._message_chunk_import_key(conversation, chunk, options) for chunk in expected]
    loaded = []
    stored = []
    hooks = chat_import.ImportRuntimeHooks(
        load_chunk_statuses=lambda job_id: loaded.append(job_id) or statuses,
    )

    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda payload: (
            stored.append(payload["metadata"]["import_key"]) or {"results": [{"id": f"memory-{len(stored)}"}]}
        ),
        hooks=hooks,
    )

    assert len(loaded) == 1
    assert completed.status == "completed"
    assert completed.total_chunks == len(expected_keys) == 3
    assert stored == expected_keys
    assert root_key not in stored
    assert first_child_key not in stored


@pytest.mark.parametrize(
    "schema_version",
    [chat_import.LEGACY_IMPORT_KEY_SCHEMA_VERSION, chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION],
    ids=["legacy-v1", "scoped-v2"],
)
def test_unversioned_active_job_infers_and_reuses_succeeded_failed_and_split_keys(
    tmp_path,
    monkeypatch,
    schema_version,
):
    conversation = _large_conversation(f"resume-v{schema_version}", turns=2, words=800)
    current_options = chat_import.ImportOptions(
        entities={"user_id": "resume-user"},
        workers=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
    )
    unversioned_snapshot = dict(current_options.__dict__)
    unversioned_snapshot.pop("import_key_schema_version")
    persisted_options = chat_import.ImportOptions(
        **{**unversioned_snapshot, "import_key_schema_version": schema_version}
    )
    root = chat_import.adaptive_chunk_messages(conversation.messages, persisted_options)[0]
    root_key = chat_import._message_chunk_import_key(conversation, root, persisted_options)
    children = chat_import.split_message_chunk(root, root_key)
    assert len(children) == 2
    leaf_keys = [chat_import._message_chunk_import_key(conversation, child, persisted_options) for child in children]

    other_version = (
        chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
        if schema_version == chat_import.LEGACY_IMPORT_KEY_SCHEMA_VERSION
        else chat_import.LEGACY_IMPORT_KEY_SCHEMA_VERSION
    )
    other_options = chat_import.ImportOptions(**{**unversioned_snapshot, "import_key_schema_version": other_version})
    other_root_key = chat_import._message_chunk_import_key(conversation, root, other_options)
    other_leaf_keys = [chat_import._message_chunk_import_key(conversation, child, other_options) for child in children]
    assert not ({root_key, *leaf_keys} & {other_root_key, *other_leaf_keys})

    persisted_rows = {
        root_key: "split",
        leaf_keys[0]: "succeeded",
        leaf_keys[1]: "failed",
    }
    input_root = tmp_path / f"active-v{schema_version}-job"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    resumed_options = chat_import.ImportOptions.from_persisted_snapshot(unversioned_snapshot)
    job = job_store.create("resume-project", [transcript.name], resumed_options, status="importing")
    job.options_snapshot = unversioned_snapshot
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])
    loaded_keys = []
    claimed_keys = []
    stored_keys = []

    def load_existing(_project_id, keys):
        loaded_keys.append(set(keys))
        return {leaf_keys[0]}

    def claim_chunk(execution, _payload):
        claimed_keys.append(execution.import_key)
        return "claimed"

    chat_import.run_import_job(
        job.id,
        [transcript],
        input_root,
        input_root / "extracted",
        resumed_options,
        lambda payload: stored_keys.append(payload["metadata"]["import_key"]) or {"results": []},
        hooks=chat_import.ImportRuntimeHooks(
            load_chunk_statuses=lambda _job_id: persisted_rows,
            load_existing_keys=load_existing,
            claim_chunk=claim_chunk,
        ),
    )

    assert resumed_options.import_key_schema_version == schema_version
    assert not hasattr(resumed_options, "_import_key_schema_version_missing")
    assert job.options_snapshot["import_key_schema_version"] == schema_version
    assert loaded_keys == [set(leaf_keys)]
    assert claimed_keys == [leaf_keys[1]]
    assert stored_keys == [leaf_keys[1]]
    assert not (set().union(*loaded_keys, claimed_keys, stored_keys) & {other_root_key, *other_leaf_keys})


def test_unversioned_job_without_persisted_chunks_defaults_to_scoped_v2(tmp_path, monkeypatch):
    conversation = _large_conversation("no-persisted-rows", turns=1, words=20)
    current_options = chat_import.ImportOptions(entities={"user_id": "resume-user"}, workers=1)
    unversioned_snapshot = dict(current_options.__dict__)
    unversioned_snapshot.pop("import_key_schema_version")
    resumed_options = chat_import.ImportOptions.from_persisted_snapshot(unversioned_snapshot)
    root = chat_import.adaptive_chunk_messages(conversation.messages, resumed_options)[0]
    scoped_key = chat_import._message_chunk_import_key(conversation, root, resumed_options)
    legacy_options = chat_import.ImportOptions(
        **{
            **unversioned_snapshot,
            "import_key_schema_version": chat_import.LEGACY_IMPORT_KEY_SCHEMA_VERSION,
        }
    )
    legacy_key = chat_import._message_chunk_import_key(conversation, root, legacy_options)

    input_root = tmp_path / "no-persisted-rows-job"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    job = job_store.create("resume-project", [transcript.name], resumed_options, status="importing")
    job.options_snapshot = unversioned_snapshot
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])
    stored_keys = []

    chat_import.run_import_job(
        job.id,
        [transcript],
        input_root,
        input_root / "extracted",
        resumed_options,
        lambda payload: stored_keys.append(payload["metadata"]["import_key"]) or {"results": []},
        hooks=chat_import.ImportRuntimeHooks(load_chunk_statuses=lambda _job_id: {}),
    )

    assert resumed_options.import_key_schema_version == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    assert not hasattr(resumed_options, "_import_key_schema_version_missing")
    assert job.options_snapshot["import_key_schema_version"] == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    assert stored_keys == [scoped_key]
    assert legacy_key not in stored_keys


def test_unversioned_job_fails_closed_when_both_schema_root_keys_are_persisted(tmp_path, monkeypatch):
    conversation = _large_conversation("ambiguous-schema", turns=1, words=20)
    current_options = chat_import.ImportOptions(entities={"user_id": "resume-user"}, workers=1)
    unversioned_snapshot = dict(current_options.__dict__)
    unversioned_snapshot.pop("import_key_schema_version")
    resumed_options = chat_import.ImportOptions.from_persisted_snapshot(unversioned_snapshot)
    root = chat_import.adaptive_chunk_messages(conversation.messages, resumed_options)[0]
    root_keys = {
        chat_import._message_chunk_import_key(
            conversation,
            root,
            chat_import.ImportOptions(**{**unversioned_snapshot, "import_key_schema_version": schema_version}),
        )
        for schema_version in (
            chat_import.LEGACY_IMPORT_KEY_SCHEMA_VERSION,
            chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION,
        )
    }
    assert len(root_keys) == 2

    input_root = tmp_path / "ambiguous-schema-job"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    job = job_store.create("resume-project", [transcript.name], resumed_options, status="importing")
    job.options_snapshot = unversioned_snapshot
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])
    stored_keys = []

    chat_import.run_import_job(
        job.id,
        [transcript],
        input_root,
        input_root / "extracted",
        resumed_options,
        lambda payload: stored_keys.append(payload["metadata"]["import_key"]) or {"results": []},
        hooks=chat_import.ImportRuntimeHooks(
            load_chunk_statuses=lambda _job_id: {import_key: "failed" for import_key in root_keys}
        ),
    )

    failed = job_store.get(job.id)
    assert failed.status == "failed"
    assert stored_keys == []
    assert "import_key_schema_version" not in failed.options_snapshot
    assert any("match both v1 and v2 deterministic root keys" in error["message"] for error in failed.errors)


def test_split_attempts_later_child_when_first_child_fails(tmp_path, monkeypatch):
    conversation = _large_conversation("partial-split", turns=2, words=800)
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_split_depth=1,
    )
    calls = []

    def store(payload):
        indices = payload["metadata"]["core_source_message_indices"]
        calls.append(indices)
        if len(indices) > 2:
            raise RuntimeError("LLM response was truncated at the output limit")
        if min(indices) == 0:
            raise RuntimeError("first child failed")
        return {"results": [{"id": "later-memory"}]}

    monkeypatch.setattr(chat_import.time, "sleep", lambda _delay: None)
    completed = _run_with_conversations(tmp_path, monkeypatch, [conversation], options, store)

    assert completed.status == "completed_with_errors"
    assert completed.total_chunks == 2
    assert completed.processed_chunks == 2
    assert completed.failed_chunks == 1
    assert completed.imported_chunks == 1
    assert [2, 3] in calls


def test_duplicate_keys_are_loaded_once_for_the_whole_job(tmp_path, monkeypatch):
    conversation = _large_conversation("dedup", turns=24)
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    loaded = []
    stored = []

    def load_existing(project_id, keys):
        loaded.append((project_id, set(keys)))
        return {sorted(keys)[0]}

    hooks = chat_import.ImportRuntimeHooks(load_existing_keys=load_existing)
    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda payload: stored.append(payload) or {"results": [{"id": "memory"}]},
        is_duplicate=lambda _key: pytest.fail("per-chunk duplicate lookup must not run"),
        hooks=hooks,
    )

    assert len(loaded) == 1
    assert completed.skipped_chunks == 1
    assert len(stored) == completed.total_chunks - 1


def test_busy_chunk_claim_retries_with_bounded_backoff_then_imports(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="busy-then-ready",
        title="busy-then-ready",
        messages=[chat_import.ChatMessage("user", "Remember tea."), chat_import.ChatMessage("assistant", "Noted.")],
        source_path="busy-then-ready.md",
    )
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    claim_results = iter(["busy", "busy", "claimed"])
    states = []
    delays = []
    stored = []
    hooks = chat_import.ImportRuntimeHooks(
        claim_chunk=lambda _execution, _payload: next(claim_results),
        update_chunk=lambda _execution, state, _details: states.append(state),
    )
    monkeypatch.setattr(chat_import.time, "sleep", delays.append)

    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda payload: stored.append(payload) or {"results": [{"id": "memory"}]},
        hooks=hooks,
    )

    assert completed.status == "completed"
    assert completed.imported_chunks == 1
    assert completed.retry_count == 2
    assert states.count("retrying") == 2
    assert delays == [1.0, 2.0]
    assert len(stored) == 1


def test_busy_chunk_claim_exhaustion_is_retryable_failure(tmp_path, monkeypatch):
    conversation = chat_import.Conversation(
        id="always-busy",
        title="always-busy",
        messages=[chat_import.ChatMessage("user", "Remember tea."), chat_import.ChatMessage("assistant", "Noted.")],
        source_path="always-busy.md",
    )
    options = chat_import.ImportOptions(entities={"user_id": "me"}, retry_jitter=0)
    claim_attempts = 0
    states = []
    delays = []

    def claim(_execution, _payload):
        nonlocal claim_attempts
        claim_attempts += 1
        return "busy"

    hooks = chat_import.ImportRuntimeHooks(
        claim_chunk=claim,
        update_chunk=lambda _execution, state, _details: states.append(state),
    )
    monkeypatch.setattr(chat_import.time, "sleep", delays.append)
    completed = _run_with_conversations(
        tmp_path,
        monkeypatch,
        [conversation],
        options,
        lambda _payload: pytest.fail("a busy chunk must not be submitted"),
        hooks=hooks,
    )

    assert completed.status == "completed_with_errors"
    assert completed.failed_chunks == 1
    assert completed.skipped_chunks == 0
    assert completed.errors[-1]["retryable"] is True
    assert completed.errors[-1]["type"] == "import_claim_busy"
    assert claim_attempts == options.max_attempts
    assert states == ["retrying", "retrying", "busy"]
    assert delays == [1.0, 2.0]


@pytest.mark.parametrize(
    ("parse_mode", "expected_type"),
    [("empty", "no_conversations"), ("error", "parse_error")],
)
def test_parse_failures_are_retryable_completed_with_errors(
    tmp_path,
    monkeypatch,
    parse_mode,
    expected_type,
):
    input_root = tmp_path / f"parse-{parse_mode}"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    options = chat_import.ImportOptions(entities={"user_id": "me"})
    job_store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", job_store)
    job = job_store.create("project", [transcript.name], options)

    if parse_mode == "empty":
        monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [])
    else:

        def fail_parse(*_args):
            raise ValueError("temporary parser failure")

        monkeypatch.setattr(chat_import, "parse_file", fail_parse)

    chat_import.run_import_job(
        job.id,
        [transcript],
        input_root,
        input_root / "extracted",
        options,
        lambda _payload: pytest.fail("parse failures must not submit chunks"),
    )

    completed = job_store.get(job.id)
    assert completed.status == "completed_with_errors"
    assert completed.phase == "completed"
    assert completed.graph_status == "skipped"
    assert completed.errors[-1]["type"] == expected_type
    assert completed.errors[-1]["retryable"] is True
    if parse_mode == "error":
        assert completed.errors[-1]["code"] == "value_error"
        assert completed.errors[-1]["details"]["root_exception_type"] == "ValueError"
        assert completed.errors[-1]["details"]["operation_phase"] == "parsing"
