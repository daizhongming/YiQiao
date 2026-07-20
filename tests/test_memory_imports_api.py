import importlib
import io
import json
import os
import sys
import threading
import time
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture
def import_server(monkeypatch, tmp_path):
    import server_state

    memory = MagicMock()
    memory.add.return_value = {"results": [{"id": "memory-1", "event": "ADD", "memory": "tea"}]}
    memory.vector_store.list.return_value = []
    monkeypatch.setattr(server_state, "_load_overrides", lambda: {})
    monkeypatch.setattr(server_state, "_save_overrides", lambda _overrides: None)
    with patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "fake-key", "ADMIN_API_KEY": "", "JWT_SECRET": "test-secret", "AUTH_DISABLED": "true"},
    ):
        with patch("mem0.Memory.from_config", return_value=memory):
            import auth

            importlib.reload(auth)
            import server.main as server_main

            server_main = importlib.reload(server_main)
    import chat_import
    import models

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    repository = server_main.ImportRepository(test_sessions)
    job_store = chat_import.ImportJobStore()
    job_store.configure_repository(repository)
    chat_import.import_jobs = job_store
    server_main.import_jobs = job_store
    server_main.import_repository = repository
    server_main.MEMORY_IMPORT_STORAGE_ROOT = tmp_path / "memory-imports"
    server_state._memory_instance = memory
    monkeypatch.setattr(server_main, "_workspace_settings", lambda: server_main.DEFAULT_WORKSPACE_SETTINGS)
    monkeypatch.setattr(server_main, "_persist_request_log", MagicMock())
    monkeypatch.setattr(server_main, "_enforce_memory_storage_quota", MagicMock())
    monkeypatch.setattr(server_main, "upsert_graph_memory", MagicMock())
    monkeypatch.setattr(server_main, "_import_llm", lambda _model, **_kwargs: MagicMock())
    monkeypatch.setattr(server_main, "graph_is_configured", lambda: False)
    monkeypatch.setattr(server_main, "queue_webhook_event", MagicMock())
    return server_main, memory, TestClient(server_main.app)


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/memory-imports/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"cancelled", "completed", "completed_with_errors", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"Memory import {job_id} did not finish before timeout")


def _options(**overrides):
    return json.dumps(
        {
            "entities": [
                {"type": "user", "id": "daz"},
                {"type": "app", "id": "personal-archive"},
            ],
            "source_app": "auto",
            "infer": True,
            "redact_secrets": True,
            "skip_duplicates": True,
            **overrides,
        }
    )


def test_memory_import_error_api_returns_safe_structured_diagnostics(import_server):
    server_main, _memory, client = import_server
    import chat_import

    options = server_main.ImportOptions(entities={"user_id": "me"}, model_tiering_enabled=False)
    job = server_main.import_jobs.create("default-project", [], options, job_id="diagnostic-error")
    chunk = server_main.import_repository.upsert_chunk(job_id=job.id, import_key="chunk-key")
    provider_error = RuntimeError("provider unavailable; api_key=sk-abcdefghijklmnopqrstuvwxyz")
    provider_error.status_code = 503
    wrapper = RuntimeError("Import extraction model call failed.")
    wrapper.reason = "model_error"
    wrapper.import_subphase = "llm"
    wrapper.__cause__ = provider_error
    error_code, diagnostics = chat_import._import_error_diagnostics(wrapper)

    server_main.import_jobs.add_error(
        job.id,
        "chat.md",
        chat_import._safe_import_error_message(wrapper),
        error_type="provider_pressure",
        error_code=error_code,
        error_details=diagnostics,
        retryable=True,
        attempt=1,
        import_key="chunk-key",
    )

    errors_response = client.get(f"/memory-imports/{job.id}/errors")
    assert errors_response.status_code == 200
    error = errors_response.json()["results"][0]
    assert error["code"] == "model_error"
    assert error["details"]["root_exception_type"] == "RuntimeError"
    assert error["details"]["operation_phase"] == "llm"
    assert error["details"]["status_code"] == 503
    assert error["details"]["import_key"] == "chunk-key"
    assert server_main.import_repository.list_errors(job.id)[0].chunk_id == chunk.id

    job_response = client.get(f"/memory-imports/{job.id}")
    assert job_response.status_code == 200
    persisted_error = job_response.json()["errors"][0]
    assert persisted_error["code"] == "model_error"
    assert persisted_error["details"] == error["details"]
    serialized = json.dumps({"error": error, "job_error": persisted_error})
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized


def test_explicit_import_route_rejects_project_writer_model_overrides(import_server, monkeypatch):
    server_main, _, _ = import_server
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", True)
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_FAST_MODEL", "configured-fast")
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_FALLBACK_MODEL", "configured-pro")

    with pytest.raises(ValueError, match="server-configured fast and fallback models"):
        server_main.MemoryImportUploadOptions.model_validate(
            {
                "entities": [{"type": "user", "id": "daz"}],
                "model_tiering_enabled": True,
                "fast_model": "arbitrary-expensive-model",
                "fallback_model": "configured-pro",
            }
        )


def test_persisted_job_without_import_key_version_preserves_absence_for_runtime_inference(import_server):
    import chat_import

    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "legacy-user"})
    job = server_main.import_jobs.create(
        "default-project",
        ["legacy-chat.md"],
        options,
        job_id="legacy-key-version",
        status="importing",
    )
    assert job.options_snapshot["import_key_schema_version"] == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    legacy_snapshot = dict(job.options_snapshot)
    legacy_snapshot.pop("import_key_schema_version")
    server_main.import_repository.update_job(job.id, options=legacy_snapshot)
    server_main.import_jobs.forget(job.id)

    reloaded = server_main.import_jobs.get(job.id, refresh=True)
    restored = server_main._options_from_import_job(reloaded)

    assert "import_key_schema_version" not in reloaded.options_snapshot
    assert restored.import_key_schema_version == chat_import.CURRENT_IMPORT_KEY_SCHEMA_VERSION
    assert restored._import_key_schema_version_missing is True


def test_import_endpoint_accepts_folder_files_and_zip(import_server):
    server_main, memory, client = import_server
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "nested/chatgpt.md",
            '# Archived\n\n#### You:\n<time datetime="2025-01-02T03:04:05Z">11:04</time>\n\nI prefer tea.\n',
        )

    response = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[
            ("files", ("folder/doubao.md", "# Direct\n\n### **[用户]**\n\nI use Neo4j.\n", "text/markdown")),
            ("files", ("history.zip", archive.getvalue(), "application/zip")),
        ],
    )

    assert response.status_code == 202
    job_id = response.json()["id"]
    result = _wait_for_job(client, job_id)
    assert result["status"] == "completed"
    assert result["discovered_files"] == 2
    assert result["total_conversations"] == 2
    assert result["imported_chunks"] == 2
    assert result["memories_created"] == 2
    persisted = server_main.import_repository.get_job(job_id)
    assert persisted.workspace is None
    assert persisted.workspace_bytes == 0
    assert not (server_main.MEMORY_IMPORT_STORAGE_ROOT / job_id).exists()
    assert memory.add.call_count == 2
    for call in memory.add.call_args_list:
        assert call.kwargs["user_id"] == "daz"
        assert call.kwargs["app_id"] == "personal-archive"
    timestamped = [call for call in memory.add.call_args_list if call.kwargs["metadata"].get("created_at")]
    assert timestamped[0].kwargs["metadata"]["created_at"] == "2025-01-02T03:04:05+00:00"
    memory.vector_store.list.assert_called()

    recent = client.get("/memory-imports")
    assert recent.status_code == 200
    assert any(item["id"] == job_id for item in recent.json()["results"])
    assert server_main.import_jobs.get(job_id) is not None


def test_import_endpoint_persists_renamed_duplicate_upload_paths(import_server):
    _, memory, client = import_server

    response = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[
            ("files", ("chat.md", "User: I prefer tea.\nAssistant: Noted.\n", "text/markdown")),
            ("files", ("chat.md", "User: I prefer coffee.\nAssistant: Noted.\n", "text/markdown")),
        ],
    )

    assert response.status_code == 202
    assert response.json()["input_files"] == ["chat.md", "1-chat.md"]
    result = _wait_for_job(client, response.json()["id"])
    assert result["status"] == "completed"
    assert result["discovered_files"] == 2
    assert result["total_conversations"] == 2
    assert result["imported_chunks"] == 2
    assert memory.add.call_count == 2


def test_import_endpoint_validates_entities_and_supported_files(import_server):
    _, memory, client = import_server
    duplicate_entities = json.dumps(
        {
            "entities": [
                {"type": "user", "id": "first"},
                {"type": "user", "id": "second"},
            ]
        }
    )

    invalid = client.post(
        "/memory-imports",
        data={"options": duplicate_entities},
        files=[("files", ("chat.md", "User: hello", "text/markdown"))],
    )
    unsupported = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[("files", ("photo.png", b"png", "image/png"))],
    )
    infer_disabled = client.post(
        "/memory-imports",
        data={"options": _options(infer=False)},
        files=[("files", ("chat.md", "User: hello", "text/markdown"))],
    )

    assert invalid.status_code == 422
    assert unsupported.status_code == 400
    assert infer_disabled.status_code == 422
    assert "infer=true" in infer_disabled.text
    memory.add.assert_not_called()


