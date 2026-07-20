from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from chat_import import PermanentImportError
from fastapi import Request
from models import QuotaPolicy, User
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from usage_service import applicable_policies, request_scope_context

STORAGE_QUOTA_SNAPSHOT_VERSION = 1
_BYPASS_AUTH_TYPES = {"admin_api_key", "disabled"}
_LOCAL_SCOPE_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_SCOPE_LOCKS_GUARD = threading.Lock()


class ImportStorageQuotaExceeded(PermanentImportError):
    """The selected import memories cannot fit under a snapshotted hard limit."""

    memory_import_capacity_error = True

    def __init__(
        self,
        *,
        scope_type: str,
        scope_id: str,
        limit_value: int,
        used: int,
        selected_new: int,
    ) -> None:
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.limit_value = limit_value
        self.used = used
        self.selected_new = selected_new
        self.projected = used + selected_new
        super().__init__(
            "Stored-memory quota exceeded for "
            f"{scope_type} '{scope_id}': {used} currently stored + {selected_new} selected "
            f"= {self.projected}, above hard limit {limit_value}. Free capacity and retry the write."
        )


def capture_import_storage_quota_snapshot(
    request: Request,
    user: User | None,
    db: Session,
) -> dict[str, Any]:
    """Capture server-resolved hard storage policies; request data is never consulted."""
    if getattr(request.state, "auth_type", "none") in _BYPASS_AUTH_TYPES:
        return {}
    context = request_scope_context(request, user, db)
    policies = [
        policy
        for policy in applicable_policies(db, context, {"stored_memories"})
        if policy.mode == "hard" and policy.scope_type in {"organization", "project"}
    ]
    policies.sort(key=lambda policy: (policy.scope_type, policy.scope_id, str(policy.id)))
    return {
        "version": STORAGE_QUOTA_SNAPSHOT_VERSION,
        "policies": [
            {
                "id": str(policy.id),
                "scope_type": policy.scope_type,
                "scope_id": policy.scope_id,
                "limit_value": int(policy.limit_value),
            }
            for policy in policies
        ],
    }


def _normalized_snapshot_policies(snapshot: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    if snapshot.get("version") != STORAGE_QUOTA_SNAPSHOT_VERSION:
        raise ValueError("Unsupported memory-import storage quota snapshot version.")
    raw_policies = snapshot.get("policies")
    if not isinstance(raw_policies, list):
        raise ValueError("Memory-import storage quota snapshot policies must be a list.")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[uuid.UUID] = set()
    for raw_policy in raw_policies:
        if not isinstance(raw_policy, Mapping):
            raise ValueError("Memory-import storage quota snapshot policy must be an object.")
        try:
            policy_id = uuid.UUID(str(raw_policy["id"]))
            scope_type = str(raw_policy["scope_type"])
            scope_id = str(raw_policy["scope_id"]).strip()
            limit_value = int(raw_policy["limit_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Memory-import storage quota snapshot policy is invalid.") from exc
        if scope_type not in {"organization", "project"} or not scope_id or limit_value <= 0:
            raise ValueError("Memory-import storage quota snapshot policy is invalid.")
        if policy_id in seen_ids:
            raise ValueError("Memory-import storage quota snapshot contains duplicate policy IDs.")
        seen_ids.add(policy_id)
        normalized.append(
            {
                "id": policy_id,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "limit_value": limit_value,
            }
        )
    return sorted(normalized, key=lambda item: (item["scope_type"], item["scope_id"], str(item["id"])))


def _scope_lock(resource: str) -> threading.Lock:
    with _LOCAL_SCOPE_LOCKS_GUARD:
        return _LOCAL_SCOPE_LOCKS.setdefault(resource, threading.Lock())


class ImportStorageQuotaGuard:
    """Serialize quota checks and memory writes that share a hard storage policy."""

    def __init__(
        self,
        snapshot: Mapping[str, Any] | None,
        session_factory: Callable[[], Session],
        count_project_memories: Callable[[str], int],
        organization_project_ids: Callable[[str], Sequence[str]],
    ) -> None:
        self._policies = _normalized_snapshot_policies(snapshot)
        self._session_factory = session_factory
        self._count_project_memories = count_project_memories
        self._organization_project_ids = organization_project_ids
        self._session: Session | None = None
        self._locks: list[threading.Lock] = []
        self._checked = False

    @property
    def enabled(self) -> bool:
        return bool(self._policies)

    def __call__(self, selected_new: int) -> None:
        if isinstance(selected_new, bool) or not isinstance(selected_new, int) or selected_new < 0:
            raise ValueError("selected_new must be a non-negative integer.")
        if not self._policies:
            return
        if self._checked or self._session is not None:
            raise RuntimeError("The memory-import storage quota guard can only be checked once.")
        self._checked = True

        resources = sorted({f"stored-memory:{policy['scope_type']}:{policy['scope_id']}" for policy in self._policies})
        try:
            for resource in resources:
                lock = _scope_lock(resource)
                lock.acquire()
                self._locks.append(lock)

            session = self._session_factory()
            self._session = session
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            elif dialect == "postgresql":
                for resource in resources:
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(hashtext(:resource))"),
                        {"resource": f"mem0:{resource}"},
                    )

            policy_ids = [policy["id"] for policy in self._policies]
            session.execute(
                select(QuotaPolicy).where(QuotaPolicy.id.in_(policy_ids)).order_by(QuotaPolicy.id).with_for_update()
            ).scalars().all()

            project_counts: dict[str, int] = {}

            def project_count(project_id: str) -> int:
                if project_id not in project_counts:
                    value = self._count_project_memories(project_id)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        raise ValueError("Stored-memory count must be a non-negative integer.")
                    project_counts[project_id] = value
                return project_counts[project_id]

            for policy in self._policies:
                if policy["scope_type"] == "project":
                    used = project_count(policy["scope_id"])
                else:
                    project_ids = dict.fromkeys(
                        str(project_id)
                        for project_id in self._organization_project_ids(policy["scope_id"])
                        if str(project_id)
                    )
                    used = sum(project_count(project_id) for project_id in project_ids)
                if used + selected_new > policy["limit_value"]:
                    raise ImportStorageQuotaExceeded(
                        scope_type=policy["scope_type"],
                        scope_id=policy["scope_id"],
                        limit_value=policy["limit_value"],
                        used=used,
                        selected_new=selected_new,
                    )
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        session, self._session = self._session, None
        try:
            if session is not None:
                session.rollback()
                session.close()
        finally:
            while self._locks:
                self._locks.pop().release()
