import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import chat_import  # noqa: E402
from import_quota import (  # noqa: E402
    ImportStorageQuotaExceeded,
    ImportStorageQuotaGuard,
    capture_import_storage_quota_snapshot,
)
from import_repository import ImportRepository  # noqa: E402
from models import Base, QuotaPolicy  # noqa: E402

from mem0.memory.main import Memory  # noqa: E402


@pytest.fixture
def quota_database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quota.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _policy(factory, *, limit_value=10, scope_type="project", scope_id="default-project"):
    with factory() as session:
        policy = QuotaPolicy(
            scope_type=scope_type,
            scope_id=scope_id,
            project_id=scope_id if scope_type == "project" else "",
            metric="stored_memories",
            period="total",
            limit_value=limit_value,
            mode="hard",
            warning_threshold=0.8,
        )
        session.add(policy)
        session.commit()
        session.refresh(policy)
        return policy


def _snapshot(policy):
    return {
        "version": 1,
        "policies": [
            {
                "id": str(policy.id),
                "scope_type": policy.scope_type,
                "scope_id": policy.scope_id,
                "limit_value": policy.limit_value,
            }
        ],
    }


def _request(auth_type="bearer"):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/memory-imports",
            "query_string": b"",
            "headers": [(b"x-project-id", b"default-project")],
        }
    )
    request.state.auth_type = auth_type
    return request


def _raw_memory():
    memory = object.__new__(Memory)
    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.return_value = [0.1, 0.2, 0.3]
    memory.vector_store = MagicMock()
    memory.db = MagicMock()
    return memory


def test_limit_minus_one_rejects_two_selected_before_any_write(quota_database):
    policy = _policy(quota_database, limit_value=10)
    memory = _raw_memory()
    guard = ImportStorageQuotaGuard(
        _snapshot(policy),
        quota_database,
        lambda _project_id: 9,
        lambda _organization_id: [],
    )

    with pytest.raises(ImportStorageQuotaExceeded) as exc_info:
        memory._add_to_vector_store(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "second"},
            ],
            {"project_id": "default-project"},
            filters={},
            infer=False,
            pre_vector_write=guard,
        )

    memory.embedding_model.embed.assert_not_called()
    memory.vector_store.insert.assert_not_called()
    memory.db.add_history.assert_not_called()
    assert exc_info.value.used == 9
    assert exc_info.value.selected_new == 2
    assert exc_info.value.projected == 11
    assert isinstance(exc_info.value, chat_import.PermanentImportError)
    assert "Free capacity and retry the write" in str(exc_info.value)


def test_exact_fit_succeeds_and_holds_lock_until_release(quota_database):
    policy = _policy(quota_database, limit_value=10)
    memory = _raw_memory()
    guard = ImportStorageQuotaGuard(
        _snapshot(policy),
        quota_database,
        lambda _project_id: 9,
        lambda _organization_id: [],
    )

    result = memory._add_to_vector_store(
        [{"role": "user", "content": "exact fit"}],
        {"project_id": "default-project"},
        filters={},
        infer=False,
        pre_vector_write=guard,
    )
    assert guard.enabled is True
    assert len(result) == 1
    memory.vector_store.insert.assert_called_once()
    guard.release()


def test_import_and_ordinary_write_serialize_until_first_write_is_visible(quota_database):
    policy = _policy(quota_database, limit_value=1)
    count = 0
    count_lock = threading.Lock()
    first_checked = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    results = []

    def current_count(_project_id):
        with count_lock:
            return count

    def make_guard():
        return ImportStorageQuotaGuard(
            _snapshot(policy),
            quota_database,
            current_count,
            lambda _organization_id: [],
        )

    def first_writer():
        nonlocal count
        guard = make_guard()
        guard(1)
        first_checked.set()
        assert release_first.wait(2)
        with count_lock:
            count += 1
        results.append("written")
        guard.release()

    def second_writer():
        assert first_checked.wait(2)
        guard = make_guard()
        memory = _raw_memory()
        try:
            memory._add_to_vector_store(
                [{"role": "user", "content": "ordinary write"}],
                {"project_id": "default-project"},
                filters={},
                infer=False,
                pre_vector_write=guard,
            )
            results.append("overcommitted")
        except ImportStorageQuotaExceeded:
            results.append("rejected")
        finally:
            memory.vector_store.insert.assert_not_called()
            guard.release()
            second_done.set()

    first = threading.Thread(target=first_writer)
    second = threading.Thread(target=second_writer)
    first.start()
    second.start()
    assert first_checked.wait(2)
    time.sleep(0.05)
    assert not second_done.is_set()
    release_first.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == ["written", "rejected"]
    assert count == 1