def test_import_endpoint_enforces_active_job_limit_before_upload(import_server, monkeypatch):
    server_main, _, client = import_server
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT", 1)
    monkeypatch.setattr(server_main, "_submit_memory_import", MagicMock(return_value=True))

    first = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[("files", ("first.md", "User: remember tea", "text/markdown"))],
    )
    second = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[("files", ("second.md", "User: remember coffee", "text/markdown"))],
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"] == {
        "code": "memory_import_active_job_limit",
        "project_id": "default-project",
        "limit": 1,
        "active_jobs": 1,
    }
    assert server_main.import_repository.count_active_jobs("default-project") == 1
    assert len(server_main.import_repository.list_jobs("default-project")) == 1


def test_import_endpoint_releases_failed_workspace_budget_reservation(import_server, monkeypatch):
    server_main, _, client = import_server
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES", 4)

    response = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[("files", ("chat.md", b"12345", "text/markdown"))],
    )

    assert response.status_code == 507
    assert response.json()["detail"] == {
        "code": "memory_import_workspace_budget",
        "limit_bytes": 4,
        "used_bytes": 0,
        "requested_bytes": 5,
    }
    assert server_main.import_repository.list_jobs("default-project") == []
    storage_root = server_main.MEMORY_IMPORT_STORAGE_ROOT
    assert not storage_root.exists() or not list(storage_root.iterdir())


def test_import_endpoint_rejects_before_streaming_when_workspace_budget_is_full(import_server, monkeypatch):
    server_main, _, client = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()
    retained = server_main.import_jobs.create(
        "default-project",
        ["old.md"],
        options,
        job_id="retained-budget",
        workspace=str(server_main.MEMORY_IMPORT_STORAGE_ROOT / "retained-budget"),
    )
    server_main.import_jobs.update(
        retained.id,
        status="failed",
        source_retry_required=True,
        workspace_bytes=4,
    )
    monkeypatch.setattr(server_main, "MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES", 4)
    save_uploads = MagicMock()
    monkeypatch.setattr(server_main, "_save_import_uploads", save_uploads)

    response = client.post(
        "/memory-imports",
        data={"options": _options()},
        files=[("files", ("chat.md", b"1", "text/markdown"))],
    )

    assert response.status_code == 507
    assert response.json()["detail"]["requested_bytes"] == 0
    save_uploads.assert_not_called()
    assert [job.id for job in server_main.import_repository.list_jobs("default-project")] == [retained.id]


def test_terminal_workspace_cleanup_retains_source_retries_and_removes_graph_only(import_server):
    server_main, _, _ = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()

    retry_workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "source-retry"
    (retry_workspace / "uploads").mkdir(parents=True)
    (retry_workspace / "uploads" / "chat.md").write_text("User: tea", encoding="utf-8")
    retry_job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="source-retry",
        workspace=str(retry_workspace),
    )
    server_main.import_jobs.update(
        retry_job.id,
        status="completed_with_errors",
        graph_status="completed",
        failed_chunks=1,
        source_retry_required=True,
        workspace_bytes=9,
    )

    assert not server_main._cleanup_terminal_import_workspace(retry_job.id)
    assert retry_workspace.exists()
    assert server_main.import_repository.get_job(retry_job.id).workspace_bytes == 9

    graph_workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "graph-only"
    (graph_workspace / "uploads").mkdir(parents=True)
    (graph_workspace / "uploads" / "chat.md").write_text("User: coffee", encoding="utf-8")
    graph_job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="graph-only",
        workspace=str(graph_workspace),
    )
    server_main.import_jobs.update(
        graph_job.id,
        status="completed_with_errors",
        graph_status="failed",
        failed_chunks=0,
        source_retry_required=False,
        workspace_bytes=12,
    )

    assert server_main._cleanup_terminal_import_workspace(graph_job.id)
    persisted = server_main.import_repository.get_job(graph_job.id)
    assert persisted.workspace is None
    assert persisted.workspace_bytes == 0
    assert not graph_workspace.exists()


def test_workspace_cleanup_failure_remains_accounted(import_server, monkeypatch):
    server_main, _, _ = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "cleanup-failure"
    (workspace / "uploads").mkdir(parents=True)
    (workspace / "uploads" / "chat.md").write_text("User: tea", encoding="utf-8")
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="cleanup-failure",
        workspace=str(workspace),
    )
    server_main.import_jobs.update(
        job.id,
        status="completed",
        source_retry_required=False,
        workspace_bytes=9,
    )
    monkeypatch.setattr(server_main.shutil, "rmtree", MagicMock(side_effect=PermissionError("denied")))

    assert not server_main._cleanup_terminal_import_workspace(job.id)

    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.workspace == str(workspace)
    assert persisted.workspace_bytes == 9
    errors = server_main.import_repository.list_errors(job.id)
    assert errors[-1].error_type == "workspace_cleanup_error"


def test_terminal_import_workspace_can_be_discarded_without_deleting_audit_job(import_server):
    server_main, _, client = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "discard-retained"
    (workspace / "uploads").mkdir(parents=True)
    source = workspace / "uploads" / "chat.md"
    source.write_text("User: remember tea", encoding="utf-8")
    job = server_main.import_jobs.create(
        "default-project",
        [source.name],
        options,
        job_id=workspace.name,
        workspace=str(workspace),
    )
    server_main.import_jobs.update(
        job.id,
        status="completed_with_errors",
        phase="completed",
        failed_chunks=1,
        source_retry_required=True,
        workspace_bytes=source.stat().st_size,
    )

    response = client.post(f"/memory-imports/{job.id}/discard")
    repeated = client.post(f"/memory-imports/{job.id}/discard")

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert response.json()["id"] == job.id
    assert response.json()["status"] == "completed_with_errors"
    persisted = server_main.import_repository.get_job(job.id)
    assert persisted is not None
    assert persisted.status == "completed_with_errors"
    assert persisted.phase == "completed"
    assert persisted.workspace is None
    assert persisted.workspace_bytes == 0
    assert persisted.source_retry_required is False
    assert server_main.import_repository.list_chunks(job.id) == []
    assert not workspace.exists()


def test_workspace_discard_failure_restores_terminal_job_and_reservation(import_server, monkeypatch):
    server_main, _, client = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "discard-failure"
    (workspace / "uploads").mkdir(parents=True)
    source = workspace / "uploads" / "chat.md"
    source.write_text("User: remember tea", encoding="utf-8")
    job = server_main.import_jobs.create(
        "default-project",
        [source.name],
        options,
        job_id=workspace.name,
        workspace=str(workspace),
    )
    server_main.import_jobs.update(
        job.id,
        status="failed",
        phase="failed",
        source_retry_required=True,
        workspace_bytes=source.stat().st_size,
    )
    monkeypatch.setattr(server_main.shutil, "rmtree", MagicMock(side_effect=PermissionError("denied")))

    response = client.post(f"/memory-imports/{job.id}/discard")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "memory_import_workspace_discard_failed"
    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.status == "failed"
    assert persisted.phase == "failed"
    assert persisted.workspace == str(workspace)
    assert persisted.workspace_bytes == source.stat().st_size
    assert persisted.source_retry_required is True
    assert persisted.lease_owner is None
    assert persisted.lease_expires_at is None
    assert workspace.exists()
    assert server_main.import_repository.list_errors(job.id)[-1].error_type == "workspace_cleanup_error"


