import json
import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from auth import require_project_read, require_project_write
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from project_scope import DEFAULT_PROJECT_ID, get_project_id
from pydantic import BaseModel, field_validator
from server_state import get_memory_instance
from settings_store import get_json, set_json
from sqlalchemy.orm import Session

router = APIRouter(prefix="/memory-exports", tags=["memory-exports"])

KEY = "memory_exports"
SCROLL_PAGE_SIZE = 1000
MAX_SCHEMA_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 4096
RESERVED = {
    "data",
    "user_id",
    "agent_id",
    "app_id",
    "run_id",
    "hash",
    "project_id",
    "created_at",
    "updated_at",
    "expiration_date",
    "categories",
    "category",
    "metadata",
    "text_lemmatized",
}

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_START_KEYS = ("start", "from", "start_date", "gte")
_END_KEYS = ("end", "to", "end_date", "lte")
_SCHEMA_MARKERS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "definitions",
    "description",
    "enum",
    "examples",
    "format",
    "items",
    "not",
    "oneOf",
    "properties",
    "required",
    "title",
    "type",
}
_SIMPLE_SCHEMA_TYPES = {
    "any",
    "array",
    "bool",
    "boolean",
    "date",
    "datetime",
    "dict",
    "float",
    "int",
    "integer",
    "list",
    "null",
    "number",
    "object",
    "str",
    "string",
    "uuid",
}
_COMPARISON_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "contains", "icontains"}


def _parse_datetime(value: Any, *, end_of_date: bool = False) -> tuple[datetime, bool]:
    is_date_only = isinstance(value, date) and not isinstance(value, datetime)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            raise ValueError("Date range boundaries cannot be empty.")
        is_date_only = bool(_DATE_ONLY.fullmatch(candidate))
        try:
            if is_date_only:
                parsed = datetime.combine(date.fromisoformat(candidate), time.min)
            else:
                if candidate.endswith(("Z", "z")):
                    candidate = f"{candidate[:-1]}+00:00"
                parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise ValueError(f"Invalid ISO 8601 date or datetime: {value!r}.") from exc
    else:
        raise ValueError("Date range boundaries must be ISO 8601 strings.")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    if end_of_date and is_date_only:
        return parsed + timedelta(days=1), True
    return parsed, False


def _range_value(date_range: Mapping[str, Any], keys: tuple[str, ...]) -> Any | None:
    present = [date_range[key] for key in keys if key in date_range and date_range[key] is not None]
    if not present:
        return None
    if any(value != present[0] for value in present[1:]):
        raise ValueError(f"Conflicting date range aliases: {', '.join(keys)}.")
    return present[0]


