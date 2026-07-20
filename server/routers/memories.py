import logging
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal

from auth import require_project_read, require_project_write
from db import SessionLocal, get_db
from fastapi import APIRouter, Depends, HTTPException, Request
from models import Settings
from project_scope import DEFAULT_PROJECT_ID, get_project_id
from pydantic import BaseModel, Field, field_validator, model_validator
from routers import exports as exports_router
from server_state import get_memory_instance
from settings_store import get_json, set_json
from sqlalchemy.orm import Session

router = APIRouter(prefix="/memories", tags=["memories"])

ENTITY_FIELDS = {
    "user": "user_id",
    "agent": "agent_id",
    "app": "app_id",
    "run": "run_id",
}
SESSION_SCOPE_KEYS = ("agent_id", "app_id", "project_id", "run_id", "user_id")


class MemoryFilter(BaseModel):
    field: Literal["entity", "memory_id", "category", "metadata"]
    value: Any
    entity_type: Literal["user", "agent", "app", "run"] | None = None
    key: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_filter(self):
        if self.field == "entity" and self.entity_type is None:
            raise ValueError("entity_type is required for entity filters.")
        if self.field == "metadata" and not (self.key or "").strip():
            raise ValueError("key is required for metadata filters.")
        if isinstance(self.value, str) and not self.value.strip():
            raise ValueError("Filter values cannot be empty.")
        if self.value is None:
            raise ValueError("Filter values cannot be null.")
        return self


class MemoryQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    match: Literal["all", "any"] = "all"
    filters: list[MemoryFilter] = Field(default_factory=list, max_length=50)
    category: str | None = Field(default=None, max_length=255)
    start_date: str | None = None
    end_date: str | None = None

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_dates(self):
        start = exports_router._parse_datetime(self.start_date)[0] if self.start_date else None
        end = exports_router._parse_datetime(self.end_date, end_of_date=True)[0] if self.end_date else None
        if start and end and start > end:
            raise ValueError("start_date must be before or equal to end_date.")
        return self


class MemoryFeedbackUpdate(BaseModel):
    rating: Literal["positive", "negative"] | None = None
    feedback: str = Field(default="", max_length=4000)
    reason: str = Field(default="", max_length=255)

    @model_validator(mode="after")
    def validate_feedback(self):
        self.feedback = self.feedback.strip()
        self.reason = self.reason.strip()
        if not self.rating and not self.feedback and not self.reason:
            raise ValueError("A rating, reason, or feedback message is required.")
        return self