def test_stale_upload_reservation_becomes_terminal_and_is_cleaned(import_server):
    server_main, _, _ = import_server
    options = server_main.MemoryImportUploadOptions.model_validate(json.loads(_options())).to_import_options()
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "stale-upload"
    (workspace / "uploads").mkdir(parents=True)
    (workspace / "uploads" / "partial.md").write_text("partial", encoding="utf-8")
    job = server_main.import_jobs.create(
        "default-project",
        [],
        options,
        job_id="stale-upload",
        workspace=str(workspace),
        status="uploading",
        lease_owner="dead-uploader",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    server_main.import_jobs.update(job.id, workspace_bytes=7)

    server_main._expire_stale_import_upload(job.id)

    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.status == "failed"
    assert persisted.workspace is None
    assert persisted.workspace_bytes == 0
    assert persisted.source_retry_required is False
    assert not workspace.exists()


def test_retry_queued_during_runner_cleanup_is_handed_to_a_new_thread(import_server, monkeypatch):
    server_main, _, client = import_server
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "retry-handoff"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / "chat.md").write_text("User: remember tea", encoding="utf-8")
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="retry-handoff",
        workspace=str(workspace),
    )
    first_terminal = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    calls = 0

    def fake_run_import_job(job_id, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            server_main.import_jobs.update(job_id, status="completed_with_errors", phase="completed")
            first_terminal.set()
            assert release_first.wait(2)
            return
        server_main.import_jobs.update(job_id, status="completed", phase="completed")
        second_finished.set()

    monkeypatch.setattr(server_main, "run_import_job", fake_run_import_job)

    assert server_main._submit_memory_import(job.id)
    assert first_terminal.wait(2)
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)
    original_owner = contender.get_job(job.id).lease_owner
    assert original_owner
    retry = client.post(f"/memory-imports/{job.id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    replacement = contender.get_job(job.id)
    assert replacement.lease_owner
    assert replacement.lease_owner != original_owner
    assert not contender.renew_job_lease(job.id, original_owner, lease_seconds=30)
    release_first.set()

    assert second_finished.wait(2)
    assert calls == 2
    assert server_main.import_jobs.get(job.id).status == "completed"


def test_stale_runner_return_cannot_commit_after_lease_takeover(import_server, monkeypatch):
    server_main, _, _client = import_server
    import chat_import
    import models

    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "stale-return"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    transcript = upload_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[chat_import.ChatMessage("user", "Remember tea.", source_index=0)],
        source_path=transcript.name,
    )
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        max_attempts=1,
        model_tiering_enabled=False,
        retry_jitter=0,
    )
    job = server_main.import_jobs.create(
        "default-project",
        [transcript.name],
        options,
        job_id="stale-return",
        workspace=str(workspace),
    )
    store_entered = threading.Event()
    release_store = threading.Event()
    store_calls = 0

    def blocked_store(_memory_create, _project_id, *, operation_context, sync_graph):
        nonlocal store_calls
        store_calls += 1
        assert sync_graph is False
        assert operation_context.core_source_message_indices == [0]
        assert operation_context.execution_guard is not None
        assert operation_context.memory_hash_claim("stale-hash")
        operation_context.claimed_memory_hashes.append("stale-hash")
        store_entered.set()
        assert release_store.wait(5)
        operation_context.execution_guard()
        return {
            "results": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "event": "ADD",
                    "memory": "The user prefers tea.",
                }
            ]
        }

    monkeypatch.setattr(server_main, "_store_memory", blocked_store)

    assert server_main._submit_memory_import(job.id)
    assert store_entered.wait(5)
    runner = server_main._import_threads[job.id]
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)
    owner_a = contender.get_job(job.id).lease_owner
    assert owner_a
    owner_b = "replacement-owner"
    takeover_at = datetime.now(timezone.utc) + timedelta(seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS + 1)
    assert contender.acquire_job_lease(
        job.id,
        owner_b,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
        now=takeover_at,
    )

    def persisted_state():
        persisted_job = contender.get_job(job.id)
        chunks = contender.list_chunks(job.id)
        manifests = contender.load_manifests(job.project_id, [chunk.import_key for chunk in chunks])
        graph_items = contender.list_graph_items(job.id, status=None)
        with contender._session_factory() as session:
            hashes = session.scalars(
                select(models.MemoryImportHash).where(models.MemoryImportHash.job_id == job.id)
            ).all()
        return {
            "job": (
                persisted_job.status,
                persisted_job.phase,
                persisted_job.processed_chunks,
                persisted_job.imported_chunks,
                persisted_job.failed_chunks,
                persisted_job.memories_created,
                persisted_job.retry_count,
                persisted_job.error_count,
                persisted_job.graph_pending_items,
                persisted_job.lease_owner,
            ),
            "chunks": [
                (
                    chunk.id,
                    chunk.import_key,
                    chunk.status,
                    chunk.attempt,
                    chunk.retry_count,
                    list(chunk.memory_ids or []),
                    chunk.error_type,
                )
                for chunk in chunks
            ],
            "manifests": sorted(
                (
                    key,
                    manifest.job_id,
                    manifest.chunk_id,
                    manifest.status,
                    tuple(manifest.memory_ids or []),
                )
                for key, manifest in manifests.items()
            ),
            "hashes": sorted((row.memory_hash, row.status, row.memory_id, row.job_id, row.chunk_id) for row in hashes),
            "graph": sorted((item.item_key, item.status, item.memory_id) for item in graph_items),
            "errors": [(error.source, error.error_type, error.message) for error in contender.list_errors(job.id)],
        }

    before = persisted_state()
    assert before["job"][0:2] == ("importing", "extracting")
    assert before["job"][-1] == owner_b
    assert before["chunks"][0][2] == "processing"
    assert before["manifests"][0][3] == "claimed"
    assert before["hashes"][0][0:3] == ("stale-hash", "claimed", None)
    assert before["graph"] == []

    release_store.set()
    runner.join(timeout=5)
    assert not runner.is_alive()

    assert persisted_state() == before
    assert store_calls == 1
    assert not contender.renew_job_lease(
        job.id,
        owner_a,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
    )


def test_runner_cleans_up_when_terminal_workspace_finalization_loses_lease(import_server, monkeypatch):
    server_main, _, _ = import_server
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "finalization-lease-loss"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    transcript = upload_root / "chat.md"
    transcript.write_text("User: remember tea", encoding="utf-8")
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        [transcript.name],
        options,
        job_id="finalization-lease-loss",
        workspace=str(workspace),
    )
    lease_owner = "finalization-owner"
    assert server_main.import_repository.acquire_job_lease(
        job.id,
        lease_owner,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
    )

    def finish_import(job_id, *_args, **_kwargs):
        server_main.import_jobs.update(
            job_id,
            lease_owner=lease_owner,
            status="completed",
            phase="completed",
            source_retry_required=False,
        )

    resubmit = MagicMock(return_value=False)
    finalizer = MagicMock(side_effect=server_main.ImportLeaseLost(job.id, lease_owner))
    monkeypatch.setattr(server_main, "run_import_job", finish_import)
    monkeypatch.setattr(server_main, "_finalize_terminal_import_workspace", finalizer)
    monkeypatch.setattr(server_main, "_submit_memory_import", resubmit)
    with server_main._import_threads_lock:
        server_main._import_threads[job.id] = threading.current_thread()

    server_main._run_memory_import(job.id, lease_owner)

    finalizer.assert_called_once_with(job.id, lease_owner=lease_owner)
    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.lease_owner is None
    assert persisted.lease_expires_at is None
    with server_main._import_threads_lock:
        assert job.id not in server_main._import_threads
    resubmit.assert_called_once_with(job.id, reset_for_recovery=True)


def test_submitted_import_holds_database_lease_until_runner_cleanup(import_server, monkeypatch):
    server_main, _, _client = import_server
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "leased-runner"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / "chat.md").write_text("User: remember tea", encoding="utf-8")
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="leased-runner",
        workspace=str(workspace),
    )
    started = threading.Event()
    release_runner = threading.Event()

    def fake_run_import_job(job_id, *_args, **_kwargs):
        server_main.import_jobs.update(job_id, status="importing", phase="extracting")
        started.set()
        assert release_runner.wait(2)
        server_main.import_jobs.update(job_id, status="completed", phase="completed")

    monkeypatch.setattr(server_main, "run_import_job", fake_run_import_job)
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)

    assert server_main._submit_memory_import(job.id)
    assert started.wait(2)
    with server_main._import_threads_lock:
        runner = server_main._import_threads.get(job.id)
    assert runner is not None
    try:
        leased = contender.get_job(job.id)
        assert leased.lease_owner
        assert leased.lease_expires_at is not None
        assert not contender.acquire_job_lease(job.id, "other-process", lease_seconds=30)
    finally:
        release_runner.set()

    runner.join(timeout=2)
    assert not runner.is_alive()
    released = contender.get_job(job.id)
    assert released is not None
    assert released.lease_owner is None
    assert released.lease_expires_at is None


def test_import_progress_get_refreshes_stale_process_cache(import_server):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="fresh-progress")
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)

    assert server_main.import_jobs.get(job.id).status == "queued"
    contender.update_job(
        job.id,
        status="importing",
        phase="extracting",
        total_chunks=4,
        processed_chunks=2,
    )

    response = client.get(f"/memory-imports/{job.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "importing"
    assert response.json()["phase"] == "extracting"
    assert response.json()["processed_chunks"] == 2
    assert server_main.import_jobs.get(job.id).status == "importing"


def test_recovery_scanner_resumes_job_after_stale_lease_expires(import_server, monkeypatch):
    server_main, _, _ = import_server
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "stale-lease-recovery"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / "chat.md").write_text("User: remember tea", encoding="utf-8")
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="stale-lease-recovery",
        workspace=str(workspace),
    )
    server_main.import_jobs.update(job.id, status="importing", phase="extracting")
    assert server_main.import_repository.acquire_job_lease(
        job.id,
        "exited-process",
        lease_seconds=0.25,
        now=datetime.now(timezone.utc),
    )
    resumed = threading.Event()

    def fake_run(job_id, lease_owner):
        server_main.import_jobs.get(job_id, refresh=True)
        server_main.import_jobs.update(job_id, status="completed", phase="completed")
        server_main.import_repository.release_job_lease(job_id, lease_owner)
        with server_main._import_threads_lock:
            if server_main._import_threads.get(job_id) is threading.current_thread():
                server_main._import_threads.pop(job_id, None)
        resumed.set()

    monkeypatch.setattr(server_main, "_run_memory_import", fake_run)
    stop = threading.Event()
    scanner = threading.Thread(
        target=server_main._memory_import_recovery_loop,
        args=(stop, 0.03),
        daemon=True,
    )
    scanner.start()
    try:
        assert not resumed.wait(0.08)
        assert resumed.wait(2)
    finally:
        stop.set()
        scanner.join(2)

    assert not scanner.is_alive()
    assert server_main.import_jobs.get(job.id, refresh=True).status == "completed"


def test_recovery_resumes_expired_graph_sync_without_source_workspace(import_server, monkeypatch):
    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    job = server_main.import_jobs.create(
        "default-project",
        [],
        options,
        job_id="expired-graph-recovery",
        status="syncing_graph",
        lease_owner="exited-graph-worker",
        lease_expires_at=expired_at,
    )
    server_main.import_jobs.update(
        job.id,
        graph_status="syncing",
        source_retry_required=False,
        workspace=None,
    )
    started = MagicMock(return_value=True)
    import_started = MagicMock(return_value=True)
    monkeypatch.setattr(server_main, "_start_graph_retry_thread", started)
    monkeypatch.setattr(server_main, "_start_memory_import_thread", import_started)

    server_main._resume_memory_imports()

    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.status == "syncing_graph"
    assert persisted.lease_owner not in {None, "exited-graph-worker"}
    started.assert_called_once_with(job.id, persisted.lease_owner)
    import_started.assert_not_called()
    server_main.import_repository.release_job_lease(job.id, persisted.lease_owner)