def _normalize_date_range(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("date_range must be an object.")

    allowed = {*_START_KEYS, *_END_KEYS}
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ValueError(f"Unsupported date_range field(s): {', '.join(unknown)}.")

    start = _range_value(value, _START_KEYS)
    end = _range_value(value, _END_KEYS)
    normalized: dict[str, str] = {}
    if start is not None:
        start_dt, _ = _parse_datetime(start)
        normalized["start"] = str(start)
    else:
        start_dt = None
    if end is not None:
        end_dt, end_is_exclusive = _parse_datetime(end, end_of_date=True)
        normalized["end"] = str(end)
    else:
        end_dt = None
        end_is_exclusive = False
    if start_dt is not None and end_dt is not None and (start_dt > end_dt or (end_is_exclusive and start_dt >= end_dt)):
        raise ValueError("date_range start must not be after end.")
    return normalized


def _validate_json_value(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_SCHEMA_NODES:
        raise ValueError(f"pydantic_schema may contain at most {MAX_SCHEMA_NODES} values.")
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError(f"pydantic_schema may be nested at most {MAX_SCHEMA_DEPTH} levels.")

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("pydantic_schema object keys must be non-empty strings.")
            _validate_json_value(nested, depth=depth + 1, counter=counter)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_json_value(nested, depth=depth + 1, counter=counter)
        return
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("pydantic_schema must contain JSON-compatible values only.")


def _looks_like_json_schema(value: Mapping[str, Any]) -> bool:
    return bool(set(value) & _SCHEMA_MARKERS)


def _validate_schema_descriptor(value: Any, *, path: str) -> None:
    if isinstance(value, str):
        if value.lower() not in _SIMPLE_SCHEMA_TYPES:
            raise ValueError(f"Unsupported type descriptor at {path}: {value!r}.")
        return
    if not isinstance(value, Mapping):
        raise ValueError(f"Schema field {path} must use a JSON Schema object or a supported type name.")

    schema_type = value.get("type")
    if schema_type is not None:
        schema_types = schema_type if isinstance(schema_type, list) else [schema_type]
        if not schema_types or any(
            not isinstance(item, str) or item not in _SIMPLE_SCHEMA_TYPES for item in schema_types
        ):
            raise ValueError(f"Invalid JSON Schema type at {path}.")

    properties = value.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise ValueError(f"JSON Schema properties at {path} must be an object.")
        _validate_schema_properties(properties, path=path)
    elif value and not _looks_like_json_schema(value):
        _validate_schema_properties(value, path=path)

    required = value.get("required")
    if required is not None and (
        not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required)
    ):
        raise ValueError(f"JSON Schema required at {path} must be a list of field names.")

    items = value.get("items")
    if items is not None:
        _validate_schema_descriptor(items, path=f"{path}[]")
    for combinator in ("allOf", "anyOf", "oneOf"):
        options = value.get(combinator)
        if options is not None:
            if not isinstance(options, list) or not options:
                raise ValueError(f"JSON Schema {combinator} at {path} must be a non-empty list.")
            for index, option in enumerate(options):
                _validate_schema_descriptor(option, path=f"{path}.{combinator}[{index}]")


def _validate_schema_properties(properties: Mapping[str, Any], *, path: str = "$.") -> None:
    for field, descriptor in properties.items():
        if not isinstance(field, str) or not field:
            raise ValueError(f"Schema properties at {path} must have non-empty string names.")
        _validate_schema_descriptor(descriptor, path=f"{path}{field}")


def _schema_properties(schema: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if not schema:
        return None
    if "properties" in schema:
        schema_type = schema.get("type")
        schema_types = schema_type if isinstance(schema_type, list) else [schema_type]
        if schema_type is not None and "object" not in schema_types:
            raise ValueError("pydantic_schema must describe an object.")
        properties = schema["properties"]
        if not isinstance(properties, Mapping):
            raise ValueError("pydantic_schema properties must be an object.")
        return properties
    if _looks_like_json_schema(schema):
        raise ValueError("pydantic_schema must describe an object with properties.")
    return schema


def _normalize_schema(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if len(value.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise ValueError(f"pydantic_schema may be at most {MAX_SCHEMA_BYTES} bytes.")
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("pydantic_schema must be a JSON object, not Python code.") from exc
    if not isinstance(value, Mapping):
        raise ValueError("pydantic_schema must be a JSON object.")

    schema = dict(value)
    _validate_json_value(schema)
    properties = _schema_properties(schema)
    if properties is not None:
        _validate_schema_properties(properties)
    return schema


class ExportCreate(BaseModel):
    filters: dict[str, Any] | None = None
    date_range: dict[str, str] | None = None
    pydantic_schema: Any | None = None

    @field_validator("date_range", mode="before")
    @classmethod
    def validate_date_range(cls, value: Any) -> dict[str, str] | None:
        return _normalize_date_range(value)

    @field_validator("pydantic_schema", mode="before")
    @classmethod
    def validate_pydantic_schema(cls, value: Any) -> dict[str, Any] | None:
        return _normalize_schema(value)


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def _normalize_categories(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return list(dict.fromkeys(str(item).strip() for item in values if item is not None and str(item).strip()))


def _serialize(row: Any) -> dict[str, Any]:
    raw_payload = _row_value(row, "payload", {}) or {}
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    metadata: dict[str, Any] = {}
    if isinstance(payload.get("metadata"), Mapping):
        metadata.update(payload["metadata"])
    metadata.update({key: value for key, value in payload.items() if key not in RESERVED})
    categories = _normalize_categories(
        payload.get("categories")
        or payload.get("category")
        or metadata.pop("categories", None)
        or metadata.pop("category", None)
    )
    metadata.pop("categories", None)
    metadata.pop("category", None)
    return {
        "id": _row_value(row, "id"),
        "memory": payload.get("data"),
        "project_id": payload.get("project_id") or metadata.get("project_id") or DEFAULT_PROJECT_ID,
        "user_id": payload.get("user_id"),
        "agent_id": payload.get("agent_id"),
        "app_id": payload.get("app_id"),
        "run_id": payload.get("run_id"),
        "categories": categories,
        "metadata": metadata,
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "expiration_date": payload.get("expiration_date"),
    }


def _normalize_list_result(result: Any) -> tuple[list[Any], Any | None]:
    if result is None:
        return [], None
    if isinstance(result, tuple):
        rows = result[0] if result else []
        cursor = result[1] if len(result) > 1 else None
        return list(rows or []), cursor
    if isinstance(result, list):
        if len(result) == 1 and isinstance(result[0], (list, tuple)):
            return list(result[0]), None
        return result, None
    return list(result), None


def _can_scroll(store: Any) -> bool:
    client = getattr(store, "client", None)
    return (
        callable(getattr(client, "scroll", None))
        and callable(getattr(store, "_create_filter", None))
        and bool(getattr(store, "collection_name", None))
    )


def _scroll_all_rows(store: Any, filters: dict[str, Any] | None) -> list[Any]:
    query_filter = store._create_filter(filters) if filters else None
    rows: list[Any] = []
    offset: Any | None = None
    seen_offsets: set[str] = set()
    while True:
        result = store.client.scroll(
            collection_name=store.collection_name,
            scroll_filter=query_filter,
            limit=SCROLL_PAGE_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        batch, next_offset = _normalize_list_result(result)
        rows.extend(batch)
        if next_offset is None:
            return rows
        marker = repr(next_offset)
        if marker in seen_offsets:
            raise RuntimeError("Vector store returned a repeated scroll cursor while exporting memories.")
        seen_offsets.add(marker)
        offset = next_offset


def _call_store_list(store: Any, filters: dict[str, Any] | None, top_k: int | None) -> Any:
    if filters is None:
        return store.list(top_k=top_k)
    try:
        return store.list(filters=filters, top_k=top_k)
    except TypeError as filtered_error:
        # Some older third-party adapters implement list(top_k=...) only. The
        # export layer still applies its own project check to every returned row.
        try:
            return store.list(top_k=top_k)
        except TypeError:
            raise filtered_error


def _list_all_rows(store: Any, filters: dict[str, Any] | None) -> list[Any]:
    if _can_scroll(store):
        return _scroll_all_rows(store, filters)

    try:
        result = _call_store_list(store, filters, None)
    except TypeError:
        # Older stores that do not implement the base class's top_k=None contract
        # can still expose all rows by accepting progressively larger limits.
        limit = SCROLL_PAGE_SIZE
        previous_count = -1
        while True:
            rows, cursor = _normalize_list_result(_call_store_list(store, filters, limit))
            if cursor is not None:
                raise RuntimeError("Vector store pagination is unavailable for memory export.")
            if len(rows) < limit or len(rows) == previous_count:
                return rows
            previous_count = len(rows)
            limit *= 2

    rows, cursor = _normalize_list_result(result)
    if cursor is not None:
        raise RuntimeError("Vector store pagination is unavailable for memory export.")
    return rows


def _all_memories(project_id: str) -> list[dict[str, Any]]:
    store = get_memory_instance().vector_store
    # Legacy memories without a project_id belong to the default project, so
    # that project must be filtered after retrieval. Other projects can be
    # narrowed by the store and are always checked again below.
    store_filters = None if project_id == DEFAULT_PROJECT_ID else {"project_id": project_id}
    rows = _list_all_rows(store, store_filters)
    return [serialized for row in rows if (serialized := _serialize(row))["project_id"] == project_id]


def _ordered_values(actual: Any, expected: Any) -> tuple[Any, Any]:
    if isinstance(actual, str) and isinstance(expected, str):
        try:
            actual_dt, _ = _parse_datetime(actual)
            expected_dt, _ = _parse_datetime(expected)
            return actual_dt, expected_dt
        except ValueError:
            pass
    return actual, expected


def _match_condition(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        operators = {str(key).lstrip("$").lower() for key in expected}
        if operators and operators <= _COMPARISON_OPERATORS:
            for raw_operator, operand in expected.items():
                operator = str(raw_operator).lstrip("$").lower()
                left, right = _ordered_values(actual, operand)
                try:
                    if operator == "eq" and left != right:
                        return False
                    if operator == "ne" and left == right:
                        return False
                    if operator == "gt" and not left > right:
                        return False
                    if operator == "gte" and not left >= right:
                        return False
                    if operator == "lt" and not left < right:
                        return False
                    if operator == "lte" and not left <= right:
                        return False
                    if operator == "in" and left not in right:
                        return False
                    if operator == "nin" and left in right:
                        return False
                    if operator == "contains" and right not in left:
                        return False
                    if operator == "icontains" and str(right).casefold() not in str(left).casefold():
                        return False
                except (TypeError, ValueError):
                    return False
            return True
        if not isinstance(actual, Mapping):
            return False
        return all(key in actual and _match_condition(actual[key], value) for key, value in expected.items())
    return actual == expected


def _flat_filter(mem: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, value in filters.items():
        logical_key = key.lstrip("$").upper()
        if logical_key == "AND":
            if not isinstance(value, list) or not all(
                isinstance(item, Mapping) and _flat_filter(mem, dict(item)) for item in value
            ):
                return False
            continue
        if logical_key == "OR":
            if not isinstance(value, list) or not any(
                isinstance(item, Mapping) and _flat_filter(mem, dict(item)) for item in value
            ):
                return False
            continue

        if key == "metadata":
            actual = mem.get("metadata") or {}
        else:
            actual = mem.get(key)
        if not _match_condition(actual, value):
            return False
    return True


def _date_filter(mem: dict[str, Any], date_range: dict[str, str] | None) -> bool:
    if not date_range:
        return True
    created_at = mem.get("created_at")
    if created_at is None:
        return False
    try:
        created, _ = _parse_datetime(created_at)
        start_value = _range_value(date_range, _START_KEYS)
        end_value = _range_value(date_range, _END_KEYS)
        if start_value is not None:
            start, _ = _parse_datetime(start_value)
            if created < start:
                return False
        if end_value is not None:
            end, end_is_exclusive = _parse_datetime(end_value, end_of_date=True)
            if (end_is_exclusive and created >= end) or (not end_is_exclusive and created > end):
                return False
        return True
    except ValueError:
        return False


def _nested_properties(descriptor: Any) -> Mapping[str, Any] | None:
    if not isinstance(descriptor, Mapping):
        return None
    properties = descriptor.get("properties")
    if isinstance(properties, Mapping):
        return properties
    if descriptor and not _looks_like_json_schema(descriptor):
        return descriptor
    for combinator in ("allOf", "anyOf", "oneOf"):
        options = descriptor.get(combinator)
        if isinstance(options, list):
            for option in options:
                nested = _nested_properties(option)
                if nested is not None:
                    return nested
    return None


def _project_value(value: Any, descriptor: Any) -> Any:
    properties = _nested_properties(descriptor)
    if properties is not None and isinstance(value, Mapping):
        return {key: _project_value(value[key], nested) for key, nested in properties.items() if key in value}
    if isinstance(descriptor, Mapping) and isinstance(value, list) and "items" in descriptor:
        return [_project_value(item, descriptor["items"]) for item in value]
    return value


def _project_memory(memory: dict[str, Any], schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if not schema:
        return memory
    properties = _schema_properties(schema)
    if properties is None:
        return memory
    return {key: _project_value(memory[key], descriptor) for key, descriptor in properties.items() if key in memory}


def _public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key != "result"}


def _matches_job_search(job: Mapping[str, Any], search: str) -> bool:
    needle = search.strip().casefold()
    if not needle:
        return True
    entity = json.dumps(job.get("entity") or {}, sort_keys=True, default=str)
    return needle in str(job.get("id") or "").casefold() or needle in entity.casefold()


@router.get("")
def list_exports(
    request: Request,
    page: int | None = Query(default=None, ge=1),
    page_size: int | None = Query(default=None, ge=1, le=100),
    search: str | None = Query(default=None, max_length=200),
    _auth=Depends(require_project_read),
    db: Session = Depends(get_db),
):
    project_id = get_project_id(request)
    jobs = [
        _public_job(job)
        for job in get_json(db, KEY, [])
        if job.get("project_id") == project_id and _matches_job_search(job, search or "")
    ]

    # Keep the original array response for API clients that predate list
    # pagination. The dashboard opts into the paginated response explicitly.
    if page is None and page_size is None and search is None:
        return jobs

    resolved_page = page or 1
    resolved_page_size = page_size or 20
    total = len(jobs)
    start = (resolved_page - 1) * resolved_page_size
    items = jobs[start : start + resolved_page_size]
    total_pages = (total + resolved_page_size - 1) // resolved_page_size
    return {
        "items": items,
        "total": total,
        "page": resolved_page,
        "page_size": resolved_page_size,
        "total_pages": total_pages,
        "has_next": resolved_page < total_pages,
        "has_previous": resolved_page > 1 and total > 0,
    }


@router.post("", status_code=201)
def create_export(
    body: ExportCreate, request: Request, _auth=Depends(require_project_write), db: Session = Depends(get_db)
):
    project_id = get_project_id(request)
    started = datetime.now(timezone.utc).isoformat()
    filtered_memories = [
        memory
        for memory in _all_memories(project_id)
        if _flat_filter(memory, body.filters or {}) and _date_filter(memory, body.date_range)
    ]
    memories = [_project_memory(memory, body.pydantic_schema) for memory in filtered_memories]
    job = {
        "id": str(uuid.uuid4()),
        "project_id": project_id,
        "status": "completed",
        "entity": body.filters or {},
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "filters": body.filters or {},
        "date_range": body.date_range,
        "pydantic_schema": body.pydantic_schema,
        "result": {"exported_at": started, "total": len(memories), "memories": memories},
    }
    jobs = [job, *get_json(db, KEY, [])][:50]
    set_json(db, KEY, jobs)
    return _public_job(job)


@router.get("/{export_id}")
def get_export(export_id: str, request: Request, _auth=Depends(require_project_read), db: Session = Depends(get_db)):
    project_id = get_project_id(request)
    for job in get_json(db, KEY, []):
        if job["id"] == export_id and job.get("project_id") == project_id:
            return job
    raise HTTPException(status_code=404, detail="Memory export not found.")