def test_snapshot_survives_job_store_restart_and_is_not_serialized(quota_database):
    policy = _policy(quota_database)
    snapshot = _snapshot(policy)
    repository = ImportRepository(quota_database)
    store = chat_import.ImportJobStore()
    store.configure_repository(repository)
    job = store.create(
        "default-project",
        ["chat.md"],
        chat_import.ImportOptions(entities={"user_id": "me"}),
        storage_quota_snapshot=snapshot,
    )

    restarted = chat_import.ImportJobStore()
    restarted.configure_repository(ImportRepository(quota_database))
    loaded = restarted.get(job.id, refresh=True)

    assert loaded is not None
    assert loaded.storage_quota_snapshot == snapshot
    assert "storage_quota_snapshot" not in restarted.serialize(loaded)


def test_capture_uses_server_policies_and_admin_or_no_policy_bypasses(quota_database):
    policy = _policy(quota_database, limit_value=12)
    with quota_database() as session:
        snapshot = capture_import_storage_quota_snapshot(_request(), None, session)
        admin_snapshot = capture_import_storage_quota_snapshot(
            _request("admin_api_key"),
            None,
            session,
        )
        session.execute(delete(QuotaPolicy))
        session.commit()
        no_policy_snapshot = capture_import_storage_quota_snapshot(_request(), None, session)

    assert snapshot == _snapshot(policy)
    assert admin_snapshot == {}
    assert no_policy_snapshot == {"version": 1, "policies": []}

    guard = ImportStorageQuotaGuard(
        admin_snapshot,
        quota_database,
        lambda _project_id: pytest.fail("no-policy guard must not count vectors"),
        lambda _organization_id: pytest.fail("no-policy guard must not resolve an organization"),
    )
    memory = _raw_memory()
    result = memory._add_to_vector_store(
        [{"role": "user", "content": "admin bypass"}],
        {"project_id": "default-project"},
        filters={},
        infer=False,
        pre_vector_write=guard,
    )
    assert len(result) == 1
    memory.vector_store.insert.assert_called_once()


def test_capacity_failure_skips_immediate_retries_but_remains_manual_retryable(
    tmp_path,
    monkeypatch,
):
    transcript = tmp_path / "chat.md"
    transcript.write_text("User: remember that I prefer tea.\nAssistant: noted.\n", encoding="utf-8")
    options = chat_import.ImportOptions(
        entities={"user_id": "me"},
        workers=1,
        max_attempts=3,
        retry_jitter=0,
        model_tiering_enabled=False,
    )
    store = chat_import.ImportJobStore()
    monkeypatch.setattr(chat_import, "import_jobs", store)
    job = store.create("default-project", [transcript.name], options)
    attempts = 0

    def reject_capacity(_payload):
        nonlocal attempts
        attempts += 1
        raise ImportStorageQuotaExceeded(
            scope_type="project",
            scope_id="default-project",
            limit_value=10,
            used=9,
            selected_new=2,
        )

    chat_import.run_import_job(
        job.id,
        [transcript],
        tmp_path,
        tmp_path / "extracted",
        options,
        reject_capacity,
    )
    completed = store.get(job.id)

    assert completed is not None
    assert attempts == 1
    assert completed.retry_count == 0
    assert completed.failed_chunks == 1
    assert completed.source_retry_required is True
    assert completed.errors[-1]["type"] == "storage_quota_exceeded"
    assert completed.errors[-1]["retryable"] is True