def test_recovery_submission_resets_parsing_derived_progress(import_server, monkeypatch):
    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="reset-recovery-progress",
    )
    server_main.import_jobs.update(
        job.id,
        status="importing",
        phase="extracting",
        discovered_files=1,
        parsed_files=1,
        skipped_files=2,
        total_conversations=3,
        total_chunks=4,
        total_tokens=5000,
        processed_chunks=2,
        imported_chunks=2,
        current_file="chat.md",
        current_conversation="Conversation",
    )
    started = MagicMock(return_value=True)
    monkeypatch.setattr(server_main, "_start_memory_import_thread", started)

    assert server_main._submit_memory_import(job.id, reset_for_recovery=True)

    restored = server_main.import_jobs.get(job.id, refresh=True)
    assert restored.status == "queued"
    assert restored.phase == "queued"
    assert restored.active_workers == 0
    assert restored.discovered_files == 0
    assert restored.parsed_files == 0
    assert restored.skipped_files == 0
    assert restored.total_conversations == 0
    assert restored.total_chunks == 0
    assert restored.total_tokens == 0
    assert restored.current_file is None
    assert restored.current_conversation is None
    assert restored.processed_chunks == 2
    assert restored.imported_chunks == 2
    persisted = server_main.import_repository.get_job(job.id)
    assert persisted.lease_owner
    started.assert_called_once_with(job.id, persisted.lease_owner)
    server_main.import_repository.release_job_lease(job.id, persisted.lease_owner)


def test_import_hooks_load_persisted_chunk_statuses(import_server):
    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="hook-chunk-statuses")
    for index, status in enumerate(("split", "succeeded")):
        server_main.import_repository.upsert_chunk(
            job_id=job.id,
            project_id=job.project_id,
            import_key=f"import-key-{index}",
            conversation_id="conversation-1",
            chunk_index=index,
            status=status,
        )

    hooks = server_main._import_hooks(job.id)

    assert hooks.load_chunk_statuses is not None
    assert hooks.load_chunk_statuses(job.id) == {
        "import-key-0": "split",
        "import-key-1": "succeeded",
    }


def test_same_project_rerun_expands_nested_split_history_and_skips_all_leaves(
    import_server,
    monkeypatch,
    tmp_path,
):
    server_main, _, _ = import_server
    import chat_import

    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_split_depth=2,
    )
    messages = []
    for index in range(2):
        messages.extend(
            [
                chat_import.ChatMessage("user", f"fact {index} " + "detail " * 800),
                chat_import.ChatMessage("assistant", f"answer {index} " + "context " * 800),
            ]
        )
    conversation = chat_import.Conversation(
        id="persisted-project-split",
        title="Persisted project split",
        messages=messages,
        source_path="chat.md",
        source_app="generic",
    )
    root = chat_import.adaptive_chunk_messages(conversation.messages, options)[0]
    root_key = chat_import._message_chunk_import_key(conversation, root, options)
    root_children = chat_import.split_message_chunk(root, root_key)
    assert len(root_children) == 2
    nested_key = chat_import._message_chunk_import_key(conversation, root_children[0], options)
    nested_children = chat_import.split_message_chunk(root_children[0], nested_key)
    assert len(nested_children) == 2
    leaves = [*nested_children, root_children[1]]

    prior = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="prior-nested-split",
        status="completed",
    )

    def persist_chunk(chunk, import_key, status, index):
        row = server_main.import_repository.upsert_chunk(
            job_id=prior.id,
            project_id=prior.project_id,
            import_key=import_key,
            conversation_id=conversation.id,
            chunk_index=index,
            parent_import_key=chunk.parent_import_key,
            split_depth=chunk.split_depth,
            status=status,
        )
        claimed, _ = server_main.import_repository.claim_manifest(
            prior.project_id,
            import_key,
            prior.id,
            row.id,
        )
        assert claimed is True
        server_main.import_repository.mark_manifest(
            prior.project_id,
            import_key,
            "split" if status == "split" else "succeeded",
            memory_ids=[],
        )
        return row

    persist_chunk(root, root_key, "split", 0)
    persist_chunk(root_children[0], nested_key, "split", 1)
    leaf_keys = []
    for index, leaf in enumerate(leaves, start=2):
        leaf_key = chat_import._message_chunk_import_key(conversation, leaf, options)
        leaf_keys.append(leaf_key)
        persist_chunk(leaf, leaf_key, "succeeded", index)

    input_root = tmp_path / "same-project-split-rerun"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("User: placeholder", encoding="utf-8")
    rerun = server_main.import_jobs.create(
        prior.project_id,
        [transcript.name],
        options,
        job_id="same-project-split-rerun",
    )
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])

    chat_import.run_import_job(
        rerun.id,
        [transcript],
        input_root,
        input_root / "extracted",
        options,
        lambda _payload: pytest.fail("persisted split leaves must be skipped"),
        hooks=server_main._import_hooks(rerun.id),
        retain_workspace=True,
    )

    completed = server_main.import_jobs.get(rerun.id, refresh=True)
    rerun_rows = server_main.import_repository.list_chunks(rerun.id)
    assert completed.status == "completed"
    assert completed.total_chunks == len(leaves) == 3
    assert completed.processed_chunks == len(leaves)
    assert completed.imported_chunks == 0
    assert completed.skipped_chunks == len(leaves)
    assert completed.failed_chunks == 0
    assert completed.memories_created == 0
    assert completed.graph_status == "skipped"
    assert {row.import_key for row in rerun_rows} == set(leaf_keys)
    assert {row.status for row in rerun_rows} == {"skipped"}


def test_missed_split_snapshot_dynamically_expands_nested_tree(
    import_server,
    monkeypatch,
    tmp_path,
):
    server_main, _, _ = import_server
    import chat_import

    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_split_depth=2,
    )
    messages = []
    for index in range(2):
        messages.extend(
            [
                chat_import.ChatMessage("user", f"race fact {index} " + "detail " * 800),
                chat_import.ChatMessage("assistant", f"race answer {index} " + "context " * 800),
            ]
        )
    conversation = chat_import.Conversation(
        id="missed-nested-split",
        title="Missed nested split",
        messages=messages,
        source_path="chat.md",
        source_app="generic",
    )
    root = chat_import.adaptive_chunk_messages(conversation.messages, options)[0]
    root_key = chat_import._message_chunk_import_key(conversation, root, options)
    root_children = chat_import.split_message_chunk(root, root_key)
    assert len(root_children) == 2
    nested_key = chat_import._message_chunk_import_key(conversation, root_children[0], options)
    nested_children = chat_import.split_message_chunk(root_children[0], nested_key)
    assert len(nested_children) == 2
    leaves = [*nested_children, root_children[1]]

    prior = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="racing-split-owner",
        status="completed",
    )
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)

    def persist_prior_tree():
        rows = [(root, root_key, "split"), (root_children[0], nested_key, "split")]
        rows.extend(
            (leaf, chat_import._message_chunk_import_key(conversation, leaf, options), "succeeded") for leaf in leaves
        )
        for index, (chunk, import_key, status) in enumerate(rows):
            row = contender.upsert_chunk(
                job_id=prior.id,
                project_id=prior.project_id,
                import_key=import_key,
                conversation_id=conversation.id,
                chunk_index=index,
                parent_import_key=chunk.parent_import_key,
                split_depth=chunk.split_depth,
                status=status,
            )
            claimed, _ = contender.claim_manifest(
                prior.project_id,
                import_key,
                prior.id,
                row.id,
            )
            assert claimed is True
            contender.mark_manifest(
                prior.project_id,
                import_key,
                ("released" if import_key == nested_key else "split" if status == "split" else "succeeded"),
                memory_ids=[],
            )

    input_root = tmp_path / "missed-split-rerun"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("User: placeholder", encoding="utf-8")
    rerun = server_main.import_jobs.create(
        prior.project_id,
        [transcript.name],
        options,
        job_id="missed-split-contender",
    )
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])
    hooks = server_main._import_hooks(rerun.id)
    load_calls = []

    def load_then_race(current_job_id):
        load_calls.append(current_job_id)
        assert (
            server_main.import_repository.load_persisted_chunk_statuses(
                prior.project_id,
                current_job_id,
            )
            == {}
        )
        persist_prior_tree()
        return {}

    hooks.load_chunk_statuses = load_then_race

    chat_import.run_import_job(
        rerun.id,
        [transcript],
        input_root,
        input_root / "extracted",
        options,
        lambda _payload: pytest.fail("a dynamically expanded persisted split must not be stored"),
        hooks=hooks,
        retain_workspace=True,
    )

    completed = server_main.import_jobs.get(rerun.id, refresh=True)
    rerun_rows = server_main.import_repository.list_chunks(rerun.id)
    assert load_calls == [rerun.id]
    assert completed.status == "completed"
    assert completed.total_chunks == len(leaves) == 3
    assert completed.processed_chunks == len(leaves)
    assert completed.imported_chunks == 0
    assert completed.skipped_chunks == len(leaves)
    assert completed.failed_chunks == 0
    assert completed.split_chunks == 2
    assert completed.graph_status == "skipped"
    assert sum(row.status == "split" for row in rerun_rows) == 2
    assert sum(row.status == "skipped" for row in rerun_rows) == len(leaves)
    manifests = contender.load_manifests(prior.project_id, [root_key, nested_key])
    assert {manifest.status for manifest in manifests.values()} == {"split"}


