from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from models import (
    MemoryImportChunk,
    MemoryImportError,
    MemoryImportGraphItem,
    MemoryImportHash,
    MemoryImportJob,
    MemoryImportManifest,
)
from sqlalchemy import bindparam, delete, func, insert, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

_TERMINAL_CHUNK_STATUSES = {"completed", "imported", "succeeded", "split"}
_SUCCEEDED_MANIFEST_STATUSES = {"completed", "imported", "succeeded"}
_RECLAIMABLE_STATUSES = {"failed", "released", "retryable"}
_DEFAULT_RECOVERABLE_JOB_STATUSES = (
    "uploading",
    "queued",
    "discovering",
    "parsing",
    "importing",
    "syncing_graph",
    "cancelling",
)
_LEASEABLE_JOB_STATUSES = tuple(status for status in _DEFAULT_RECOVERABLE_JOB_STATUSES if status != "cancelling")
_ACTIVE_JOB_STATUSES = _DEFAULT_RECOVERABLE_JOB_STATUSES
_DISCARDABLE_JOB_STATUSES = ("cancelled", "completed", "completed_with_errors", "failed")
_WORKSPACE_DISCARD_PHASE = "discarding"
_RESOURCE_TRANSACTION_LOCK = threading.RLock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _as_json_value(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return value


def _coerce_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return value


def _column_values(model: type, values: Mapping[str, Any], *, exclude: set[str] | None = None) -> dict[str, Any]:
    columns = {column.key: column for column in model.__table__.columns}
    excluded = exclude or set()
    unknown = set(values) - set(columns)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise TypeError(f"Unknown {model.__name__} field(s): {names}")

    normalized: dict[str, Any] = {}
    for key, value in values.items():
        if key in excluded:
            continue
        if isinstance(columns[key].type, type(MemoryImportJob.created_at.type)):
            value = _coerce_datetime(value)
        elif key in {
            "input_files",
            "entities",
            "options",
            "phase_durations",
            "timings",
            "source_message_indices",
            "core_source_message_indices",
            "audit_metadata",
            "claimed_memory_hashes",
            "memory_ids",
            "details",
            "payload",
        }:
            value = _as_json_value(value)
        normalized[key] = value
    return normalized


class ImportLeaseLost(RuntimeError):
    """Raised when a fenced import mutation no longer owns an active lease."""

    def __init__(self, job_id: str | None, lease_owner: str):
        target = f" for job {job_id}" if job_id else ""
        super().__init__(f"Memory import lease lost{target}.")
        self.job_id = job_id
        self.lease_owner = lease_owner


class ImportActiveJobLimitExceeded(RuntimeError):
    def __init__(self, project_id: str, limit: int, active_jobs: int):
        super().__init__(f"Project {project_id} already has {active_jobs} active memory imports (limit {limit}).")
        self.project_id = project_id
        self.limit = limit
        self.active_jobs = active_jobs


class ImportWorkspaceBudgetExceeded(RuntimeError):
    def __init__(self, limit_bytes: int, used_bytes: int, requested_bytes: int):
        super().__init__(
            f"Memory-import workspaces use {used_bytes} bytes; reserving {requested_bytes} more "
            f"would exceed the {limit_bytes}-byte limit."
        )
        self.limit_bytes = limit_bytes
        self.used_bytes = used_bytes
        self.requested_bytes = requested_bytes


class ImportRepository:
    """Short-transaction persistence API for resumable memory imports.

    The factory must return a fresh SQLAlchemy ``Session``. Database uniqueness
    constraints are the final arbiter for claims, so separate repository
    instances and separate processes remain idempotent.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory
        self._lock = threading.RLock()

    @staticmethod
    def _detach(session: Session, row: Any) -> Any:
        session.refresh(row)
        session.expunge(row)
        return row

    @staticmethod
    def _detach_all(session: Session, rows: Iterable[Any]) -> list[Any]:
        detached = list(rows)
        for row in detached:
            session.expunge(row)
        return detached

    @staticmethod
    def _rollback_close(session: Session) -> None:
        try:
            session.rollback()
        finally:
            session.close()

    @staticmethod
    def _normalize_lease_owner(lease_owner: str | None) -> str | None:
        if lease_owner is None:
            return None
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner must be non-empty when provided")
        return owner

    @staticmethod
    def _active_lease_conditions(
        job_id: Any,
        lease_owner: str,
        now: datetime,
    ) -> tuple[Any, ...]:
        return (
            MemoryImportJob.id == job_id,
            MemoryImportJob.lease_owner == lease_owner,
            MemoryImportJob.lease_expires_at.is_not(None),
            MemoryImportJob.lease_expires_at > now,
        )

    @classmethod
    def _active_lease_exists(
        cls,
        job_id: Any,
        lease_owner: str,
        now: datetime,
    ) -> Any:
        return (
            select(MemoryImportJob.id)
            .where(*cls._active_lease_conditions(job_id, lease_owner, now))
            .correlate_except(MemoryImportJob)
            .exists()
        )

    @classmethod
    def _assert_active_lease(
        cls,
        session: Session,
        job_id: str,
        lease_owner: str,
        now: datetime,
    ) -> None:
        active = session.scalar(
            select(MemoryImportJob.id).where(*cls._active_lease_conditions(job_id, lease_owner, now))
        )
        if active is None:
            raise ImportLeaseLost(job_id, lease_owner)

    def assert_job_lease(
        self,
        job_id: str,
        lease_owner: str,
        *,
        now: datetime | str | None = None,
    ) -> None:
        """Raise when ``lease_owner`` no longer owns an unexpired job lease."""

        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        checked_at = _coerce_datetime(now) or _utcnow()
        with self._lock:
            session = self._session_factory()
            try:
                self._assert_active_lease(session, job_id, owner, checked_at)
            finally:
                session.close()

    @classmethod
    def _insert_if_active_lease(
        cls,
        session: Session,
        model: type,
        values: Mapping[str, Any],
        job_id: str,
        lease_owner: str,
        now: datetime,
    ) -> None:
        columns = model.__table__.columns
        names = list(values)
        source = select(
            *(
                bindparam(
                    f"_fenced_{model.__tablename__}_{name}",
                    value=values[name],
                    type_=columns[name].type,
                    unique=True,
                )
                for name in names
            )
        ).where(cls._active_lease_exists(job_id, lease_owner, now))
        result = session.execute(insert(model).from_select(names, source))
        if result.rowcount == 0:
            raise ImportLeaseLost(job_id, lease_owner)

    @staticmethod
    def _serialize_resource_transaction(session: Session, resource: str) -> None:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:resource))"),
                {"resource": f"mem0:{resource}"},
            )
        elif dialect == "sqlite" and not session.info.get("memory_import_resource_transaction"):
            session.execute(text("BEGIN IMMEDIATE"))
            session.info["memory_import_resource_transaction"] = True

    @staticmethod
    def _prepare_job_values(values: Mapping[str, Any]) -> dict[str, Any]:
        now = _utcnow()
        prepared = dict(values)
        prepared.setdefault("id", _new_id())
        prepared.setdefault("status", "queued")
        prepared.setdefault("phase", prepared["status"])
        prepared.setdefault("created_at", now)
        prepared.setdefault("updated_at", now)
        if "input_files" in prepared:
            prepared["input_files"] = [str(path) for path in prepared["input_files"]]
            prepared.setdefault("total_input_files", len(prepared["input_files"]))
        return prepared

    def create_job(self, **values: Any) -> MemoryImportJob:
        values = self._prepare_job_values(values)
        row = MemoryImportJob(**_column_values(MemoryImportJob, values))
        with self._lock:
            session = self._session_factory()
            try:
                session.add(row)
                session.commit()
                return self._detach(session, row)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def create_job_with_active_limit(
        self,
        max_active_jobs: int,
        *,
        max_retained_bytes: int | None = None,
        **values: Any,
    ) -> MemoryImportJob:
        limit = int(max_active_jobs)
        if limit <= 0:
            raise ValueError("max_active_jobs must be positive")
        values = self._prepare_job_values(values)
        project_id = str(values.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("project_id is required")
        row = MemoryImportJob(**_column_values(MemoryImportJob, values))
        with _RESOURCE_TRANSACTION_LOCK:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"active-imports:{project_id}")
                active_jobs = int(
                    session.scalar(
                        select(func.count(MemoryImportJob.id)).where(
                            MemoryImportJob.project_id == project_id,
                            MemoryImportJob.status.in_(_ACTIVE_JOB_STATUSES),
                        )
                    )
                    or 0
                )
                if active_jobs >= limit:
                    raise ImportActiveJobLimitExceeded(project_id, limit, active_jobs)
                if max_retained_bytes is not None:
                    retained_limit = int(max_retained_bytes)
                    if retained_limit <= 0:
                        raise ValueError("max_retained_bytes must be positive")
                    self._serialize_resource_transaction(session, "workspace-budget")
                    used_bytes = int(session.scalar(select(func.sum(MemoryImportJob.workspace_bytes))) or 0)
                    if used_bytes >= retained_limit:
                        raise ImportWorkspaceBudgetExceeded(retained_limit, used_bytes, 0)
                session.add(row)
                session.commit()
                return self._detach(session, row)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def count_active_jobs(self, project_id: str) -> int:
        with self._lock:
            session = self._session_factory()
            try:
                return int(
                    session.scalar(
                        select(func.count(MemoryImportJob.id)).where(
                            MemoryImportJob.project_id == project_id,
                            MemoryImportJob.status.in_(_ACTIVE_JOB_STATUSES),
                        )
                    )
                    or 0
                )
            finally:
                session.close()

    def activate_graph_retry(
        self,
        job_id: str,
        project_id: str,
        lease_owner: str,
        *,
        lease_seconds: float,
        max_active_jobs: int,
        now: datetime | str | None = None,
    ) -> MemoryImportJob | None:
        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        limit = int(max_active_jobs)
        if limit <= 0:
            raise ValueError("max_active_jobs must be positive")
        activated_at, expires_at = self._lease_window(lease_seconds, now)
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"import-job-operation:{job_id}")
                self._serialize_resource_transaction(session, f"active-imports:{project_id}")
                eligible = session.scalar(
                    select(MemoryImportJob.id).where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.project_id == project_id,
                        MemoryImportJob.status.in_(("completed", "completed_with_errors")),
                        MemoryImportJob.graph_status.in_(("failed", "pending", "disabled")),
                        MemoryImportJob.phase != _WORKSPACE_DISCARD_PHASE,
                    )
                )
                if eligible is None:
                    session.rollback()
                    return None
                active_jobs = int(
                    session.scalar(
                        select(func.count(MemoryImportJob.id)).where(
                            MemoryImportJob.project_id == project_id,
                            MemoryImportJob.status.in_(_ACTIVE_JOB_STATUSES),
                        )
                    )
                    or 0
                )
                if active_jobs >= limit:
                    raise ImportActiveJobLimitExceeded(project_id, limit, active_jobs)
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.project_id == project_id,
                        MemoryImportJob.status.in_(("completed", "completed_with_errors")),
                        MemoryImportJob.graph_status.in_(("failed", "pending", "disabled")),
                        MemoryImportJob.phase != _WORKSPACE_DISCARD_PHASE,
                    )
                    .values(
                        status="syncing_graph",
                        phase="graph_sync",
                        graph_status="syncing",
                        finished_at=None,
                        lease_owner=owner,
                        lease_expires_at=expires_at,
                        updated_at=activated_at,
                    )
                )
                if not result.rowcount:
                    session.rollback()
                    return None
                session.commit()
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def reserve_workspace_bytes(
        self,
        job_id: str,
        lease_owner: str,
        additional_bytes: int,
        *,
        max_retained_bytes: int,
        lease_seconds: float,
        now: datetime | str | None = None,
    ) -> tuple[int, int]:
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner is required")
        requested = int(additional_bytes)
        if requested < 0:
            raise ValueError("additional_bytes cannot be negative")
        limit = int(max_retained_bytes)
        if limit <= 0:
            raise ValueError("max_retained_bytes must be positive")
        reserved_at, expires_at = self._lease_window(lease_seconds, now)

        with _RESOURCE_TRANSACTION_LOCK:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, "workspace-budget")
                self._assert_active_lease(session, job_id, owner, reserved_at)
                used_bytes = int(session.scalar(select(func.sum(MemoryImportJob.workspace_bytes))) or 0)
                if used_bytes + requested > limit:
                    raise ImportWorkspaceBudgetExceeded(limit, used_bytes, requested)
                result = session.execute(
                    update(MemoryImportJob)
                    .where(*self._active_lease_conditions(job_id, owner, reserved_at))
                    .values(
                        workspace_bytes=MemoryImportJob.workspace_bytes + requested,
                        lease_expires_at=expires_at,
                        updated_at=reserved_at,
                    )
                )
                if not result.rowcount:
                    raise ImportLeaseLost(job_id, owner)
                job_bytes = int(
                    session.scalar(select(MemoryImportJob.workspace_bytes).where(MemoryImportJob.id == job_id)) or 0
                )
                session.commit()
                return used_bytes + requested, job_bytes
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def delete_uploading_job(self, job_id: str, lease_owner: str) -> bool:
        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        now = _utcnow()
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, "workspace-budget")
                result = session.execute(
                    delete(MemoryImportJob).where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.status == "uploading",
                        MemoryImportJob.lease_owner == owner,
                        MemoryImportJob.lease_expires_at.is_not(None),
                        MemoryImportJob.lease_expires_at > now,
                    )
                )
                session.commit()
                return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def get_job(self, job_id: str, project_id: str | None = None) -> MemoryImportJob | None:
        with self._lock:
            session = self._session_factory()
            try:
                statement = select(MemoryImportJob).where(MemoryImportJob.id == job_id)
                if project_id is not None:
                    statement = statement.where(MemoryImportJob.project_id == project_id)
                row = session.scalar(statement)
                if row is not None:
                    session.expunge(row)
                return row
            finally:
                session.close()

    def list_jobs(self, project_id: str, limit: int = 20) -> list[MemoryImportJob]:
        limit = max(0, int(limit))
        with self._lock:
            session = self._session_factory()
            try:
                rows = session.scalars(
                    select(MemoryImportJob)
                    .where(MemoryImportJob.project_id == project_id)
                    .order_by(MemoryImportJob.created_at.desc(), MemoryImportJob.id.desc())
                    .limit(limit)
                ).all()
                return self._detach_all(session, rows)
            finally:
                session.close()

    def list_jobs_with_workspaces(self) -> list[MemoryImportJob]:
        with self._lock:
            session = self._session_factory()
            try:
                rows = session.scalars(
                    select(MemoryImportJob)
                    .where(MemoryImportJob.workspace.is_not(None))
                    .order_by(MemoryImportJob.created_at, MemoryImportJob.id)
                ).all()
                return self._detach_all(session, rows)
            finally:
                session.close()

    def reconcile_workspace_bytes(
        self,
        job_id: str,
        workspace_bytes: int,
        *,
        available_at: datetime | str | None = None,
    ) -> bool:
        size = int(workspace_bytes)
        if size < 0:
            raise ValueError("workspace_bytes cannot be negative")
        checked_at = _coerce_datetime(available_at) or _utcnow()
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, "workspace-budget")
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        or_(
                            MemoryImportJob.lease_owner.is_(None),
                            MemoryImportJob.lease_expires_at.is_(None),
                            MemoryImportJob.lease_expires_at <= checked_at,
                        ),
                    )
                    .values(workspace_bytes=size, updated_at=checked_at)
                )
                session.commit()
                return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def list_recoverable_jobs(
        self,
        statuses: Iterable[str] | None = None,
        *,
        available_at: datetime | str | None = None,
    ) -> list[MemoryImportJob]:
        status_values = (
            list(_DEFAULT_RECOVERABLE_JOB_STATUSES)
            if statuses is None
            else list(dict.fromkeys(str(status) for status in statuses))
        )
        if not status_values:
            return []
        available_at = _coerce_datetime(available_at) or _utcnow()
        with self._lock:
            session = self._session_factory()
            try:
                rows = session.scalars(
                    select(MemoryImportJob)
                    .where(
                        MemoryImportJob.status.in_(status_values),
                        or_(
                            MemoryImportJob.lease_owner.is_(None),
                            MemoryImportJob.lease_expires_at.is_(None),
                            MemoryImportJob.lease_expires_at <= available_at,
                        ),
                    )
                    .order_by(MemoryImportJob.created_at, MemoryImportJob.id)
                ).all()
                return self._detach_all(session, rows)
            finally:
                session.close()

    @staticmethod
    def _lease_window(
        lease_seconds: float,
        now: datetime | str | None,
    ) -> tuple[datetime, datetime]:
        duration = float(lease_seconds)
        if duration <= 0:
            raise ValueError("lease_seconds must be positive")
        acquired_at = _coerce_datetime(now) or _utcnow()
        return acquired_at, acquired_at + timedelta(seconds=duration)

    def acquire_job_lease(
        self,
        job_id: str,
        lease_owner: str,
        *,
        lease_seconds: float,
        now: datetime | str | None = None,
    ) -> bool:
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner is required")
        acquired_at, expires_at = self._lease_window(lease_seconds, now)
        with self._lock:
            session = self._session_factory()
            try:
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.status.in_(_LEASEABLE_JOB_STATUSES),
                        MemoryImportJob.cancel_requested.is_(False),
                        or_(
                            MemoryImportJob.lease_owner.is_(None),
                            MemoryImportJob.lease_expires_at.is_(None),
                            MemoryImportJob.lease_expires_at <= acquired_at,
                        ),
                    )
                    .values(lease_owner=owner, lease_expires_at=expires_at)
                )
                session.commit()
                return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def acquire_job_retry_lease(
        self,
        job_id: str,
        project_id: str,
        lease_owner: str,
        *,
        lease_seconds: float,
        allowed_statuses: Iterable[str] = ("cancelled", "completed_with_errors", "failed"),
        max_active_jobs: int | None = None,
        now: datetime | str | None = None,
    ) -> MemoryImportJob | None:
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner is required")
        status_values = list(dict.fromkeys(str(status) for status in allowed_statuses))
        if not status_values:
            return None
        acquired_at, expires_at = self._lease_window(lease_seconds, now)
        if max_active_jobs is not None and int(max_active_jobs) <= 0:
            raise ValueError("max_active_jobs must be positive")
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"import-job-operation:{job_id}")
                if max_active_jobs is not None:
                    self._serialize_resource_transaction(session, f"active-imports:{project_id}")
                    eligible = session.scalar(
                        select(MemoryImportJob.id).where(
                            MemoryImportJob.id == job_id,
                            MemoryImportJob.project_id == project_id,
                            MemoryImportJob.status.in_(status_values),
                            MemoryImportJob.phase != _WORKSPACE_DISCARD_PHASE,
                        )
                    )
                    if eligible is None:
                        session.rollback()
                        return None
                    active_jobs = int(
                        session.scalar(
                            select(func.count(MemoryImportJob.id)).where(
                                MemoryImportJob.project_id == project_id,
                                MemoryImportJob.status.in_(_ACTIVE_JOB_STATUSES),
                            )
                        )
                        or 0
                    )
                    if active_jobs >= int(max_active_jobs):
                        raise ImportActiveJobLimitExceeded(
                            project_id,
                            int(max_active_jobs),
                            active_jobs,
                        )
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.project_id == project_id,
                        MemoryImportJob.status.in_(status_values),
                        MemoryImportJob.phase != _WORKSPACE_DISCARD_PHASE,
                    )
                    .values(
                        status="queued",
                        phase="queued",
                        cancel_requested=False,
                        finished_at=None,
                        graph_error=None,
                        active_workers=0,
                        discovered_files=0,
                        parsed_files=0,
                        skipped_files=0,
                        total_conversations=0,
                        total_chunks=0,
                        total_tokens=0,
                        current_file=None,
                        current_conversation=None,
                        lease_owner=owner,
                        lease_expires_at=expires_at,
                        updated_at=acquired_at,
                    )
                )
                if not result.rowcount:
                    session.rollback()
                    return None
                session.commit()
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def acquire_job_workspace_discard_lease(
        self,
        job_id: str,
        project_id: str,
        lease_owner: str,
        *,
        lease_seconds: float,
        allowed_statuses: Iterable[str] = _DISCARDABLE_JOB_STATUSES,
        now: datetime | str | None = None,
    ) -> MemoryImportJob | None:
        """Claim a terminal workspace while excluding retry and graph-retry transitions."""

        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        status_values = list(dict.fromkeys(str(status) for status in allowed_statuses))
        if not status_values:
            return None
        acquired_at, expires_at = self._lease_window(lease_seconds, now)
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"import-job-operation:{job_id}")
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.project_id == project_id,
                        MemoryImportJob.status.in_(status_values),
                        MemoryImportJob.workspace.is_not(None),
                        or_(
                            MemoryImportJob.phase != _WORKSPACE_DISCARD_PHASE,
                            MemoryImportJob.lease_owner.is_(None),
                            MemoryImportJob.lease_expires_at.is_(None),
                            MemoryImportJob.lease_expires_at <= acquired_at,
                        ),
                    )
                    .values(
                        phase=_WORKSPACE_DISCARD_PHASE,
                        lease_owner=owner,
                        lease_expires_at=expires_at,
                        updated_at=acquired_at,
                    )
                )
                if not result.rowcount:
                    session.rollback()
                    return None
                session.commit()
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    @staticmethod
    def _terminal_phase(status: str) -> str:
        return "completed" if status in {"completed", "completed_with_errors"} else status

    def complete_job_workspace_discard(
        self,
        job_id: str,
        lease_owner: str,
    ) -> MemoryImportJob:
        """Clear retained-source state after the claimed workspace is gone."""

        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"import-job-operation:{job_id}")
                row = session.scalar(
                    select(MemoryImportJob).where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.status.in_(_DISCARDABLE_JOB_STATUSES),
                        MemoryImportJob.phase == _WORKSPACE_DISCARD_PHASE,
                        MemoryImportJob.lease_owner == owner,
                    )
                )
                if row is None:
                    raise ImportLeaseLost(job_id, owner)
                row.phase = self._terminal_phase(row.status)
                row.workspace = None
                row.workspace_bytes = 0
                row.source_retry_required = False
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = _utcnow()
                session.commit()
                return self._detach(session, row)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def abort_job_workspace_discard(
        self,
        job_id: str,
        lease_owner: str,
    ) -> MemoryImportJob:
        """Release a discard claim without changing retained workspace accounting."""

        owner = self._normalize_lease_owner(lease_owner)
        assert owner is not None
        with _RESOURCE_TRANSACTION_LOCK, self._lock:
            session = self._session_factory()
            try:
                self._serialize_resource_transaction(session, f"import-job-operation:{job_id}")
                row = session.scalar(
                    select(MemoryImportJob).where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.status.in_(_DISCARDABLE_JOB_STATUSES),
                        MemoryImportJob.phase == _WORKSPACE_DISCARD_PHASE,
                        MemoryImportJob.lease_owner == owner,
                    )
                )
                if row is None:
                    raise ImportLeaseLost(job_id, owner)
                row.phase = self._terminal_phase(row.status)
                row.lease_owner = None
                row.lease_expires_at = None
                row.updated_at = _utcnow()
                session.commit()
                return self._detach(session, row)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def request_job_cancel(
        self,
        job_id: str,
        project_id: str,
        allowed_statuses: Iterable[str] = (
            "queued",
            "discovering",
            "parsing",
            "importing",
            "syncing_graph",
        ),
    ) -> MemoryImportJob | None:
        status_values = list(dict.fromkeys(str(status) for status in allowed_statuses))
        if not status_values:
            return None
        now = _utcnow()
        with self._lock:
            session = self._session_factory()
            try:
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.project_id == project_id,
                        MemoryImportJob.status.in_(status_values),
                    )
                    .values(
                        cancel_requested=True,
                        status="cancelling",
                        phase="cancelling",
                        updated_at=now,
                    )
                )
                if not result.rowcount:
                    session.rollback()
                    return None
                session.commit()
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def renew_job_lease(
        self,
        job_id: str,
        lease_owner: str,
        *,
        lease_seconds: float,
        now: datetime | str | None = None,
    ) -> bool:
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner is required")
        renewed_at, expires_at = self._lease_window(lease_seconds, now)
        with self._lock:
            session = self._session_factory()
            try:
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        *self._active_lease_conditions(job_id, owner, renewed_at),
                    )
                    .values(lease_expires_at=expires_at, updated_at=renewed_at)
                )
                session.commit()
                return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def release_job_lease(self, job_id: str, lease_owner: str) -> bool:
        owner = str(lease_owner).strip()
        if not owner:
            raise ValueError("lease_owner is required")
        with self._lock:
            session = self._session_factory()
            try:
                result = session.execute(
                    update(MemoryImportJob)
                    .where(
                        MemoryImportJob.id == job_id,
                        MemoryImportJob.lease_owner == owner,
                    )
                    .values(lease_owner=None, lease_expires_at=None)
                )
                session.commit()
                return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def update_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        **values: Any,
    ) -> MemoryImportJob | None:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        changes = _column_values(MemoryImportJob, values, exclude={"id", "created_at"})
        changes["updated_at"] = now
        with self._lock:
            session = self._session_factory()
            try:
                statement = update(MemoryImportJob).where(MemoryImportJob.id == job_id)
                if owner is not None:
                    statement = statement.where(*self._active_lease_conditions(job_id, owner, now))
                result = session.execute(statement.values(**changes))
                if owner is not None and not result.rowcount:
                    raise ImportLeaseLost(job_id, owner)
                session.commit()
                if not result.rowcount:
                    return None
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def increment_job(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        **deltas: int,
    ) -> MemoryImportJob | None:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        integer_columns = {
            column.key
            for column in MemoryImportJob.__table__.columns
            if isinstance(column.type, type(MemoryImportJob.total_chunks.type))
        }
        unknown = set(deltas) - integer_columns
        if unknown:
            names = ", ".join(sorted(unknown))
            raise TypeError(f"Non-counter MemoryImportJob field(s): {names}")
        assignments = {
            key: func.coalesce(getattr(MemoryImportJob, key), 0) + int(delta) for key, delta in deltas.items()
        }
        assignments["updated_at"] = now
        with self._lock:
            session = self._session_factory()
            try:
                statement = update(MemoryImportJob).where(MemoryImportJob.id == job_id)
                if owner is not None:
                    statement = statement.where(*self._active_lease_conditions(job_id, owner, now))
                result = session.execute(statement.values(**assignments))
                if owner is not None and not result.rowcount:
                    raise ImportLeaseLost(job_id, owner)
                session.commit()
                if not result.rowcount:
                    return None
                row = session.scalar(select(MemoryImportJob).where(MemoryImportJob.id == job_id))
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def upsert_chunk(
        self,
        *,
        lease_owner: str | None = None,
        **values: Any,
    ) -> MemoryImportChunk:
        owner = self._normalize_lease_owner(lease_owner)
        values = dict(values)
        if not values.get("job_id") or not values.get("import_key"):
            raise ValueError("job_id and import_key are required")
        now = _utcnow()
        values.setdefault("id", _new_id())
        values.setdefault("conversation_id", str(values["import_key"]))
        values.setdefault("status", "pending")
        values.setdefault("created_at", now)
        values.setdefault("updated_at", now)

        with self._lock:
            session = self._session_factory()
            try:
                if not values.get("project_id"):
                    project_id = session.scalar(
                        select(MemoryImportJob.project_id).where(MemoryImportJob.id == values["job_id"])
                    )
                    if project_id is None:
                        raise ValueError(f"Unknown import job: {values['job_id']}")
                    values["project_id"] = project_id
                normalized = _column_values(MemoryImportChunk, values)
                row = MemoryImportChunk(**normalized)
                try:
                    if owner is None:
                        session.add(row)
                    else:
                        self._insert_if_active_lease(
                            session,
                            MemoryImportChunk,
                            normalized,
                            str(values["job_id"]),
                            owner,
                            now,
                        )
                        row = session.scalar(select(MemoryImportChunk).where(MemoryImportChunk.id == normalized["id"]))
                        if row is None:
                            raise RuntimeError("Fenced chunk insert produced no row")
                    session.commit()
                    return self._detach(session, row)
                except IntegrityError as conflict:
                    session.rollback()
                    existing = session.scalar(
                        select(MemoryImportChunk).where(
                            MemoryImportChunk.job_id == values["job_id"],
                            MemoryImportChunk.import_key == values["import_key"],
                        )
                    )
                    if existing is None:
                        raise conflict
                    changes = _column_values(
                        MemoryImportChunk,
                        values,
                        exclude={"id", "job_id", "import_key", "created_at"},
                    )
                    if existing.status in _TERMINAL_CHUNK_STATUSES:
                        for key in (
                            "status",
                            "memory_ids",
                            "finished_at",
                            "error_type",
                            "error_message",
                        ):
                            changes.pop(key, None)
                    update_now = _utcnow()
                    changes["updated_at"] = update_now
                    if changes:
                        statement = update(MemoryImportChunk).where(MemoryImportChunk.id == existing.id)
                        if owner is not None:
                            statement = statement.where(
                                self._active_lease_exists(
                                    str(values["job_id"]),
                                    owner,
                                    update_now,
                                )
                            )
                        result = session.execute(statement.values(**changes))
                        if owner is not None and not result.rowcount:
                            self._assert_active_lease(
                                session,
                                str(values["job_id"]),
                                owner,
                                update_now,
                            )
                        session.commit()
                        existing = session.scalar(select(MemoryImportChunk).where(MemoryImportChunk.id == existing.id))
                    return self._detach(session, existing)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def get_chunk(self, job_id: str, import_key: str) -> MemoryImportChunk | None:
        with self._lock:
            session = self._session_factory()
            try:
                row = session.scalar(
                    select(MemoryImportChunk).where(
                        MemoryImportChunk.job_id == job_id,
                        MemoryImportChunk.import_key == import_key,
                    )
                )
                if row is not None:
                    session.expunge(row)
                return row
            finally:
                session.close()

    def list_chunks(
        self,
        job_id: str,
        statuses: Iterable[str] | str | None = None,
    ) -> list[MemoryImportChunk]:
        statement = select(MemoryImportChunk).where(MemoryImportChunk.job_id == job_id)
        if statuses is not None:
            status_values = [statuses] if isinstance(statuses, str) else list(statuses)
            if not status_values:
                return []
            statement = statement.where(MemoryImportChunk.status.in_(status_values))
        statement = statement.order_by(
            MemoryImportChunk.conversation_id,
            MemoryImportChunk.chunk_index,
            MemoryImportChunk.created_at,
        )
        with self._lock:
            session = self._session_factory()
            try:
                return self._detach_all(session, session.scalars(statement).all())
            finally:
                session.close()

    def load_persisted_chunk_statuses(
        self,
        project_id: str,
        current_job_id: str,
    ) -> dict[str, str]:
        """Load current recovery state plus manifest-confirmed project split history."""
        current_statement = (
            select(
                MemoryImportChunk.import_key,
                MemoryImportChunk.status,
                MemoryImportManifest.status.label("manifest_status"),
            )
            .outerjoin(
                MemoryImportManifest,
                (MemoryImportManifest.project_id == MemoryImportChunk.project_id)
                & (MemoryImportManifest.import_key == MemoryImportChunk.import_key),
            )
            .where(
                MemoryImportChunk.project_id == project_id,
                MemoryImportChunk.job_id == current_job_id,
            )
        )
        historical_statement = (
            select(MemoryImportManifest.import_key)
            .outerjoin(
                MemoryImportChunk,
                MemoryImportChunk.id == MemoryImportManifest.chunk_id,
            )
            .where(
                MemoryImportManifest.project_id == project_id,
                MemoryImportManifest.status.in_(("split", "released")),
                or_(
                    MemoryImportManifest.status == "split",
                    MemoryImportChunk.status == "split",
                ),
            )
        )
        with self._lock:
            session = self._session_factory()
            try:
                statuses = {str(import_key): "split" for import_key in session.scalars(historical_statement)}
                for import_key, chunk_status, manifest_status in session.execute(current_statement):
                    key = str(import_key)
                    status = str(chunk_status)
                    if status == "split" and str(manifest_status or "").lower() in _SUCCEEDED_MANIFEST_STATUSES:
                        statuses[key] = "superseded_split"
                    else:
                        statuses[key] = status
                return statuses
            finally:
                session.close()

    def update_chunk(
        self,
        job_id: str,
        import_key: str,
        *,
        lease_owner: str | None = None,
        **values: Any,
    ) -> MemoryImportChunk | None:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        changes = _column_values(
            MemoryImportChunk,
            values,
            exclude={"id", "job_id", "import_key", "created_at"},
        )
        changes["updated_at"] = now
        with self._lock:
            session = self._session_factory()
            try:
                statement = update(MemoryImportChunk).where(
                    MemoryImportChunk.job_id == job_id,
                    MemoryImportChunk.import_key == import_key,
                )
                if owner is not None:
                    statement = statement.where(self._active_lease_exists(job_id, owner, now))
                result = session.execute(statement.values(**changes))
                if owner is not None and not result.rowcount:
                    self._assert_active_lease(session, job_id, owner, now)
                session.commit()
                if not result.rowcount:
                    return None
                row = session.scalar(
                    select(MemoryImportChunk).where(
                        MemoryImportChunk.job_id == job_id,
                        MemoryImportChunk.import_key == import_key,
                    )
                )
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def load_manifests(
        self,
        project_id: str,
        keys: Iterable[str],
    ) -> dict[str, MemoryImportManifest]:
        key_list = list(dict.fromkeys(str(key) for key in keys))
        if not key_list:
            return {}
        found: dict[str, MemoryImportManifest] = {}
        with self._lock:
            session = self._session_factory()
            try:
                for offset in range(0, len(key_list), 500):
                    rows = session.scalars(
                        select(MemoryImportManifest).where(
                            MemoryImportManifest.project_id == project_id,
                            MemoryImportManifest.import_key.in_(key_list[offset : offset + 500]),
                        )
                    ).all()
                    for row in rows:
                        session.expunge(row)
                        found[row.import_key] = row
                return found
            finally:
                session.close()

    def claim_manifest(
        self,
        project_id: str,
        key: str,
        job_id: str,
        chunk_id: str,
        *,
        lease_owner: str | None = None,
    ) -> tuple[bool, MemoryImportManifest]:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        with self._lock:
            session = self._session_factory()
            try:
                row_values = _column_values(
                    MemoryImportManifest,
                    {
                        "id": _new_id(),
                        "project_id": project_id,
                        "import_key": key,
                        "job_id": job_id,
                        "chunk_id": chunk_id,
                        "status": "claimed",
                        "attempts": 1,
                        "memory_ids": [],
                        "claimed_at": now,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                row = MemoryImportManifest(**row_values)
                try:
                    if owner is None:
                        session.add(row)
                    else:
                        self._insert_if_active_lease(
                            session,
                            MemoryImportManifest,
                            row_values,
                            job_id,
                            owner,
                            now,
                        )
                        row = session.scalar(
                            select(MemoryImportManifest).where(MemoryImportManifest.id == row_values["id"])
                        )
                        if row is None:
                            raise RuntimeError("Fenced manifest insert produced no row")
                    session.commit()
                    return True, self._detach(session, row)
                except IntegrityError as conflict:
                    session.rollback()
                    existing = session.scalar(
                        select(MemoryImportManifest).where(
                            MemoryImportManifest.project_id == project_id,
                            MemoryImportManifest.import_key == key,
                        )
                    )
                    if existing is None:
                        raise conflict
                    update_now = _utcnow()
                    legacy_released_split = (
                        select(MemoryImportChunk.id)
                        .where(
                            MemoryImportChunk.id == MemoryImportManifest.chunk_id,
                            MemoryImportChunk.status == "split",
                        )
                        .exists()
                    )
                    statement = update(MemoryImportManifest).where(
                        MemoryImportManifest.id == existing.id,
                        MemoryImportManifest.status.in_(_RECLAIMABLE_STATUSES),
                        ~((MemoryImportManifest.status == "released") & legacy_released_split),
                    )
                    if owner is not None:
                        statement = statement.where(self._active_lease_exists(job_id, owner, update_now))
                    result = session.execute(
                        statement.values(
                            job_id=job_id,
                            chunk_id=chunk_id,
                            status="claimed",
                            attempts=MemoryImportManifest.attempts + 1,
                            last_error=None,
                            claimed_at=update_now,
                            completed_at=None,
                            updated_at=update_now,
                        )
                    )
                    if not result.rowcount:
                        upgrade = update(MemoryImportManifest).where(
                            MemoryImportManifest.id == existing.id,
                            MemoryImportManifest.status == "released",
                            legacy_released_split,
                        )
                        if owner is not None:
                            upgrade = upgrade.where(self._active_lease_exists(job_id, owner, update_now))
                        session.execute(
                            upgrade.values(
                                status="split",
                                completed_at=update_now,
                                updated_at=update_now,
                            )
                        )
                    if owner is not None and not result.rowcount:
                        self._assert_active_lease(session, job_id, owner, update_now)
                    session.commit()
                    existing = session.scalar(
                        select(MemoryImportManifest).where(MemoryImportManifest.id == existing.id)
                    )
                    return bool(result.rowcount), self._detach(session, existing)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def mark_manifest(
        self,
        project_id: str,
        key: str,
        status: str,
        memory_ids: Iterable[str] | None = None,
        *,
        job_id: str | None = None,
        lease_owner: str | None = None,
        **values: Any,
    ) -> MemoryImportManifest | None:
        owner = self._normalize_lease_owner(lease_owner)
        if owner is not None and not job_id:
            raise ValueError("job_id is required when lease_owner is provided")
        now = _utcnow()
        changes: dict[str, Any] = {"status": status, "updated_at": now}
        if memory_ids is not None:
            changes["memory_ids"] = list(memory_ids)
        if status in _TERMINAL_CHUNK_STATUSES:
            changes["completed_at"] = now
        changes.update(
            _column_values(
                MemoryImportManifest,
                values,
                exclude={"id", "project_id", "import_key", "created_at"},
            )
        )
        with self._lock:
            session = self._session_factory()
            try:
                statement = update(MemoryImportManifest).where(
                    MemoryImportManifest.project_id == project_id,
                    MemoryImportManifest.import_key == key,
                )
                if owner is not None:
                    statement = statement.where(
                        MemoryImportManifest.job_id == job_id,
                        self._active_lease_exists(str(job_id), owner, now),
                    )
                result = session.execute(statement.values(**changes))
                if owner is not None and not result.rowcount:
                    self._assert_active_lease(session, str(job_id), owner, now)
                session.commit()
                if not result.rowcount:
                    return None
                row = session.scalar(
                    select(MemoryImportManifest).where(
                        MemoryImportManifest.project_id == project_id,
                        MemoryImportManifest.import_key == key,
                    )
                )
                return self._detach(session, row) if row is not None else None
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def add_error(
        self,
        job_id: str,
        source: str,
        message: str,
        *,
        lease_owner: str | None = None,
        **values: Any,
    ) -> MemoryImportError:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        values = dict(values)
        values.update(
            id=values.get("id", _new_id()),
            job_id=job_id,
            source=str(source),
            message=str(message),
            created_at=values.get("created_at", now),
        )
        normalized = _column_values(MemoryImportError, values)
        row = MemoryImportError(**normalized)
        with self._lock:
            session = self._session_factory()
            try:
                if owner is None:
                    session.add(row)
                else:
                    self._insert_if_active_lease(
                        session,
                        MemoryImportError,
                        normalized,
                        job_id,
                        owner,
                        now,
                    )
                statement = update(MemoryImportJob).where(MemoryImportJob.id == job_id)
                if owner is not None:
                    statement = statement.where(*self._active_lease_conditions(job_id, owner, now))
                result = session.execute(
                    statement.values(
                        error_count=MemoryImportJob.error_count + 1,
                        updated_at=now,
                    )
                )
                if owner is not None and not result.rowcount:
                    raise ImportLeaseLost(job_id, owner)
                if owner is not None:
                    row = session.scalar(select(MemoryImportError).where(MemoryImportError.id == normalized["id"]))
                    if row is None:
                        raise RuntimeError("Fenced import error insert produced no row")
                session.commit()
                return self._detach(session, row)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def list_errors(
        self,
        job_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryImportError]:
        with self._lock:
            session = self._session_factory()
            try:
                rows = session.scalars(
                    select(MemoryImportError)
                    .where(MemoryImportError.job_id == job_id)
                    .order_by(MemoryImportError.created_at, MemoryImportError.id)
                    .offset(max(0, int(offset)))
                    .limit(max(0, int(limit)))
                ).all()
                return self._detach_all(session, rows)
            finally:
                session.close()

    def claim_memory_hash(
        self,
        project_id: str,
        conversation_id: str,
        memory_hash: str,
        job_id: str,
        chunk_id: str,
        *,
        lease_owner: str | None = None,
    ) -> bool:
        owner = self._normalize_lease_owner(lease_owner)
        now = _utcnow()
        with self._lock:
            session = self._session_factory()
            try:
                row_values = _column_values(
                    MemoryImportHash,
                    {
                        "id": _new_id(),
                        "project_id": project_id,
                        "conversation_id": conversation_id,
                        "memory_hash": memory_hash,
                        "job_id": job_id,
                        "chunk_id": chunk_id,
                        "status": "claimed",
                        "claimed_at": now,
                        "created_at": now,
                        "updated_at": now,
                    },
                )
                row = MemoryImportHash(**row_values)
                try:
                    if owner is None:
                        session.add(row)
                    else:
                        self._insert_if_active_lease(
                            session,
                            MemoryImportHash,
                            row_values,
                            job_id,
                            owner,
                            now,
                        )
                    session.commit()
                    return True
                except IntegrityError as conflict:
                    session.rollback()
                    existing = session.scalar(
                        select(MemoryImportHash).where(
                            MemoryImportHash.project_id == project_id,
                            MemoryImportHash.conversation_id == conversation_id,
                            MemoryImportHash.memory_hash == memory_hash,
                        )
                    )
                    if existing is None:
                        raise conflict
                    update_now = _utcnow()
                    statement = update(MemoryImportHash).where(
                        MemoryImportHash.id == existing.id,
                        MemoryImportHash.status.in_(_RECLAIMABLE_STATUSES),
                    )
                    if owner is not None:
                        statement = statement.where(self._active_lease_exists(job_id, owner, update_now))
                    result = session.execute(
                        statement.values(
                            job_id=job_id,
                            chunk_id=chunk_id,
                            status="claimed",
                            memory_id=None,
                            claimed_at=update_now,
                            succeeded_at=None,
                            updated_at=update_now,
                        )
                    )
                    if owner is not None and not result.rowcount:
                        self._assert_active_lease(session, job_id, owner, update_now)
                    session.commit()
                    return bool(result.rowcount)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def release_memory_hashes(
        self,
        job_id: str,
        chunk_id: str,
        hashes: Iterable[str] | None = None,
        *,
        lease_owner: str | None = None,
    ) -> int:
        owner = self._normalize_lease_owner(lease_owner)
        hash_values = None if hashes is None else list(dict.fromkeys(str(value) for value in hashes))
        if hash_values == []:
            if owner is not None:
                now = _utcnow()
                with self._lock:
                    session = self._session_factory()
                    try:
                        self._assert_active_lease(session, job_id, owner, now)
                    finally:
                        session.close()
            return 0
        predicates = [
            MemoryImportHash.job_id == job_id,
            MemoryImportHash.chunk_id == chunk_id,
            MemoryImportHash.status == "claimed",
        ]
        if hash_values is not None:
            predicates.append(MemoryImportHash.memory_hash.in_(hash_values))
        now = _utcnow()
        if owner is not None:
            predicates.append(self._active_lease_exists(job_id, owner, now))
        with self._lock:
            session = self._session_factory()
            try:
                result = session.execute(delete(MemoryImportHash).where(*predicates))
                if owner is not None and not result.rowcount:
                    self._assert_active_lease(session, job_id, owner, now)
                session.commit()
                return int(result.rowcount or 0)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def mark_memory_hashes_succeeded(
        self,
        project_id: str,
        conversation_id: str,
        memory_hashes: Iterable[str],
        job_id: str | None = None,
        chunk_id: str | None = None,
        memory_ids: Mapping[str, str] | Iterable[str] | None = None,
        *,
        lease_owner: str | None = None,
    ) -> int:
        owner = self._normalize_lease_owner(lease_owner)
        if owner is not None and job_id is None:
            raise ValueError("job_id is required when lease_owner is provided")
        hashes = list(dict.fromkeys(str(value) for value in memory_hashes))
        if not hashes:
            if owner is not None:
                now = _utcnow()
                with self._lock:
                    session = self._session_factory()
                    try:
                        self._assert_active_lease(session, str(job_id), owner, now)
                    finally:
                        session.close()
            return 0
        if isinstance(memory_ids, Mapping):
            id_by_hash = {str(key): str(value) for key, value in memory_ids.items()}
        elif memory_ids is None:
            id_by_hash = {}
        else:
            id_by_hash = {
                memory_hash: str(memory_id) for memory_hash, memory_id in zip(hashes, memory_ids, strict=False)
            }
        now = _utcnow()
        updated = 0
        with self._lock:
            session = self._session_factory()
            try:
                for memory_hash in hashes:
                    predicates = [
                        MemoryImportHash.project_id == project_id,
                        MemoryImportHash.conversation_id == conversation_id,
                        MemoryImportHash.memory_hash == memory_hash,
                        MemoryImportHash.status == "claimed",
                    ]
                    if job_id is not None:
                        predicates.append(MemoryImportHash.job_id == job_id)
                    if chunk_id is not None:
                        predicates.append(MemoryImportHash.chunk_id == chunk_id)
                    if owner is not None:
                        predicates.append(self._active_lease_exists(str(job_id), owner, now))
                    result = session.execute(
                        update(MemoryImportHash)
                        .where(*predicates)
                        .values(
                            status="succeeded",
                            memory_id=id_by_hash.get(memory_hash),
                            succeeded_at=now,
                            updated_at=now,
                        )
                    )
                    updated += int(result.rowcount or 0)
                if owner is not None:
                    self._assert_active_lease(session, str(job_id), owner, now)
                session.commit()
                return updated
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    @staticmethod
    def _graph_payload(item: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
        raw_payload = item.get("payload")
        source = raw_payload if isinstance(raw_payload, Mapping) else item
        payload = {
            "memory_id": str(source.get("memory_id") or item.get("memory_id") or ""),
            "text": source.get("text"),
            "entities": dict(source.get("entities") or {}),
            "metadata": dict(source.get("metadata") or {}),
        }
        if not payload["memory_id"]:
            raise ValueError("Each graph item requires memory_id")
        item_key = str(item.get("item_key") or payload["memory_id"])
        if not item_key:
            canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            item_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return item_key, payload["memory_id"], payload

    def add_graph_items(
        self,
        job_id: str,
        chunk_id: str,
        items: Iterable[Mapping[str, Any]],
        *,
        lease_owner: str | None = None,
    ) -> list[MemoryImportGraphItem]:
        owner = self._normalize_lease_owner(lease_owner)
        prepared_by_key: dict[str, tuple[str, str, dict[str, Any]]] = {}
        for item in items:
            prepared = self._graph_payload(item)
            prepared_by_key.setdefault(prepared[0], prepared)
        prepared = list(prepared_by_key.values())
        if not prepared:
            return []
        now = _utcnow()
        rows: list[MemoryImportGraphItem] = []
        inserted = 0
        with self._lock:
            session = self._session_factory()
            try:
                for item_key, memory_id, payload in prepared:
                    row_values = _column_values(
                        MemoryImportGraphItem,
                        {
                            "id": _new_id(),
                            "job_id": job_id,
                            "chunk_id": chunk_id,
                            "item_key": item_key,
                            "memory_id": memory_id,
                            "payload": payload,
                            "status": "pending",
                            "attempts": 0,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
                    row = MemoryImportGraphItem(**row_values)
                    try:
                        with session.begin_nested():
                            if owner is None:
                                session.add(row)
                                session.flush()
                            else:
                                self._insert_if_active_lease(
                                    session,
                                    MemoryImportGraphItem,
                                    row_values,
                                    job_id,
                                    owner,
                                    now,
                                )
                        inserted += 1
                        if owner is not None:
                            row = session.scalar(
                                select(MemoryImportGraphItem).where(MemoryImportGraphItem.id == row_values["id"])
                            )
                            if row is None:
                                raise RuntimeError("Fenced graph item insert produced no row")
                    except IntegrityError:
                        row = session.scalar(
                            select(MemoryImportGraphItem).where(
                                MemoryImportGraphItem.job_id == job_id,
                                MemoryImportGraphItem.chunk_id == chunk_id,
                                MemoryImportGraphItem.item_key == item_key,
                            )
                        )
                        if row is None:
                            raise
                    rows.append(row)
                if inserted:
                    statement = update(MemoryImportJob).where(MemoryImportJob.id == job_id)
                    if owner is not None:
                        statement = statement.where(*self._active_lease_conditions(job_id, owner, now))
                    result = session.execute(
                        statement.values(
                            graph_status="pending",
                            graph_pending_items=MemoryImportJob.graph_pending_items + inserted,
                            updated_at=now,
                        )
                    )
                    if owner is not None and not result.rowcount:
                        raise ImportLeaseLost(job_id, owner)
                elif owner is not None:
                    self._assert_active_lease(session, job_id, owner, now)
                session.commit()
                return [self._detach(session, row) for row in rows]
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def list_graph_items(
        self,
        job_id: str,
        status: str | Iterable[str] | None = "pending",
        limit: int | None = None,
    ) -> list[MemoryImportGraphItem]:
        statement = select(MemoryImportGraphItem).where(MemoryImportGraphItem.job_id == job_id)
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            if not statuses:
                return []
            statement = statement.where(MemoryImportGraphItem.status.in_(statuses))
        statement = statement.order_by(MemoryImportGraphItem.created_at, MemoryImportGraphItem.id)
        if limit is not None:
            statement = statement.limit(max(0, int(limit)))
        with self._lock:
            session = self._session_factory()
            try:
                return self._detach_all(session, session.scalars(statement).all())
            finally:
                session.close()

    def _refresh_graph_job_counts(
        self,
        session: Session,
        job_ids: Iterable[str],
        error: str | None,
        *,
        lease_owner: str | None = None,
        now: datetime | None = None,
    ) -> None:
        owner = self._normalize_lease_owner(lease_owner)
        updated_at = now or _utcnow()
        for job_id in set(job_ids):
            counts = dict(
                session.execute(
                    select(MemoryImportGraphItem.status, func.count(MemoryImportGraphItem.id))
                    .where(MemoryImportGraphItem.job_id == job_id)
                    .group_by(MemoryImportGraphItem.status)
                ).all()
            )
            pending = int(counts.get("pending", 0))
            failed = int(counts.get("failed", 0))
            synced = int(counts.get("synced", 0))
            graph_status = "failed" if failed else ("pending" if pending else "synced")
            statement = update(MemoryImportJob).where(MemoryImportJob.id == job_id)
            if owner is not None:
                statement = statement.where(*self._active_lease_conditions(job_id, owner, updated_at))
            result = session.execute(
                statement.values(
                    graph_status=graph_status,
                    graph_error=error if failed else None,
                    graph_pending_items=pending,
                    graph_synced_items=synced,
                    graph_failed_items=failed,
                    updated_at=updated_at,
                )
            )
            if owner is not None and not result.rowcount:
                raise ImportLeaseLost(job_id, owner)

    def mark_graph_items(
        self,
        item_ids: Iterable[str] | str,
        status: str,
        error: str | None = None,
        *,
        increment_attempts: bool = False,
        next_retry_at: datetime | str | None = None,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> int:
        owner = self._normalize_lease_owner(lease_owner)
        if owner is not None and not job_id:
            raise ValueError("job_id is required when lease_owner is provided")
        raw_ids = [item_ids] if isinstance(item_ids, str) else list(item_ids)
        ids = list(dict.fromkeys(str(getattr(item_id, "id", item_id)) for item_id in raw_ids))
        if not ids:
            return 0
        now = _utcnow()
        values: dict[str, Any] = {
            "status": status,
            "last_error": error,
            "updated_at": now,
        }
        if increment_attempts:
            values["attempts"] = MemoryImportGraphItem.attempts + 1
        if next_retry_at is not None:
            values["next_retry_at"] = _coerce_datetime(next_retry_at)
        if status == "synced":
            values.update(synced_at=now, last_error=None, next_retry_at=None)
        with self._lock:
            session = self._session_factory()
            try:
                job_id_statement = select(MemoryImportGraphItem.job_id).where(MemoryImportGraphItem.id.in_(ids))
                if job_id is not None:
                    job_id_statement = job_id_statement.where(MemoryImportGraphItem.job_id == job_id)
                job_ids = session.scalars(job_id_statement.distinct()).all()
                statement = update(MemoryImportGraphItem).where(MemoryImportGraphItem.id.in_(ids))
                if job_id is not None:
                    statement = statement.where(MemoryImportGraphItem.job_id == job_id)
                if owner is not None:
                    statement = statement.where(self._active_lease_exists(str(job_id), owner, now))
                result = session.execute(statement.values(**values))
                if owner is not None and not result.rowcount:
                    self._assert_active_lease(session, str(job_id), owner, now)
                if increment_attempts and job_ids:
                    job_statement = update(MemoryImportJob).where(MemoryImportJob.id.in_(job_ids))
                    if owner is not None:
                        job_statement = job_statement.where(*self._active_lease_conditions(str(job_id), owner, now))
                    job_result = session.execute(
                        job_statement.values(
                            graph_attempts=MemoryImportJob.graph_attempts + 1,
                            updated_at=now,
                        )
                    )
                    if owner is not None and not job_result.rowcount:
                        raise ImportLeaseLost(str(job_id), owner)
                self._refresh_graph_job_counts(
                    session,
                    job_ids,
                    error,
                    lease_owner=owner,
                    now=now,
                )
                session.commit()
                return int(result.rowcount or 0)
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def mark_graph_synced(
        self,
        item_ids: Iterable[str] | str,
        *,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> int:
        return self.mark_graph_items(
            item_ids,
            "synced",
            increment_attempts=True,
            job_id=job_id,
            lease_owner=lease_owner,
        )

    def mark_graph_failed(
        self,
        item_ids: Iterable[str] | str,
        error: str,
        next_retry_at: datetime | str | None = None,
        *,
        job_id: str | None = None,
        lease_owner: str | None = None,
    ) -> int:
        return self.mark_graph_items(
            item_ids,
            "failed",
            error,
            increment_attempts=True,
            next_retry_at=next_retry_at,
            job_id=job_id,
            lease_owner=lease_owner,
        )
