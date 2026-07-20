import importlib.util
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from import_repository import (  # noqa: E402
    ImportActiveJobLimitExceeded,
    ImportLeaseLost,
    ImportRepository,
    ImportWorkspaceBudgetExceeded,
)
from models import Base, MemoryImportHash  # noqa: E402


def _repositories(tmp_path, count=1):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'imports.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return engine, [ImportRepository(factory) for _ in range(count)]


def _job_and_chunk(repo):
    job = repo.create_job(
        project_id="project-1",
        input_files=["history/chat.md"],
        entities={"user_id": "user-1"},
        options={"worker_count": 3},
        workspace="/tmp/import-job",
        worker_count=3,
        current_concurrency=3,
    )
    chunk = repo.upsert_chunk(
        job_id=job.id,
        project_id=job.project_id,
        import_key="import-key-1",
        conversation_id="conversation-1",
        chunk_index=0,
        source_message_indices=[0, 1, 2, 3, 4, 5, 6, 7],
        core_source_message_indices=[2, 3, 4, 5],
        source_message_start=0,
        source_message_end=7,
        parent_import_key="parent-import-key",
        split_depth=1,
        overlap_turns=2,
        source_path="history/chat.md",
        conversation_title="Preferences",
        token_count=512,
        duration_seconds=1.6,
        timings={"parse": 0.2, "llm": 1.4},
        model_used="fast-model",
        fallback_used=True,
        fallback_reason="schema_validation",
    )
    return job, chunk


def test_active_job_limit_is_atomic_across_repository_instances(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    barrier = Barrier(2)

    def create(repository, suffix):
        barrier.wait()
        try:
            return repository.create_job_with_active_limit(
                1,
                id=f"job-{suffix}",
                project_id="project-1",
                status="uploading",
                input_files=[],
                workspace=f"/tmp/job-{suffix}",
            )
        except ImportActiveJobLimitExceeded as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda item: create(*item), zip(repositories, ("a", "b"))))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, ImportActiveJobLimitExceeded))
    assert rejected.limit == 1
    assert rejected.active_jobs == 1
    assert repositories[0].count_active_jobs("project-1") == 1


def test_workspace_budget_reservation_is_atomic_across_repository_instances(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    jobs = []
    owners = []
    for index, repository in enumerate(repositories):
        job = repository.create_job(
            id=f"job-{index}",
            project_id=f"project-{index}",
            status="uploading",
            input_files=[],
            workspace=f"/tmp/job-{index}",
        )
        owner = f"owner-{index}"
        assert repository.acquire_job_lease(job.id, owner, lease_seconds=30)
        jobs.append(job)
        owners.append(owner)

    barrier = Barrier(2)

    def reserve(index):
        barrier.wait()
        try:
            return repositories[index].reserve_workspace_bytes(
                jobs[index].id,
                owners[index],
                60,
                max_retained_bytes=100,
                lease_seconds=30,
            )
        except ImportWorkspaceBudgetExceeded as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    assert sum(not isinstance(result, Exception) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, ImportWorkspaceBudgetExceeded))
    assert rejected.limit_bytes == 100
    assert rejected.used_bytes == 60
    assert sum(repositories[0].get_job(job.id).workspace_bytes for job in jobs) == 60