def test_retry_preserves_superseded_nested_split_and_skips_unfinished_leaves(
    import_server,
    monkeypatch,
    tmp_path,
):
    server_main, memory, _ = import_server
    import chat_import

    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter=0,
        max_split_depth=2,
    )
    conversation = chat_import.Conversation(
        id="superseded-nested-split",
        title="Superseded nested split",
        messages=[
            chat_import.ChatMessage("user", "first " * 800, source_index=0),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=1),
            chat_import.ChatMessage("user", "second " * 800, source_index=2),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=3),
        ],
        source_path="chat.md",
    )
    root = chat_import.adaptive_chunk_messages(conversation.messages, options)[0]
    root_key = chat_import._message_chunk_import_key(conversation, root, options)
    root_children = chat_import.split_message_chunk(root, root_key)
    assert len(root_children) == 2
    nested_key = chat_import._message_chunk_import_key(conversation, root_children[0], options)
    nested_children = chat_import.split_message_chunk(root_children[0], nested_key)
    assert len(nested_children) == 2
    leaves = [*nested_children, root_children[1]]
    leaf_keys = [chat_import._message_chunk_import_key(conversation, leaf, options) for leaf in leaves]

    input_root = tmp_path / "superseded-nested-retry"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("User: placeholder", encoding="utf-8")
    current = server_main.import_jobs.create(
        "default-project",
        [transcript.name],
        options,
        job_id="superseded-current-job",
        status="completed_with_errors",
    )

    def persist_current_chunk(chunk, import_key, status, index, **values):
        return server_main.import_repository.upsert_chunk(
            job_id=current.id,
            project_id=current.project_id,
            import_key=import_key,
            conversation_id=conversation.id,
            chunk_index=index,
            chunk_count=2 if chunk.parent_import_key else 1,
            parent_import_key=chunk.parent_import_key,
            split_depth=chunk.split_depth,
            status=status,
            **values,
        )

    root_row = persist_current_chunk(root, root_key, "split", 0)
    nested_row = persist_current_chunk(root_children[0], nested_key, "split", 0)
    succeeded_row = persist_current_chunk(
        leaves[0],
        leaf_keys[0],
        "succeeded",
        0,
        memory_ids=["existing-leaf-memory"],
    )
    failed_row = persist_current_chunk(
        leaves[1],
        leaf_keys[1],
        "failed",
        1,
        error_type="temporary_failure",
        error_message="retry me",
        next_retry_at=datetime.now(timezone.utc),
    )
    for row, status, memory_ids in (
        (nested_row, "split", []),
        (succeeded_row, "succeeded", ["existing-leaf-memory"]),
        (failed_row, "failed", []),
    ):
        claimed, _ = server_main.import_repository.claim_manifest(
            current.project_id,
            row.import_key,
            current.id,
            row.id,
        )
        assert claimed is True
        server_main.import_repository.mark_manifest(
            current.project_id,
            row.import_key,
            status,
            memory_ids=memory_ids,
            last_error="retry me" if status == "failed" else None,
        )

    later = server_main.import_jobs.create(
        current.project_id,
        [transcript.name],
        options,
        job_id="later-root-success",
        status="completed",
    )
    later_root = server_main.import_repository.upsert_chunk(
        job_id=later.id,
        project_id=later.project_id,
        import_key=root_key,
        conversation_id=conversation.id,
        status="succeeded",
        memory_ids=["later-root-memory"],
    )
    claimed, _ = server_main.import_repository.claim_manifest(
        later.project_id,
        root_key,
        later.id,
        later_root.id,
    )
    assert claimed is True
    server_main.import_repository.mark_manifest(
        later.project_id,
        root_key,
        "succeeded",
        memory_ids=["later-root-memory"],
    )
    later_missing_leaf = server_main.import_repository.upsert_chunk(
        job_id=later.id,
        project_id=later.project_id,
        import_key=leaf_keys[2],
        conversation_id=conversation.id,
        status="succeeded",
        memory_ids=["later-leaf-memory"],
    )
    claimed, _ = server_main.import_repository.claim_manifest(
        later.project_id,
        leaf_keys[2],
        later.id,
        later_missing_leaf.id,
    )
    assert claimed is True
    server_main.import_repository.mark_manifest(
        later.project_id,
        leaf_keys[2],
        "succeeded",
        memory_ids=["later-leaf-memory"],
    )

    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    monkeypatch.setattr(chat_import, "adaptive_chunk_messages", lambda *_args: [root])
    memory.add.side_effect = AssertionError("superseded split leaves must not be stored")

    chat_import.run_import_job(
        current.id,
        [transcript],
        input_root,
        input_root / "extracted",
        options,
        lambda _payload: pytest.fail("context-aware storage must be used"),
        store_payload_with_context=lambda payload, execution: server_main._store_import_chunk(
            payload,
            execution,
            options,
        ),
        hooks=server_main._import_hooks(current.id),
        retain_workspace=True,
    )

    completed = server_main.import_jobs.get(current.id, refresh=True)
    rows = {row.import_key: row for row in server_main.import_repository.list_chunks(current.id)}
    assert memory.add.call_count == 0
    assert rows[root_key].id == root_row.id
    assert rows[root_key].status == "split"
    assert rows[nested_key].status == "split"
    assert rows[leaf_keys[0]].status == "succeeded"
    assert rows[leaf_keys[1]].status == "skipped"
    assert rows[leaf_keys[2]].status == "skipped"
    assert rows[leaf_keys[1]].error_type is None
    assert rows[leaf_keys[1]].error_message is None
    assert rows[leaf_keys[1]].next_retry_at is None
    assert rows[leaf_keys[1]].audit_metadata == {"skip_reason": "superseded_split"}
    assert rows[leaf_keys[2]].audit_metadata == {"skip_reason": "superseded_split"}
    assert completed.status == "completed"
    assert completed.total_chunks == 3
    assert completed.processed_chunks == 3
    assert completed.imported_chunks == 1
    assert completed.skipped_chunks == 2
    assert completed.failed_chunks == 0
    assert completed.split_chunks == 2

    manifests = server_main.import_repository.load_manifests(
        current.project_id,
        [root_key, nested_key, *leaf_keys],
    )
    assert manifests[root_key].job_id == later.id
    assert manifests[root_key].status == "succeeded"
    assert manifests[nested_key].job_id == current.id
    assert manifests[nested_key].status == "split"
    assert manifests[leaf_keys[0]].memory_ids == ["existing-leaf-memory"]
    assert manifests[leaf_keys[1]].job_id == current.id
    assert manifests[leaf_keys[1]].status == "succeeded"
    assert manifests[leaf_keys[1]].memory_ids == []
    assert manifests[leaf_keys[2]].job_id == later.id
    assert manifests[leaf_keys[2]].status == "succeeded"
    assert manifests[leaf_keys[2]].memory_ids == ["later-leaf-memory"]


def test_cancel_uses_fresh_cross_process_status(import_server):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="fresh-cancel")
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)
    contender.update_job(job.id, status="importing", phase="extracting")

    response = client.post(f"/memory-imports/{job.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelling"
    persisted = contender.get_job(job.id)
    assert persisted.status == "cancelling"
    assert persisted.cancel_requested is True


def test_graph_retry_increments_persisted_attempt_count(import_server, monkeypatch):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="graph-retry")
    server_main.import_jobs.update(
        job.id,
        status="completed_with_errors",
        phase="completed",
        graph_status="failed",
        graph_attempts=1,
    )
    retried = threading.Event()

    def sync_graph(job_id, *, include_failed=False, lease_owner=None):
        assert job_id == job.id
        assert include_failed is True
        assert lease_owner
        retried.set()
        return "completed"

    monkeypatch.setattr(server_main, "_sync_import_graph", sync_graph)

    response = client.post(f"/memory-imports/{job.id}/graph-retry")
    assert response.status_code == 200
    assert retried.wait(2)
    deadline = time.monotonic() + 2
    result = response.json()
    while time.monotonic() < deadline:
        result = client.get(f"/memory-imports/{job.id}").json()
        if result["status"] == "completed":
            break
        time.sleep(0.01)

    assert result["status"] == "completed"
    assert result["graph_attempts"] == 2
    assert result["metrics"]["phase_durations_ms"]["neo4j"] >= 0


def test_graph_retry_rejects_an_active_import(import_server):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="active-graph-retry")
    server_main.import_jobs.update(job.id, status="importing", phase="extracting", graph_status="pending")

    response = client.post(f"/memory-imports/{job.id}/graph-retry")

    assert response.status_code == 409
    assert server_main.import_jobs.get(job.id, refresh=True).status == "importing"