def _normalize_categories(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = []
    categories: list[str] = []
    seen: set[str] = set()
    for item in values:
        category = str(item or "").strip()
        key = category.casefold()
        if category and key not in seen:
            seen.add(key)
            categories.append(category)
    return categories


def _normalize_memory(memory: Any) -> dict[str, Any]:
    if not isinstance(memory, Mapping):
        return {}
    result = dict(memory)
    metadata = result.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    for key in (*ENTITY_FIELDS.values(), "project_id", "created_at", "updated_at", "expiration_date"):
        if result.get(key) is None and metadata.get(key) is not None:
            result[key] = metadata.pop(key)
    result["project_id"] = result.get("project_id") or DEFAULT_PROJECT_ID
    result["memory"] = result.get("memory") or result.get("data") or ""
    result["categories"] = _normalize_categories(
        result.get("categories")
        or result.get("category")
        or metadata.pop("categories", None)
        or metadata.pop("category", None)
    )
    result["metadata"] = metadata
    return result


def _all_project_memories(project_id: str) -> list[dict[str, Any]]:
    return exports_router._all_memories(project_id)


def _metadata_value(metadata: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = metadata
    for part in (item.strip() for item in path.split(".")):
        if not part or not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return actual.strip().casefold() == expected.strip().casefold()
    return actual == expected


def _matches_filter(memory: Mapping[str, Any], condition: MemoryFilter) -> bool:
    if condition.field == "entity":
        return _values_equal(memory.get(ENTITY_FIELDS[condition.entity_type or "user"]), condition.value)
    if condition.field == "memory_id":
        return _values_equal(memory.get("id"), condition.value)
    if condition.field == "category":
        return any(_values_equal(category, condition.value) for category in memory.get("categories") or [])
    metadata = memory.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    found, actual = _metadata_value(metadata, condition.key or "")
    return found and _values_equal(actual, condition.value)


def _in_date_range(memory: Mapping[str, Any], start_date: str | None, end_date: str | None) -> bool:
    if not start_date and not end_date:
        return True
    created_at = memory.get("created_at")
    if not created_at:
        return False
    try:
        created = exports_router._parse_datetime(created_at)[0]
        if start_date and created < exports_router._parse_datetime(start_date)[0]:
            return False
        if end_date:
            end, exclusive = exports_router._parse_datetime(end_date, end_of_date=True)
            if (exclusive and created >= end) or (not exclusive and created > end):
                return False
        return True
    except (TypeError, ValueError):
        return False


def _sort_timestamp(memory: Mapping[str, Any]) -> float:
    value = memory.get("created_at")
    if not value:
        return float("-inf")
    try:
        return exports_router._parse_datetime(value)[0].timestamp()
    except (TypeError, ValueError, OverflowError):
        return float("-inf")


def _query_rows(rows: list[dict[str, Any]], query: MemoryQuery) -> dict[str, Any]:
    dated = [row for row in rows if _in_date_range(row, query.start_date, query.end_date)]
    if query.filters:
        predicate = all if query.match == "all" else any
        filtered = [row for row in dated if predicate(_matches_filter(row, condition) for condition in query.filters)]
    else:
        filtered = dated

    category_counts = Counter(category for row in filtered for category in row.get("categories") or [])
    overview_total = len(filtered)
    if query.category:
        filtered = [
            row
            for row in filtered
            if any(_values_equal(category, query.category) for category in row.get("categories") or [])
        ]

    filtered.sort(key=lambda item: (_sort_timestamp(item), str(item.get("id") or "")), reverse=True)
    total = len(filtered)
    start = (query.page - 1) * query.page_size
    results = filtered[start : start + query.page_size]
    return {
        "results": results,
        "total": total,
        "page": query.page,
        "page_size": query.page_size,
        "total_pages": ceil(total / query.page_size) if total else 0,
        "facets": {
            "total": overview_total,
            "categories": [
                {"name": name, "count": count}
                for name, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
            ],
        },
    }


def _feedback_key(project_id: str, memory_id: str) -> str:
    return f"memory_feedback:{project_id}:{memory_id}"


def delete_memory_feedback(project_id: str, memory_id: str) -> bool:
    try:
        with SessionLocal() as db:
            record = db.get(Settings, _feedback_key(project_id, memory_id))
            if record is None:
                return False
            db.delete(record)
            db.commit()
            return True
    except Exception:
        logging.warning("Failed to remove feedback for deleted memory %s.", memory_id, exc_info=True)
        return False


def _get_project_memory(memory_id: str, project_id: str) -> dict[str, Any]:
    try:
        memory = _normalize_memory(get_memory_instance().get(memory_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Memory not found.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="The memory database is unreachable.")
    if not memory or memory.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return memory


def _source_messages(memory: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = memory.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    embedded = metadata.get("source_messages") or metadata.get("messages")
    if isinstance(embedded, list):
        return [dict(message) for message in embedded if isinstance(message, Mapping)]

    scope = "&".join(f"{key}={memory.get(key)}" for key in SESSION_SCOPE_KEYS if memory.get(key))
    if not scope:
        return []
    database = getattr(get_memory_instance(), "db", None)
    getter = getattr(database, "get_last_messages", None)
    if not callable(getter):
        return []
    try:
        source = getter(scope, limit=10)
        return [dict(message) for message in source if isinstance(message, Mapping)]
    except Exception:
        return []


def _set_query_log_context(request: Request, query: MemoryQuery, response: dict[str, Any]) -> None:
    payload = query.model_dump(mode="json", exclude_none=True)
    entities = {
        ENTITY_FIELDS[condition.entity_type or "user"]: str(condition.value)
        for condition in query.filters
        if condition.field == "entity"
    }
    request.state.request_log_event_type = "GET_ALL"
    request.state.request_log_payload = payload
    request.state.request_log_entities = entities
    request.state.request_log_response = {"total": response["total"], "page": response["page"]}
    request.state.request_log_result_count = len(response["results"])


@router.post("/query", summary="Query memories for the dashboard")
def query_memories(
    body: MemoryQuery,
    request: Request,
    _auth=Depends(require_project_read),
):
    try:
        response = _query_rows(_all_project_memories(get_project_id(request)), body)
        _set_query_log_context(request, body, response)
        return response
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="The memory database is unreachable.")


@router.get("/{memory_id}/details", summary="Get dashboard memory details")
def memory_details(
    memory_id: str,
    request: Request,
    _auth=Depends(require_project_read),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    memory = _get_project_memory(memory_id, project_id)
    try:
        history = get_memory_instance().history(memory_id=memory_id)
    except Exception:
        history = []
    return {
        "memory": memory,
        "source": _source_messages(memory),
        "history": history if isinstance(history, list) else [],
        "feedback": get_json(db, _feedback_key(project_id, memory_id), None),
    }


@router.get("/{memory_id}/feedback", summary="Get memory feedback")
def get_memory_feedback(
    memory_id: str,
    request: Request,
    _auth=Depends(require_project_read),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    _get_project_memory(memory_id, project_id)
    return get_json(db, _feedback_key(project_id, memory_id), None)


@router.post("/{memory_id}/feedback", summary="Save memory feedback")
def save_memory_feedback(
    memory_id: str,
    body: MemoryFeedbackUpdate,
    request: Request,
    _auth=Depends(require_project_write),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    _get_project_memory(memory_id, project_id)
    record = {
        **body.model_dump(),
        "memory_id": memory_id,
        "project_id": project_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    set_json(db, _feedback_key(project_id, memory_id), record)
    return {"message": "Feedback recorded", "feedback": record}