def test_source_retry_repair_migration_preserves_graph_only_and_repairs_stranded_jobs(tmp_path):
    engine, repositories = _repositories(tmp_path)
    repository = repositories[0]
    graph_only = repository.create_job(
        id="graph-only",
        project_id="project-1",
        status="completed_with_errors",
        graph_status="failed",
        source_retry_required=False,
    )
    repository.add_error(
        graph_only.id,
        "graph_sync",
        "offline",
        error_type="graph_sync_error",
        retryable=True,
    )
    cancelled = repository.create_job(
        id="cancelled-chunk",
        project_id="project-1",
        status="completed_with_errors",
        graph_status="failed",
        source_retry_required=False,
    )
    repository.upsert_chunk(
        job_id=cancelled.id,
        project_id=cancelled.project_id,
        import_key="cancelled-key",
        conversation_id="conversation-1",
        status="cancelled",
    )
    parse_error = repository.create_job(
        id="parse-error",
        project_id="project-1",
        status="completed_with_errors",
        graph_status="failed",
        source_retry_required=False,
    )
    repository.add_error(
        parse_error.id,
        "chat.md",
        "invalid markdown",
        error_type="parse_error",
        retryable=True,
    )

    migration_path = SERVER_DIR / "alembic" / "versions" / "015_repair_import_source_retry_flags.py"
    spec = importlib.util.spec_from_file_location("repair_import_source_retry_flags", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        connection.execute(text(migration.SOURCE_RETRY_REPAIR_SQL))
        connection.execute(text(migration.SOURCE_RETRY_REPAIR_SQL))

    assert repository.get_job(graph_only.id).source_retry_required is False
    assert repository.get_job(cancelled.id).source_retry_required is True
    assert repository.get_job(parse_error.id).source_retry_required is True


def test_job_and_chunk_state_survives_new_repository_instance(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    first, second = repositories
    job, chunk = _job_and_chunk(first)

    first.increment_job(job.id, processed_chunks=1, retry_count=2, memories_created=3)
    first.update_job(
        job.id,
        status="importing",
        phase="embedding",
        phase_durations={"parse": 0.2, "llm": 1.4},
        graph_status="pending",
    )
    first.update_chunk(
        job.id,
        chunk.import_key,
        status="succeeded",
        attempt=2,
        retry_count=1,
        memory_ids=["memory-1"],
    )

    restored = second.get_job(job.id, "project-1")
    restored_chunk = second.get_chunk(job.id, chunk.import_key)
    assert restored.status == "importing"
    assert restored.phase == "embedding"
    assert restored.processed_chunks == 1
    assert restored.retry_count == 2
    assert restored.current_concurrency == 3
    assert restored.memories_created == 3
    assert restored.phase_durations["llm"] == 1.4
    assert restored_chunk.status == "succeeded"
    assert restored_chunk.timings["parse"] == 0.2
    assert restored_chunk.source_message_indices == list(range(8))
    assert restored_chunk.core_source_message_indices == [2, 3, 4, 5]
    assert restored_chunk.parent_import_key == "parent-import-key"
    assert restored_chunk.duration_seconds == 1.6
    assert restored_chunk.model_used == "fast-model"
    assert restored_chunk.fallback_reason == "schema_validation"

    # A recovery scan may upsert planned chunks again; terminal work must not regress.
    same = second.upsert_chunk(
        job_id=job.id,
        project_id="project-1",
        import_key=chunk.import_key,
        conversation_id="conversation-1",
        status="pending",
    )
    assert same.id == chunk.id
    assert same.status == "succeeded"
    assert same.memory_ids == ["memory-1"]


def test_split_chunk_state_is_protected_from_recovery_upsert(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    job, chunk = _job_and_chunk(repo)
    finished_at = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)
    repo.update_chunk(
        job.id,
        chunk.import_key,
        status="split",
        finished_at=finished_at,
        error_type="adaptive_split",
        error_message="output limit",
    )

    restored = repo.upsert_chunk(
        job_id=job.id,
        project_id=job.project_id,
        import_key=chunk.import_key,
        conversation_id=chunk.conversation_id,
        status="pending",
        finished_at=None,
        error_type=None,
        error_message=None,
    )

    assert restored.status == "split"
    assert restored.finished_at.replace(tzinfo=timezone.utc) == finished_at
    assert restored.error_type == "adaptive_split"
    assert restored.error_message == "output limit"


def test_recoverable_jobs_scan_crosses_projects_and_excludes_terminal_jobs(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    first = repo.create_job(project_id="project-a", input_files=[], entities={}, options={})
    second = repo.create_job(
        project_id="project-b",
        input_files=[],
        entities={},
        options={},
        status="importing",
    )
    terminal = repo.create_job(
        project_id="project-c",
        input_files=[],
        entities={},
        options={},
        status="completed",
    )

    assert [job.id for job in repo.list_recoverable_jobs()] == [first.id, second.id]
    assert [job.id for job in repo.list_recoverable_jobs(["completed"])] == [terminal.id]
    assert repo.list_recoverable_jobs([]) == []


def test_job_lease_is_atomic_and_stale_owners_can_be_replaced(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    job = repositories[0].create_job(project_id="project-a", input_files=[], entities={}, options={})
    barrier = Barrier(2)
    acquired_at = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)

    def acquire(candidate):
        barrier.wait()
        owner = f"worker-{repositories.index(candidate)}"
        return owner, candidate.acquire_job_lease(
            job.id,
            owner,
            lease_seconds=30,
            now=acquired_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(acquire, repositories))

    assert sorted(acquired for _, acquired in claims) == [False, True]
    winner = next(owner for owner, acquired in claims if acquired)
    loser = next(owner for owner, acquired in claims if not acquired)
    assert repositories[0].list_recoverable_jobs(available_at=acquired_at + timedelta(seconds=1)) == []
    assert not repositories[1].renew_job_lease(
        job.id,
        loser,
        lease_seconds=30,
        now=acquired_at + timedelta(seconds=5),
    )
    assert not repositories[1].release_job_lease(job.id, loser)
    assert repositories[0].renew_job_lease(
        job.id,
        winner,
        lease_seconds=30,
        now=acquired_at + timedelta(seconds=5),
    )

    replacement = "replacement-worker"
    stale_at = acquired_at + timedelta(seconds=36)
    assert repositories[1].acquire_job_lease(job.id, replacement, lease_seconds=30, now=stale_at)
    assert not repositories[0].renew_job_lease(job.id, winner, lease_seconds=30, now=stale_at)
    assert not repositories[0].release_job_lease(job.id, winner)
    assert repositories[1].release_job_lease(job.id, replacement)
    assert [record.id for record in repositories[0].list_recoverable_jobs(available_at=stale_at)] == [job.id]


def test_stale_lease_owner_cannot_mutate_persisted_import_state(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    stale, current = repositories
    job, chunk = _job_and_chunk(stale)
    owner_a = "worker-a"
    owner_b = "worker-b"
    acquired_at = datetime.now(timezone.utc)
    assert stale.acquire_job_lease(job.id, owner_a, lease_seconds=30, now=acquired_at)

    stale.update_job(job.id, lease_owner=owner_a, status="importing", phase="extracting")
    stale.update_chunk(job.id, chunk.import_key, lease_owner=owner_a, status="processing")
    claimed, manifest = stale.claim_manifest(
        job.project_id,
        chunk.import_key,
        job.id,
        chunk.id,
        lease_owner=owner_a,
    )
    assert claimed
    assert stale.claim_memory_hash(
        job.project_id,
        chunk.conversation_id,
        "hash-1",
        job.id,
        chunk.id,
        lease_owner=owner_a,
    )
    graph_item = stale.add_graph_items(
        job.id,
        chunk.id,
        [{"memory_id": "memory-1", "text": "tea", "metadata": {}}],
        lease_owner=owner_a,
    )[0]

    takeover_at = acquired_at + timedelta(seconds=31)
    assert current.acquire_job_lease(job.id, owner_b, lease_seconds=30, now=takeover_at)

    stale_mutations = [
        lambda: stale.update_job(job.id, lease_owner=owner_a, status="failed"),
        lambda: stale.increment_job(job.id, lease_owner=owner_a, processed_chunks=1),
        lambda: stale.add_error(job.id, "stale", "must not persist", lease_owner=owner_a),
        lambda: stale.upsert_chunk(
            lease_owner=owner_a,
            job_id=job.id,
            project_id=job.project_id,
            import_key="stale-chunk",
            conversation_id="conversation-2",
        ),
        lambda: stale.update_chunk(
            job.id,
            chunk.import_key,
            lease_owner=owner_a,
            status="succeeded",
        ),
        lambda: stale.claim_manifest(
            job.project_id,
            "stale-manifest",
            job.id,
            chunk.id,
            lease_owner=owner_a,
        ),
        lambda: stale.mark_manifest(
            job.project_id,
            chunk.import_key,
            "succeeded",
            memory_ids=["stale-memory"],
            job_id=job.id,
            lease_owner=owner_a,
        ),
        lambda: stale.claim_memory_hash(
            job.project_id,
            chunk.conversation_id,
            "stale-hash",
            job.id,
            chunk.id,
            lease_owner=owner_a,
        ),
        lambda: stale.release_memory_hashes(
            job.id,
            chunk.id,
            ["hash-1"],
            lease_owner=owner_a,
        ),
        lambda: stale.mark_memory_hashes_succeeded(
            job.project_id,
            chunk.conversation_id,
            ["hash-1"],
            job_id=job.id,
            chunk_id=chunk.id,
            memory_ids={"hash-1": "stale-memory"},
            lease_owner=owner_a,
        ),
        lambda: stale.add_graph_items(
            job.id,
            chunk.id,
            [{"memory_id": "stale-memory", "text": "stale", "metadata": {}}],
            lease_owner=owner_a,
        ),
        lambda: stale.mark_graph_items(
            graph_item.id,
            "synced",
            job_id=job.id,
            lease_owner=owner_a,
        ),
    ]
    for mutation in stale_mutations:
        with pytest.raises(ImportLeaseLost):
            mutation()

    persisted_job = current.get_job(job.id)
    persisted_chunk = current.get_chunk(job.id, chunk.import_key)
    persisted_manifests = current.load_manifests(
        job.project_id,
        [chunk.import_key, "stale-manifest"],
    )
    graph_items = current.list_graph_items(job.id, status=None)
    with current._session_factory() as session:
        hashes = session.scalars(select(MemoryImportHash)).all()

    assert persisted_job.lease_owner == owner_b
    assert persisted_job.status == "importing"
    assert persisted_job.processed_chunks == 0
    assert persisted_job.error_count == 0
    assert persisted_job.graph_pending_items == 1
    assert persisted_chunk.status == "processing"
    assert current.get_chunk(job.id, "stale-chunk") is None
    assert set(persisted_manifests) == {chunk.import_key}
    assert persisted_manifests[chunk.import_key].id == manifest.id
    assert persisted_manifests[chunk.import_key].status == "claimed"
    assert len(hashes) == 1
    assert hashes[0].memory_hash == "hash-1"
    assert hashes[0].status == "claimed"
    assert hashes[0].memory_id is None
    assert current.list_errors(job.id) == []
    assert [item.id for item in graph_items] == [graph_item.id]
    assert graph_items[0].status == "pending"


def test_fenced_mutation_rejects_an_expired_matching_lease(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    job = repo.create_job(project_id="project-a", input_files=[], entities={}, options={})
    owner = "expired-owner"
    assert repo.acquire_job_lease(
        job.id,
        owner,
        lease_seconds=1,
        now=datetime.now(timezone.utc) - timedelta(seconds=2),
    )

    assert not repo.renew_job_lease(job.id, owner, lease_seconds=30)
    with pytest.raises(ImportLeaseLost):
        repo.assert_job_lease(job.id, owner)
    with pytest.raises(ImportLeaseLost):
        repo.update_job(job.id, lease_owner=owner, status="importing")

    persisted = repo.get_job(job.id)
    assert persisted.lease_owner == owner
    assert persisted.status == "queued"


def test_graph_retry_atomically_acquires_active_slot_and_execution_lease(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    activated_at = datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="completed_with_errors",
        graph_status="failed",
    )
    barrier = Barrier(2)

    def activate(index):
        barrier.wait()
        owner = f"graph-worker-{index}"
        row = repositories[index].activate_graph_retry(
            job.id,
            job.project_id,
            owner,
            lease_seconds=30,
            max_active_jobs=1,
            now=activated_at,
        )
        return owner, row

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, range(2)))

    assert sum(row is not None for _, row in results) == 1
    winner, activated = next((owner, row) for owner, row in results if row is not None)
    assert activated.status == "syncing_graph"
    assert activated.phase == "graph_sync"
    assert activated.graph_status == "syncing"
    assert activated.lease_owner == winner
    lease_expires_at = activated.lease_expires_at
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    assert lease_expires_at == activated_at + timedelta(seconds=30)
    assert repositories[0].count_active_jobs(job.project_id) == 1
    repositories[1].assert_job_lease(job.id, winner, now=activated_at + timedelta(seconds=1))


def test_graph_retry_and_full_retry_have_a_single_atomic_winner(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    activated_at = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="completed_with_errors",
        graph_status="failed",
    )
    barrier = Barrier(2)

    def graph_retry():
        barrier.wait()
        return repositories[0].activate_graph_retry(
            job.id,
            job.project_id,
            "graph-owner",
            lease_seconds=30,
            max_active_jobs=2,
            now=activated_at,
        )

    def full_retry():
        barrier.wait()
        return repositories[1].acquire_job_retry_lease(
            job.id,
            job.project_id,
            "full-owner",
            lease_seconds=30,
            max_active_jobs=2,
            now=activated_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        graph_future = executor.submit(graph_retry)
        full_future = executor.submit(full_retry)
        graph_result = graph_future.result()
        full_result = full_future.result()

    assert sum(result is not None for result in (graph_result, full_result)) == 1
    persisted = repositories[0].get_job(job.id)
    if graph_result is not None:
        assert persisted.status == "syncing_graph"
        assert persisted.lease_owner == "graph-owner"
    else:
        assert persisted.status == "queued"
        assert persisted.lease_owner == "full-owner"


def test_retry_lease_atomically_resets_terminal_job_across_repositories(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    acquired_at = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="failed",
        phase="failed",
        cancel_requested=True,
        finished_at=acquired_at - timedelta(minutes=1),
        graph_error="graph unavailable",
        active_workers=2,
        discovered_files=3,
        parsed_files=2,
        skipped_files=1,
        total_conversations=4,
        total_chunks=5,
        total_tokens=6000,
        current_file="stale.md",
        current_conversation="stale conversation",
    )
    barrier = Barrier(2)

    def retry(candidate):
        barrier.wait()
        owner = f"retry-worker-{repositories.index(candidate)}"
        return owner, candidate.acquire_job_retry_lease(
            job.id,
            "project-a",
            owner,
            lease_seconds=30,
            now=acquired_at,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(retry, repositories))

    assert sorted(row is not None for _, row in claims) == [False, True]
    owner, retried = next((owner, row) for owner, row in claims if row is not None)
    assert retried.status == "queued"
    assert retried.phase == "queued"
    assert retried.cancel_requested is False
    assert retried.finished_at is None
    assert retried.graph_error is None
    assert retried.active_workers == 0
    assert retried.discovered_files == 0
    assert retried.parsed_files == 0
    assert retried.skipped_files == 0
    assert retried.total_conversations == 0
    assert retried.total_chunks == 0
    assert retried.total_tokens == 0
    assert retried.current_file is None
    assert retried.current_conversation is None
    assert retried.lease_owner == owner
    assert retried.lease_expires_at is not None


def test_retry_lease_guards_project_and_status_while_replacing_terminal_owner(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    now = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)
    active = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="failed",
        lease_owner="active-worker",
        lease_expires_at=now + timedelta(seconds=30),
    )
    stale = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="completed_with_errors",
        lease_owner="stale-worker",
        lease_expires_at=now - timedelta(seconds=1),
    )

    replaced_active = repositories[1].acquire_job_retry_lease(
        active.id,
        "project-a",
        "replacement-live",
        lease_seconds=30,
        now=now,
    )
    assert replaced_active is not None
    assert replaced_active.status == "queued"
    assert replaced_active.lease_owner == "replacement-live"
    assert not repositories[0].renew_job_lease(
        active.id,
        "active-worker",
        lease_seconds=30,
        now=now,
    )
    assert not repositories[0].release_job_lease(active.id, "active-worker")
    assert (
        repositories[1].acquire_job_retry_lease(
            stale.id,
            "wrong-project",
            "replacement",
            lease_seconds=30,
            now=now,
        )
        is None
    )
    retried = repositories[1].acquire_job_retry_lease(
        stale.id,
        "project-a",
        "replacement",
        lease_seconds=30,
        now=now,
    )
    assert retried is not None
    assert retried.status == "queued"
    assert retried.lease_owner == "replacement"
    assert (
        repositories[0].acquire_job_retry_lease(
            stale.id,
            "project-a",
            "another-worker",
            lease_seconds=30,
            now=now,
        )
        is None
    )


def test_workspace_discard_claim_clears_accounting_only_when_completed(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    started_at = datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=["chat.md"],
        entities={"user_id": "me"},
        options={},
        status="completed_with_errors",
        phase="completed",
        workspace="/tmp/retained-import",
        workspace_bytes=123,
        source_retry_required=True,
    )

    first = repositories[0].acquire_job_workspace_discard_lease(
        job.id,
        job.project_id,
        "discard-a",
        lease_seconds=1,
        now=started_at,
    )

    assert first is not None
    assert first.status == "completed_with_errors"
    assert first.phase == "discarding"
    assert first.workspace == "/tmp/retained-import"
    assert first.workspace_bytes == 123
    assert first.source_retry_required is True
    assert (
        repositories[1].acquire_job_retry_lease(
            job.id,
            job.project_id,
            "retry-during-discard",
            lease_seconds=30,
            now=started_at,
        )
        is None
    )
    assert (
        repositories[1].activate_graph_retry(
            job.id,
            job.project_id,
            "graph-retry-during-discard",
            lease_seconds=30,
            max_active_jobs=2,
            now=started_at,
        )
        is None
    )

    reclaimed = repositories[1].acquire_job_workspace_discard_lease(
        job.id,
        job.project_id,
        "discard-b",
        lease_seconds=30,
        now=started_at + timedelta(seconds=2),
    )
    assert reclaimed is not None
    assert reclaimed.lease_owner == "discard-b"
    with pytest.raises(ImportLeaseLost):
        repositories[0].complete_job_workspace_discard(job.id, "discard-a")

    completed = repositories[1].complete_job_workspace_discard(job.id, "discard-b")

    assert completed.status == "completed_with_errors"
    assert completed.phase == "completed"
    assert completed.workspace is None
    assert completed.workspace_bytes == 0
    assert completed.source_retry_required is False
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None


def test_workspace_discard_and_full_retry_have_single_winner(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    now = datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=["chat.md"],
        entities={"user_id": "me"},
        options={},
        status="failed",
        phase="failed",
        workspace="/tmp/raced-import",
        workspace_bytes=42,
        source_retry_required=True,
    )
    barrier = Barrier(2)

    def discard():
        barrier.wait()
        return repositories[0].acquire_job_workspace_discard_lease(
            job.id,
            job.project_id,
            "discard-owner",
            lease_seconds=30,
            now=now,
        )

    def retry():
        barrier.wait()
        return repositories[1].acquire_job_retry_lease(
            job.id,
            job.project_id,
            "retry-owner",
            lease_seconds=30,
            max_active_jobs=2,
            now=now,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        discard_future = executor.submit(discard)
        retry_future = executor.submit(retry)
        discard_result = discard_future.result()
        retry_result = retry_future.result()

    assert sum(result is not None for result in (discard_result, retry_result)) == 1
    persisted = repositories[0].get_job(job.id)
    if discard_result is not None:
        assert persisted.status == "failed"
        assert persisted.phase == "discarding"
        assert persisted.lease_owner == "discard-owner"
    else:
        assert persisted.status == "queued"
        assert persisted.phase == "queued"
        assert persisted.lease_owner == "retry-owner"


def test_cancel_request_atomically_transitions_active_job(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    job = repositories[0].create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="importing",
        phase="extracting",
    )
    barrier = Barrier(2)

    def cancel(candidate):
        barrier.wait()
        return candidate.request_job_cancel(job.id, "project-a")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(cancel, repositories))

    assert sorted(result is not None for result in results) == [False, True]
    cancelled = next(result for result in results if result is not None)
    assert cancelled.cancel_requested is True
    assert cancelled.status == "cancelling"
    assert cancelled.phase == "cancelling"


def test_cancel_request_guards_project_status_and_missing_job(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    completed = repo.create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="completed",
    )
    active = repo.create_job(
        project_id="project-a",
        input_files=[],
        entities={},
        options={},
        status="queued",
    )

    assert repo.request_job_cancel(completed.id, "project-a") is None
    assert repo.request_job_cancel(active.id, "wrong-project") is None
    assert repo.request_job_cancel("missing-job", "project-a") is None
    assert repo.request_job_cancel(active.id, "project-a", allowed_statuses=()) is None
    assert repo.get_job(active.id).status == "queued"


def test_manifest_claim_uses_database_unique_constraint_across_repositories(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    job, chunk = _job_and_chunk(repositories[0])
    barrier = Barrier(2)

    def claim(repo):
        barrier.wait()
        return repo.claim_manifest("project-1", "same-key", job.id, chunk.id)[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, repositories))

    assert sorted(claims) == [False, True]
    manifests = repositories[0].load_manifests("project-1", ["same-key", "missing"])
    assert set(manifests) == {"same-key"}
    assert manifests["same-key"].status == "claimed"

    repositories[0].mark_manifest("project-1", "same-key", "released", last_error="restart recovery")
    reclaimed, row = repositories[1].claim_manifest("project-1", "same-key", job.id, chunk.id)
    assert reclaimed is True
    assert row.attempts == 2

    completed = repositories[0].mark_manifest("project-1", "same-key", "succeeded", memory_ids=["memory-1"])
    assert completed.memory_ids == ["memory-1"]
    assert repositories[1].claim_manifest("project-1", "same-key", job.id, chunk.id)[0] is False


def test_persisted_chunk_statuses_include_nested_project_splits(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    prior = repo.create_job(
        id="prior-split-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
        status="completed",
    )
    current = repo.create_job(
        id="current-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    for index, import_key in enumerate(("root-key", "nested-key")):
        chunk = repo.upsert_chunk(
            job_id=prior.id,
            project_id=prior.project_id,
            import_key=import_key,
            conversation_id="conversation-1",
            status="split",
        )
        claimed, _ = repo.claim_manifest(prior.project_id, import_key, prior.id, chunk.id)
        assert claimed is True
        repo.mark_manifest(
            prior.project_id,
            import_key,
            "split" if index == 0 else "released",
        )
    repo.upsert_chunk(
        job_id=current.id,
        project_id=current.project_id,
        import_key="current-leaf",
        conversation_id="conversation-1",
        status="failed",
    )

    assert repo.load_persisted_chunk_statuses("project-1", current.id) == {
        "current-leaf": "failed",
        "nested-key": "split",
        "root-key": "split",
    }


def test_current_chunk_status_overrides_historical_split(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    prior = repo.create_job(
        id="prior-split-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
        status="completed_with_errors",
    )
    current = repo.create_job(
        id="current-recovery-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    for job, status in ((prior, "split"), (current, "failed")):
        chunk = repo.upsert_chunk(
            job_id=job.id,
            project_id=job.project_id,
            import_key="shared-parent-key",
            conversation_id="conversation-1",
            status=status,
        )
        if job.id == prior.id:
            claimed, _ = repo.claim_manifest(job.project_id, chunk.import_key, job.id, chunk.id)
            assert claimed is True
            repo.mark_manifest(job.project_id, chunk.import_key, "split")

    assert repo.load_persisted_chunk_statuses("project-1", current.id)["shared-parent-key"] == "failed"


def test_succeeded_manifest_marks_current_split_as_superseded(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    prior = repo.create_job(
        id="prior-split-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
        status="completed",
    )
    later = repo.create_job(
        id="later-success-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
        status="completed",
    )
    current = repo.create_job(
        id="future-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    repo.upsert_chunk(
        job_id=prior.id,
        project_id=prior.project_id,
        import_key="shared-parent-key",
        conversation_id="conversation-1",
        status="split",
    )
    later_chunk = repo.upsert_chunk(
        job_id=later.id,
        project_id=later.project_id,
        import_key="shared-parent-key",
        conversation_id="conversation-1",
        status="succeeded",
    )
    claimed, _ = repo.claim_manifest(
        later.project_id,
        later_chunk.import_key,
        later.id,
        later_chunk.id,
    )
    assert claimed is True
    repo.mark_manifest(
        later.project_id,
        later_chunk.import_key,
        "succeeded",
        memory_ids=["memory-1"],
    )
    repo.upsert_chunk(
        job_id=current.id,
        project_id=current.project_id,
        import_key="shared-parent-key",
        conversation_id="conversation-1",
        status="split",
    )

    assert repo.load_persisted_chunk_statuses("project-1", current.id)["shared-parent-key"] == ("superseded_split")


def test_split_manifest_cannot_be_reclaimed_across_repositories(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    owner, contender = repositories
    first_job = owner.create_job(
        id="split-owner-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    second_job = contender.create_job(
        id="split-contender-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    first_chunk = owner.upsert_chunk(
        job_id=first_job.id,
        project_id=first_job.project_id,
        import_key="shared-root",
        conversation_id="conversation-1",
        status="processing",
    )
    second_chunk = contender.upsert_chunk(
        job_id=second_job.id,
        project_id=second_job.project_id,
        import_key="shared-root",
        conversation_id="conversation-1",
        status="pending",
    )
    claimed, _ = owner.claim_manifest(
        first_job.project_id,
        first_chunk.import_key,
        first_job.id,
        first_chunk.id,
    )
    assert claimed is True

    barrier = Barrier(2)

    def transition_to_split():
        barrier.wait()
        owner.mark_manifest(first_job.project_id, first_chunk.import_key, "split")
        owner.update_chunk(first_job.id, first_chunk.import_key, status="split")

    def race_claim():
        barrier.wait()
        return contender.claim_manifest(
            second_job.project_id,
            second_chunk.import_key,
            second_job.id,
            second_chunk.id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        transition = executor.submit(transition_to_split)
        claim = executor.submit(race_claim)
        transition.result()
        reclaimed, raced_manifest = claim.result()

    assert reclaimed is False
    assert raced_manifest.status in {"claimed", "split"}
    reclaimed, manifest = contender.claim_manifest(
        second_job.project_id,
        second_chunk.import_key,
        second_job.id,
        second_chunk.id,
    )
    assert reclaimed is False
    assert manifest.status == "split"
    assert manifest.job_id == first_job.id
    assert manifest.chunk_id == first_chunk.id


def test_legacy_released_split_manifest_is_atomically_upgraded(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    legacy, contender = repositories
    prior = legacy.create_job(
        id="legacy-split-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    current = contender.create_job(
        id="current-job",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    prior_chunk = legacy.upsert_chunk(
        job_id=prior.id,
        project_id=prior.project_id,
        import_key="legacy-root",
        conversation_id="conversation-1",
        status="split",
    )
    current_chunk = contender.upsert_chunk(
        job_id=current.id,
        project_id=current.project_id,
        import_key="legacy-root",
        conversation_id="conversation-1",
        status="pending",
    )
    claimed, _ = legacy.claim_manifest(
        prior.project_id,
        prior_chunk.import_key,
        prior.id,
        prior_chunk.id,
    )
    assert claimed is True
    legacy.mark_manifest(prior.project_id, prior_chunk.import_key, "released")

    reclaimed, manifest = contender.claim_manifest(
        current.project_id,
        current_chunk.import_key,
        current.id,
        current_chunk.id,
    )

    assert reclaimed is False
    assert manifest.status == "split"
    assert manifest.job_id == prior.id
    assert manifest.chunk_id == prior_chunk.id
    assert manifest.attempts == 1


@pytest.mark.parametrize("manifest_status", ["claimed", "failed", "retryable"])
def test_historical_split_loader_ignores_unconfirmed_manifest_states(tmp_path, manifest_status):
    _, (repo,) = _repositories(tmp_path)
    prior = repo.create_job(
        id="unconfirmed-prior",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    current = repo.create_job(
        id="unconfirmed-current",
        project_id="project-1",
        input_files=[],
        entities={},
        options={},
    )
    chunk = repo.upsert_chunk(
        job_id=prior.id,
        project_id=prior.project_id,
        import_key="unconfirmed-root",
        conversation_id="conversation-1",
        status="split",
    )
    claimed, _ = repo.claim_manifest(prior.project_id, chunk.import_key, prior.id, chunk.id)
    assert claimed is True
    if manifest_status != "claimed":
        repo.mark_manifest(prior.project_id, chunk.import_key, manifest_status)

    assert "unconfirmed-root" not in repo.load_persisted_chunk_statuses(
        project_id="project-1", current_job_id=current.id
    )


def test_memory_hash_claim_release_and_success_are_idempotent(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    repo = repositories[0]
    job, chunk = _job_and_chunk(repo)
    barrier = Barrier(2)

    def claim(candidate):
        barrier.wait()
        return candidate.claim_memory_hash("project-1", "conversation-1", "hash-1", job.id, chunk.id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, repositories))
    assert sorted(claims) == [False, True]
    assert repo.release_memory_hashes(job.id, chunk.id, ["hash-1"]) == 1
    assert repo.claim_memory_hash("project-1", "conversation-1", "hash-1", job.id, chunk.id)
    assert (
        repo.mark_memory_hashes_succeeded(
            "project-1",
            "conversation-1",
            ["hash-1"],
            job.id,
            chunk.id,
            {"hash-1": "memory-1"},
        )
        == 1
    )
    assert not repo.claim_memory_hash("project-1", "conversation-1", "hash-1", job.id, chunk.id)
    assert repo.release_memory_hashes(job.id, chunk.id, ["hash-1"]) == 0


def test_memory_hash_recovery_releases_unpersisted_chunk_claims(tmp_path):
    _, repositories = _repositories(tmp_path, count=2)
    first, second = repositories
    job, chunk = _job_and_chunk(first)

    # A process can exit after claiming a hash but before the successful chunk update
    # persists claimed_memory_hashes on the chunk row.
    assert first.claim_memory_hash("project-1", "conversation-1", "hash-1", job.id, chunk.id)
    assert first.get_chunk(job.id, chunk.import_key).claimed_memory_hashes == []

    assert second.release_memory_hashes(job.id, chunk.id) == 1
    assert second.claim_memory_hash("project-1", "conversation-1", "hash-1", job.id, chunk.id)


def test_graph_payload_errors_and_retry_state_are_persisted(tmp_path):
    _, (repo,) = _repositories(tmp_path)
    job, chunk = _job_and_chunk(repo)
    graph_rows = repo.add_graph_items(
        job.id,
        chunk.id,
        [
            {
                "memory_id": "memory-1",
                "text": "The user prefers tea.",
                "entities": {"user_id": "user-1"},
                "metadata": {"source_message_indices": [1, 2]},
            }
        ],
    )
    repeated = repo.add_graph_items(
        job.id,
        chunk.id,
        [{"memory_id": "memory-1", "text": "ignored duplicate", "entities": {}, "metadata": {}}],
    )
    assert repeated[0].id == graph_rows[0].id
    assert len(repo.list_graph_items(job.id)) == 1

    assert repo.mark_graph_failed(graph_rows[0].id, "neo4j unavailable") == 1
    failed = repo.list_graph_items(job.id, status="failed")[0]
    assert failed.attempts == 1
    assert failed.last_error == "neo4j unavailable"
    assert failed.payload == {
        "memory_id": "memory-1",
        "text": "The user prefers tea.",
        "entities": {"user_id": "user-1"},
        "metadata": {"source_message_indices": [1, 2]},
    }
    assert repo.mark_graph_items(failed.id, "pending") == 1
    assert repo.mark_graph_synced(failed.id) == 1
    assert repo.get_job(job.id).graph_status == "synced"

    long_message = "database failure: " + ("x" * 5000)
    repo.add_error(
        job.id,
        "history/chat.md",
        long_message,
        chunk_id=chunk.id,
        phase="pgvector",
        retryable=True,
        details={"status_code": 503},
    )
    errors = repo.list_errors(job.id, limit=10, offset=0)
    assert errors[0].message == long_message
    assert errors[0].details == {"status_code": 503}
    assert repo.get_job(job.id).error_count == 1