def test_graph_retry_stale_worker_cannot_commit_after_lease_takeover(import_server, monkeypatch):
    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create(
        "default-project",
        [],
        options,
        job_id="stale-graph-retry",
    )
    server_main.import_jobs.update(
        job.id,
        status="completed_with_errors",
        phase="completed",
        graph_status="failed",
        source_retry_required=False,
    )
    chunk = server_main.import_repository.upsert_chunk(
        job_id=job.id,
        project_id=job.project_id,
        import_key="stale-graph-key",
        conversation_id="conversation-1",
        status="succeeded",
    )
    graph_item = server_main.import_repository.add_graph_items(
        job.id,
        chunk.id,
        [{"memory_id": "memory-1", "text": "tea", "metadata": {}}],
    )[0]
    server_main.import_repository.mark_graph_items(graph_item.id, "failed", "old failure")
    owner_a = "stale-graph-owner"
    activated = server_main.import_repository.activate_graph_retry(
        job.id,
        job.project_id,
        owner_a,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
        max_active_jobs=server_main.MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT,
    )
    assert activated is not None

    graph_entered = threading.Event()
    release_graph = threading.Event()

    def blocked_upsert(_items):
        graph_entered.set()
        assert release_graph.wait(5)

    monkeypatch.setattr(server_main, "graph_is_configured", lambda: True)
    monkeypatch.setattr(server_main, "upsert_memories_batch", blocked_upsert)
    runner = threading.Thread(target=server_main._run_graph_retry, args=(job.id, owner_a))
    runner.start()
    assert graph_entered.wait(5)

    contender = server_main.ImportRepository(server_main.import_repository._session_factory)
    owner_b = "replacement-graph-owner"
    takeover_at = datetime.now(timezone.utc) + timedelta(seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS + 1)
    assert contender.acquire_job_lease(
        job.id,
        owner_b,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
        now=takeover_at,
    )
    contender.mark_graph_items(
        graph_item.id,
        "synced",
        job_id=job.id,
        lease_owner=owner_b,
    )
    contender.update_job(
        job.id,
        lease_owner=owner_b,
        status="completed",
        phase="completed",
        graph_status="completed",
        graph_error=None,
    )
    before = contender.get_job(job.id)
    before_items = contender.list_graph_items(job.id, status=None)
    before_errors = contender.list_errors(job.id)

    release_graph.set()
    runner.join(5)
    assert not runner.is_alive()

    after = contender.get_job(job.id)
    after_items = contender.list_graph_items(job.id, status=None)
    after_errors = contender.list_errors(job.id)
    assert after.status == before.status == "completed"
    assert after.phase == before.phase == "completed"
    assert after.graph_status == before.graph_status == "completed"
    assert after.graph_attempts == before.graph_attempts
    assert after.phase_durations == before.phase_durations
    assert after.lease_owner == before.lease_owner == owner_b
    assert [(item.id, item.status, item.last_error) for item in after_items] == [
        (item.id, item.status, item.last_error) for item in before_items
    ]
    assert [(error.id, error.message) for error in after_errors] == [
        (error.id, error.message) for error in before_errors
    ]
    contender.release_job_lease(job.id, owner_b)


def test_general_retry_directs_graph_only_failure_to_graph_retry(import_server):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="graph-only-failure")
    server_main.import_jobs.update(
        job.id,
        status="completed_with_errors",
        phase="completed",
        graph_status="failed",
        graph_failed_items=1,
        source_retry_required=False,
    )

    response = client.post(f"/memory-imports/{job.id}/retry")

    assert response.status_code == 409
    assert "graph-retry" in response.json()["detail"]


def test_general_retry_uses_persisted_source_retry_flag_when_no_chunk_is_failed(import_server, monkeypatch):
    server_main, _, client = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    workspace = server_main.MEMORY_IMPORT_STORAGE_ROOT / "stranded-source-retry"
    upload_root = workspace / "uploads"
    upload_root.mkdir(parents=True)
    (upload_root / "chat.md").write_text("User: remember tea", encoding="utf-8")
    job = server_main.import_jobs.create(
        "default-project",
        ["chat.md"],
        options,
        job_id="stranded-source-retry",
        workspace=str(workspace),
    )
    server_main.import_jobs.update(
        job.id,
        status="completed_with_errors",
        phase="completed",
        graph_status="failed",
        source_retry_required=True,
        workspace_bytes=18,
    )
    server_main.import_repository.upsert_chunk(
        job_id=job.id,
        project_id=job.project_id,
        import_key="cancelled-key",
        conversation_id="conversation-1",
        status="cancelled",
    )
    started = MagicMock(return_value=True)
    monkeypatch.setattr(server_main, "_start_memory_import_thread", started)

    response = client.post(f"/memory-imports/{job.id}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    started.assert_called_once()
    assert workspace.exists()


def test_resumed_import_graph_hook_includes_previously_failed_items(import_server, monkeypatch):
    server_main, _, _ = import_server
    job = server_main.import_jobs.create(
        "default-project",
        [],
        server_main.ImportOptions(entities={"user_id": "me"}),
        job_id="resumed-graph-items",
    )
    sync_graph = MagicMock(return_value="completed")
    monkeypatch.setattr(server_main, "_sync_import_graph", sync_graph)

    result = server_main._import_hooks(job.id).sync_graph(job.id)

    assert result == "completed"
    sync_graph.assert_called_once_with(job.id, include_failed=True)


def test_cancelled_chunk_update_releases_manifest_and_unpersisted_hash(import_server):
    import chat_import

    server_main, _, _ = import_server
    options = server_main.ImportOptions(entities={"user_id": "me"})
    job = server_main.import_jobs.create("default-project", [], options, job_id="cancelled-claim")
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="conversation-1",
        messages=[chat_import.ChatMessage("user", "Remember tea.", source_index=0)],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=conversation.messages,
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )
    execution = server_main.ChunkExecution(
        job_id=job.id,
        conversation=conversation,
        chunk=chunk,
        chunk_index=0,
        chunk_count=1,
        import_key="cancelled-import-key",
        attempt=1,
        phase_callback=lambda _phase, _seconds: None,
        force_fallback_reason=None,
        audit=False,
        obvious_facts=True,
    )
    chunk_row = server_main._ensure_import_chunk(execution)
    claimed, _ = server_main.import_repository.claim_manifest(
        job.project_id,
        execution.import_key,
        job.id,
        chunk_row.id,
    )
    assert claimed is True
    assert server_main.import_repository.claim_memory_hash(
        job.project_id,
        conversation.id,
        "memory-hash",
        job.id,
        chunk_row.id,
    )

    server_main._update_import_chunk(execution, "cancelled", {})

    manifest = server_main.import_repository.load_manifests(job.project_id, [execution.import_key])
    assert manifest[execution.import_key].status == "released"
    assert server_main.import_repository.get_chunk(job.id, execution.import_key).status == "cancelled"
    assert server_main.import_repository.claim_memory_hash(
        job.project_id,
        conversation.id,
        "memory-hash",
        job.id,
        chunk_row.id,
    )


def test_post_vector_failure_reconciles_rows_before_retrying_extraction(import_server, monkeypatch):
    server_main, memory, _client = import_server
    import chat_import
    import models

    webhook_queue = server_main.queue_webhook_event
    webhook_queue.reset_mock()

    options = server_main.ImportOptions(entities={"user_id": "me"}, model_tiering_enabled=False)
    job = server_main.import_jobs.create("default-project", [], options, job_id="reconcile-vector-row")
    message = chat_import.ChatMessage(role="user", content="Remember tea", source_index=0)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[message],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=[message],
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )
    payload = chat_import.build_payload(conversation, chunk, 0, 1, options)
    execution = server_main.ChunkExecution(
        job_id=job.id,
        conversation=conversation,
        chunk=chunk,
        chunk_index=0,
        chunk_count=1,
        import_key=payload["metadata"]["import_key"],
        attempt=1,
        phase_callback=lambda _phase, _seconds: None,
        force_fallback_reason=None,
        audit=False,
        obvious_facts=False,
    )
    assert server_main._claim_import_chunk(execution, payload) == "claimed"

    vector_row = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        payload={
            "data": "The user prefers tea.",
            "hash": "hash-1",
            "project_id": job.project_id,
            "conversation_id": conversation.id,
            "import_key": execution.import_key,
            "source_message_indices": [0],
            "confidence": 0.95,
        },
    )
    store_calls = 0

    def fake_store_memory(_memory_create, _project_id, *, operation_context, sync_graph):
        nonlocal store_calls
        store_calls += 1
        assert sync_graph is False
        assert operation_context.memory_hash_claim("hash-1")
        operation_context.claimed_memory_hashes.append("hash-1")
        memory.vector_store.list.return_value = [[vector_row]]
        response = {
            "results": [
                {
                    "id": vector_row.id,
                    "event": "ADD",
                    "memory": vector_row.payload["data"],
                    "source_message_indices": [0],
                    "confidence": 0.95,
                }
            ]
        }
        server_main._queue_memory_added_webhook(
            response,
            _project_id,
            import_key=_memory_create.metadata["import_key"],
            execution_guard=operation_context.execution_guard,
        )
        return response

    monkeypatch.setattr(server_main, "_store_memory", fake_store_memory)
    original_mark_succeeded = server_main.import_repository.mark_memory_hashes_succeeded
    mark_attempts = 0

    def fail_first_hash_bookkeeping(*args, **kwargs):
        nonlocal mark_attempts
        mark_attempts += 1
        if mark_attempts == 1:
            raise RuntimeError("database unavailable after vector commit")
        return original_mark_succeeded(*args, **kwargs)

    monkeypatch.setattr(
        server_main.import_repository,
        "mark_memory_hashes_succeeded",
        fail_first_hash_bookkeeping,
    )

    with pytest.raises(RuntimeError, match="after vector commit"):
        server_main._store_import_chunk(payload, execution, options)

    side_effect_attempts = 0

    def fail_first_side_effect_replay(*_args, **_kwargs):
        nonlocal side_effect_attempts
        side_effect_attempts += 1
        if side_effect_attempts == 1:
            raise RuntimeError("history repair unavailable")

    memory._complete_import_side_effects.side_effect = fail_first_side_effect_replay

    recovered_execution = replace(execution, reconcile_existing=False)
    assert server_main._claim_import_chunk(recovered_execution, payload) == "claimed"
    assert recovered_execution.reconcile_existing is True
    with pytest.raises(RuntimeError, match="history repair unavailable"):
        server_main._store_import_chunk(payload, recovered_execution, options)

    manifest = server_main.import_repository.load_manifests(job.project_id, [execution.import_key])
    assert manifest[execution.import_key].status == "claimed"
    final_execution = replace(execution, reconcile_existing=False)
    assert server_main._claim_import_chunk(final_execution, payload) == "claimed"
    assert final_execution.reconcile_existing is True
    reconciled = server_main._store_import_chunk(payload, final_execution, options)

    assert store_calls == 1
    assert side_effect_attempts == 2
    assert reconciled["memory_ids"] == [vector_row.id]
    manifest = server_main.import_repository.load_manifests(job.project_id, [execution.import_key])
    assert manifest[execution.import_key].status == "succeeded"
    assert manifest[execution.import_key].memory_ids == [vector_row.id]
    graph_items = server_main.import_repository.list_graph_items(job.id)
    assert [item.memory_id for item in graph_items] == [vector_row.id]
    assert graph_items[0].payload["text"] == vector_row.payload["data"]
    assert graph_items[0].payload["metadata"]["hash"] == "hash-1"
    assert "data" not in graph_items[0].payload["metadata"]
    with server_main.import_repository._session_factory() as session:
        memory_hash = session.scalar(select(models.MemoryImportHash))
    assert memory_hash.status == "succeeded"
    assert memory_hash.memory_id == vector_row.id
    assert webhook_queue.call_count == 2
    first_delivery, recovered_delivery = webhook_queue.call_args_list
    assert first_delivery.args == recovered_delivery.args
    assert first_delivery.kwargs["delivery_key"] == recovered_delivery.kwargs["delivery_key"]
    assert first_delivery.kwargs["delivery_key"].startswith("memory-import-")


