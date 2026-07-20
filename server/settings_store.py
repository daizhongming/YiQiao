import json
from copy import deepcopy
from typing import Any

from models import Settings
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_json(db: Session, key: str, default: Any) -> Any:
    row = db.get(Settings, key)
    if row is None:
        return deepcopy(default)
    try:
        return deep_merge(default, json.loads(row.value)) if isinstance(default, dict) else json.loads(row.value)
    except json.JSONDecodeError:
        return deepcopy(default)


def set_json(db: Session, key: str, value: Any, *, commit: bool = True) -> Any:
    serialized = json.dumps(value)
    stmt = (
        insert(Settings)
        .values(key=key, value=serialized)
        .on_conflict_do_update(index_elements=[Settings.key], set_={"value": serialized})
    )
    db.execute(stmt)
    if commit:
        db.commit()
    return value