def test_reconciliation_guard_propagates_lease_takeover_before_side_effects(import_server):
    server_main, memory, _ = import_server
    import chat_import

    options = server_main.ImportOptions(entities={"user_id": "me"}, model_tiering_enabled=False)
    job = server_main.import_jobs.create("default-project", [], options, job_id="fenced-reconciliation")
    owner_a = "reconciliation-owner-a"
    assert server_main.import_repository.acquire_job_lease(
        job.id,
        owner_a,
        lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
    )
    message = chat_import.ChatMessage(role="user", content="Remember tea", source_index=0)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[message],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=[message],
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )
    payload = chat_import.build_payload(conversation, chunk, 0, 1, options)
    execution = server_main.ChunkExecution(
        job_id=job.id,
        conversation=conversation,
        chunk=chunk,
        chunk_index=0,
        chunk_count=1,
        import_key=payload["metadata"]["import_key"],
        attempt=2,
        phase_callback=lambda _phase, _seconds: None,
        force_fallback_reason=None,
        audit=False,
        obvious_facts=False,
        reconcile_existing=True,
        lease_owner=owner_a,
    )
    assert server_main._claim_import_chunk(execution, payload) == "claimed"
    vector_row = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        payload={
            "data": "The user prefers tea.",
            "hash": "hash-1",
            "project_id": job.project_id,
            "conversation_id": conversation.id,
            "import_key": execution.import_key,
            "source_message_indices": [0],
            "confidence": 0.95,
        },
    )
    memory.vector_store.list.return_value = [[vector_row]]
    contender = server_main.ImportRepository(server_main.import_repository._session_factory)
    owner_b = "reconciliation-owner-b"

    def complete_side_effects(*_args, operation_context):
        assert operation_context.execution_guard is not None
        assert contender.acquire_job_lease(
            job.id,
            owner_b,
            lease_seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS,
            now=datetime.now(timezone.utc) + timedelta(seconds=server_main.MEMORY_IMPORT_LEASE_SECONDS + 1),
        )
        operation_context.execution_guard()

    memory._complete_import_side_effects.side_effect = complete_side_effects

    with pytest.raises(server_main.ImportLeaseLost):
        server_main._store_import_chunk(payload, execution, options)

    manifest = contender.load_manifests(job.project_id, [execution.import_key])[execution.import_key]
    assert manifest.status == "claimed"
    assert contender.list_graph_items(job.id, status=None) == []
    assert contender.get_job(job.id).lease_owner == owner_b
    server_main.queue_webhook_event.assert_not_called()
    contender.release_job_lease(job.id, owner_b)


def test_store_memory_checks_import_lease_after_durable_add_before_webhook(import_server):
    server_main, memory, _ = import_server
    guard = MagicMock(side_effect=server_main.ImportLeaseLost("job-1", "stale-owner"))
    context = server_main.MemoryOperationContext(execution_guard=guard)
    memory_create = server_main.MemoryCreate(
        messages=[server_main.Message(role="user", content="Remember tea")],
        user_id="me",
        metadata={"import_key": "import-key-1"},
    )

    with pytest.raises(server_main.ImportLeaseLost):
        server_main._store_memory(
            memory_create,
            "default-project",
            operation_context=context,
            sync_graph=False,
        )

    memory.add.assert_called_once()
    guard.assert_called_once_with()
    server_main.queue_webhook_event.assert_not_called()


def test_non_tiered_import_uses_import_specific_runtime_llm(import_server, monkeypatch):
    server_main, _, _ = import_server
    import chat_import

    options = server_main.ImportOptions(entities={"user_id": "me"}, model_tiering_enabled=False)
    job = server_main.import_jobs.create("default-project", [], options, job_id="non-tiered-runtime-llm")
    message = chat_import.ChatMessage(role="user", content="Remember tea", source_index=0)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[message],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=[message],
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )
    payload = chat_import.build_payload(conversation, chunk, 0, 1, options)
    execution = server_main.ChunkExecution(
        job_id=job.id,
        conversation=conversation,
        chunk=chunk,
        chunk_index=0,
        chunk_count=1,
        import_key=payload["metadata"]["import_key"],
        attempt=1,
        phase_callback=lambda _phase, _seconds: None,
        force_fallback_reason=None,
        audit=False,
        obvious_facts=False,
    )
    runtime_llm = MagicMock(name="runtime-import-llm")
    selected_models = []
    captured = {}

    def import_llm(model, **kwargs):
        selected_models.append((model, kwargs))
        return runtime_llm

    def store_memory(_memory_create, _project_id, *, operation_context, sync_graph):
        captured["context"] = operation_context
        assert sync_graph is False
        return {"results": []}

    monkeypatch.setattr(
        server_main,
        "get_current_config",
        lambda: {"llm": {"config": {"model": "gemini-2.5-pro"}}},
    )
    monkeypatch.setattr(server_main, "_import_llm", import_llm)
    monkeypatch.setattr(server_main, "_store_memory", store_memory)

    result = server_main._store_import_chunk(payload, execution, options)

    assert result["model_used"] is None
    assert result["pressure_fallback"] is False
    assert selected_models == [("gemini-2.5-pro", {})]
    assert captured["context"].primary_llm is runtime_llm
    assert captured["context"].fallback_llm is None
    assert captured["context"].primary_model_label == "gemini-2.5-pro"


def test_tiered_import_uses_explicit_route_for_both_models(import_server, monkeypatch):
    server_main, _, _ = import_server
    import chat_import

    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        model_tiering_enabled=True,
        fast_model="gemini-fast",
        fallback_model="gemini-pro",
    )
    job = server_main.import_jobs.create("default-project", [], options, job_id="tiered-routed-llms")
    message = chat_import.ChatMessage(role="user", content="Remember tea", source_index=0)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[message],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=[message],
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )
    payload = chat_import.build_payload(conversation, chunk, 0, 1, options)
    execution = server_main.ChunkExecution(
        job_id=job.id,
        conversation=conversation,
        chunk=chunk,
        chunk_index=0,
        chunk_count=1,
        import_key=payload["metadata"]["import_key"],
        attempt=1,
        phase_callback=lambda _phase, _seconds: None,
        force_fallback_reason=None,
        audit=False,
        obvious_facts=False,
    )
    selected = []
    models = {"gemini-fast": MagicMock(name="fast"), "gemini-pro": MagicMock(name="pro")}
    captured = {}

    def import_llm(model, **kwargs):
        selected.append((model, kwargs))
        return models[model]

    def store_memory(_memory_create, _project_id, *, operation_context, sync_graph):
        captured["context"] = operation_context
        assert sync_graph is False
        return {"results": []}

    monkeypatch.setattr(server_main, "_import_llm", import_llm)
    monkeypatch.setattr(server_main, "_store_memory", store_memory)

    server_main._store_import_chunk(payload, execution, options)

    assert selected == [
        ("gemini-fast", {"use_import_route": True}),
        ("gemini-pro", {"use_import_route": True}),
    ]
    assert captured["context"].primary_llm is models["gemini-fast"]
    assert captured["context"].fallback_llm is models["gemini-pro"]


def test_same_transcript_in_same_project_is_distinct_across_entity_scopes(import_server, monkeypatch):
    server_main, _, _ = import_server
    import chat_import
    import models

    message = chat_import.ChatMessage(role="user", content="Remember tea", source_index=0)
    conversation = chat_import.Conversation(
        id="conversation-1",
        title="Tea",
        messages=[message],
        source_path="chat.md",
    )
    chunk = chat_import.MessageChunk(
        messages=[message],
        token_count=10,
        source_indices=[0],
        core_source_indices=[0],
    )

    def execution_for(job, options):
        payload = chat_import.build_payload(conversation, chunk, 0, 1, options)
        return payload, server_main.ChunkExecution(
            job_id=job.id,
            conversation=conversation,
            chunk=chunk,
            chunk_index=0,
            chunk_count=1,
            import_key=payload["metadata"]["import_key"],
            attempt=1,
            phase_callback=lambda _phase, _seconds: None,
            force_fallback_reason=None,
            audit=False,
            obvious_facts=False,
        )

    options_a = server_main.ImportOptions(entities={"user_id": "user-a"}, model_tiering_enabled=False)
    options_b = server_main.ImportOptions(entities={"user_id": "user-b"}, model_tiering_enabled=False)
    job_a = server_main.import_jobs.create("project-1", [], options_a, job_id="entity-scope-a")
    job_b = server_main.import_jobs.create("project-1", [], options_b, job_id="entity-scope-b")
    payload_a, execution_a = execution_for(job_a, options_a)
    payload_b, execution_b = execution_for(job_b, options_b)

    def store_memory(_memory_create, _project_id, *, operation_context, sync_graph):
        assert sync_graph is False
        assert operation_context.memory_hash_claim("same-memory-hash") is True
        operation_context.claimed_memory_hashes.append("same-memory-hash")
        memory_id = operation_context.id_factory("same-memory-hash")
        return {"results": [{"id": memory_id, "event": "ADD", "memory": "The user prefers tea."}]}

    monkeypatch.setattr(server_main, "_store_memory", store_memory)

    result_a = server_main._store_import_chunk(payload_a, execution_a, options_a)
    result_b = server_main._store_import_chunk(payload_b, execution_b, options_b)

    assert execution_a.import_key != execution_b.import_key
    assert result_a["memory_ids"] != result_b["memory_ids"]
    with server_main.import_repository._session_factory() as session:
        hashes = session.scalars(
            select(models.MemoryImportHash).order_by(models.MemoryImportHash.conversation_id)
        ).all()
        assert len(hashes) == 2
        assert hashes[0].conversation_id != hashes[1].conversation_id
        assert {row.memory_id for row in hashes} == {result_a["memory_ids"][0], result_b["memory_ids"][0]}


def test_legacy_hash_lookup_requires_exact_entity_scope(import_server):
    server_main, memory, _ = import_server
    memory.vector_store.list.return_value = [
        [
            SimpleNamespace(
                id="user-only",
                payload={
                    "project_id": "project-1",
                    "user_id": "user-a",
                    "conversation_id": "conversation-1",
                    "hash": "hash-user-only",
                },
            ),
            SimpleNamespace(
                id="user-and-app",
                payload={
                    "project_id": "project-1",
                    "user_id": "user-a",
                    "app_id": "app-x",
                    "conversation_id": "conversation-1",
                    "hash": "hash-user-and-app",
                },
            ),
        ]
    ]

    hashes = server_main._existing_import_memory_hashes("project-1", {"user_id": "user-a"})

    assert hashes == {("conversation-1", "hash-user-only"): "user-only"}


def test_persisted_split_retry_only_executes_failed_leaf(import_server, monkeypatch, tmp_path):
    server_main, memory, _client = import_server
    import chat_import

    input_root = tmp_path / "persisted-split-retry"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    conversation = chat_import.Conversation(
        id="split-retry",
        title="Split retry",
        messages=[
            chat_import.ChatMessage("user", "first " * 800, source_index=0),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=1),
            chat_import.ChatMessage("user", "second " * 800, source_index=2),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=3),
        ],
        source_path=transcript.name,
    )
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        model_tiering_enabled=False,
        max_attempts=1,
        retry_jitter=0,
        max_split_depth=2,
    )
    job = server_main.import_jobs.create(
        "default-project",
        [transcript.name],
        options,
        job_id="persisted-split-retry",
    )

    def run_job():
        chat_import.run_import_job(
            job.id,
            [transcript],
            input_root,
            input_root / "extracted",
            options,
            lambda _payload: {},
            store_payload_with_context=lambda payload, execution: server_main._store_import_chunk(
                payload,
                execution,
                options,
            ),
            hooks=server_main._import_hooks(job.id),
            retain_workspace=True,
        )

    def first_attempt(*_args, **kwargs):
        metadata = kwargs["metadata"]
        core_indices = metadata["core_source_message_indices"]
        if len(core_indices) > 2:
            raise RuntimeError("LLM response was truncated at the output limit")
        if min(core_indices) == 0:
            raise RuntimeError("temporary first leaf failure")
        return {
            "results": [
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "event": "ADD",
                    "memory": "second leaf",
                }
            ]
        }

    memory.add.side_effect = first_attempt
    run_job()

    first_result = server_main.import_jobs.get(job.id, refresh=True)
    rows = server_main.import_repository.list_chunks(job.id)
    split_parents = [row for row in rows if row.status == "split"]
    failed = next(row for row in rows if row.status == "failed")
    by_key = {row.import_key: row for row in rows}
    ancestor_keys = set()
    ancestor_key = failed.parent_import_key
    while ancestor_key:
        ancestor_keys.add(ancestor_key)
        ancestor_key = by_key[ancestor_key].parent_import_key
    leaf_count = sum(row.status != "split" for row in rows)
    assert first_result.status == "completed_with_errors"
    assert first_result.total_chunks == leaf_count
    assert first_result.failed_chunks == 1
    assert first_result.imported_chunks == leaf_count - 1
    assert split_parents
    assert {row.import_key for row in split_parents} & ancestor_keys

    retry_owner = "split-retry-owner"
    assert (
        server_main.import_repository.acquire_job_retry_lease(
            job.id,
            job.project_id,
            retry_owner,
            lease_seconds=120,
        )
        is not None
    )
    server_main.import_repository.update_chunk(
        job.id,
        failed.import_key,
        status="pending",
        error_type=None,
        error_message=None,
        finished_at=None,
    )
    server_main._recompute_import_progress(job.id)
    server_main.import_jobs.get(job.id, refresh=True)

    retried_core_indices = []

    def retry_failed_leaf(*_args, **kwargs):
        core_indices = list(kwargs["metadata"]["core_source_message_indices"])
        retried_core_indices.append(core_indices)
        assert core_indices == list(failed.core_source_message_indices)
        return {
            "results": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "event": "ADD",
                    "memory": "first leaf",
                }
            ]
        }

    memory.add.reset_mock()
    memory.add.side_effect = retry_failed_leaf
    run_job()
    server_main.import_repository.release_job_lease(job.id, retry_owner)

    completed = server_main.import_jobs.get(job.id, refresh=True)
    final_rows = server_main.import_repository.list_chunks(job.id)
    assert retried_core_indices == [list(failed.core_source_message_indices)]
    assert memory.add.call_count == 1
    assert all(
        next(row for row in final_rows if row.import_key == parent.import_key).status == "split"
        for parent in split_parents
    )
    assert {row.status for row in final_rows if row.status != "split"} == {"succeeded"}
    assert completed.status == "completed"
    assert completed.total_chunks == leaf_count
    assert completed.processed_chunks == leaf_count
    assert completed.imported_chunks == leaf_count
    assert completed.failed_chunks == 0


def test_split_counter_recovers_after_post_persist_hook_failure(import_server, monkeypatch, tmp_path):
    server_main, memory, _ = import_server
    import chat_import

    input_root = tmp_path / "split-counter-recovery"
    input_root.mkdir()
    transcript = input_root / "chat.md"
    transcript.write_text("placeholder", encoding="utf-8")
    conversation = chat_import.Conversation(
        id="split-counter-recovery",
        title="Split counter recovery",
        messages=[
            chat_import.ChatMessage("user", "first " * 800, source_index=0),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=1),
            chat_import.ChatMessage("user", "second " * 800, source_index=2),
            chat_import.ChatMessage("assistant", "answer " * 800, source_index=3),
        ],
        source_path=transcript.name,
    )
    monkeypatch.setattr(chat_import, "parse_file", lambda *_args: [conversation])
    options = server_main.ImportOptions(
        entities={"user_id": "me"},
        model_tiering_enabled=False,
        max_attempts=1,
        retry_jitter=0,
        max_split_depth=1,
    )
    job = server_main.import_jobs.create(
        "default-project",
        [transcript.name],
        options,
        job_id="split-counter-recovery",
    )

    def run_job(hooks):
        chat_import.run_import_job(
            job.id,
            [transcript],
            input_root,
            input_root / "extracted",
            options,
            lambda _payload: {},
            store_payload_with_context=lambda payload, execution: server_main._store_import_chunk(
                payload,
                execution,
                options,
            ),
            hooks=hooks,
            retain_workspace=True,
        )

    memory.add.side_effect = RuntimeError("LLM response was truncated at the output limit")
    failing_hooks = server_main._import_hooks(job.id)
    persist_chunk = failing_hooks.update_chunk
    assert persist_chunk is not None
    failed_after_persist = False

    def fail_after_split_persist(execution, state, details):
        nonlocal failed_after_persist
        persist_chunk(execution, state, details)
        if state == "split" and not failed_after_persist:
            failed_after_persist = True
            raise RuntimeError("injected post-persist split hook failure")

    failing_hooks.update_chunk = fail_after_split_persist
    run_job(failing_hooks)

    interrupted = server_main.import_jobs.get(job.id, refresh=True)
    interrupted_rows = server_main.import_repository.list_chunks(job.id)
    assert failed_after_persist is True
    assert interrupted.status == "failed"
    assert sum(row.status == "split" for row in interrupted_rows) == 1
    assert interrupted.split_chunks == 0

    result_ids = iter(
        [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]
    )

    def store_recovered_leaf(*_args, **_kwargs):
        memory_id = next(result_ids)
        return {
            "results": [
                {
                    "id": memory_id,
                    "event": "ADD",
                    "memory": f"recovered {memory_id}",
                }
            ]
        }

    memory.add.reset_mock()
    memory.add.side_effect = store_recovered_leaf
    run_job(server_main._import_hooks(job.id))

    completed = server_main.import_jobs.get(job.id, refresh=True)
    final_rows = server_main.import_repository.list_chunks(job.id)
    assert memory.add.call_count == 2
    assert sum(row.status == "split" for row in final_rows) == 1
    assert {row.status for row in final_rows if row.status != "split"} == {"succeeded"}
    assert completed.status == "completed"
    assert completed.split_chunks == 1
    assert completed.total_chunks == 2
    assert completed.processed_chunks == 2
    assert completed.imported_chunks == 2
    assert completed.skipped_chunks == 0
    assert completed.failed_chunks == 0
