# This file was modified in 2026 by YiQiao contributors. See NOTICE.

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from server.scripts import reembed_memories


def _backup(path: Path, *rows: dict[str, object], collection: str = "memories") -> None:
    records = [
        {
            "kind": "yiqiao-reembed-backup",
            "version": 1,
            "collection": collection,
            "project_id": "default-project",
        },
        *rows,
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_read_backup_rejects_scalar_rows_instead_of_leaking_attribute_error(tmp_path: Path):
    path = tmp_path / "backup.jsonl"
    path.write_text(
        json.dumps({"kind": "yiqiao-reembed-backup", "version": 1, "collection": "memories"}) + "\n42\n",
        encoding="utf-8",
    )

    with pytest.raises(reembed_memories.ReembedUsageError, match="JSON object"):
        reembed_memories._read_backup(path)


def test_read_backup_validates_header_and_duplicate_ids(tmp_path: Path):
    memory_id = "11111111-1111-1111-1111-111111111111"
    path = tmp_path / "backup.jsonl"
    _backup(
        path,
        {"id": memory_id, "vector": "[1,2]"},
        {"id": memory_id, "vector": "[1,2]"},
    )

    with pytest.raises(reembed_memories.ReembedUsageError, match="repeats memory id"):
        reembed_memories._read_backup(path)


def test_parse_vector_rejects_non_finite_values():
    with pytest.raises(ValueError, match="non-finite"):
        reembed_memories._parse_vector("[1e999]")


def test_parse_vector_accepts_null_lexical_only_record():
    assert reembed_memories._parse_vector(None) is None


def test_scope_filters_include_legacy_default_rows():
    assert reembed_memories._scope_filters("default-project", None) == {
        "$or": [
            {"project_id": "default-project"},
            {"$not": [{"project_id": "*"}]},
        ]
    }
    assert reembed_memories._scope_filters("project-a", "user-a") == {
        "project_id": "project-a",
        "user_id": "user-a",
    }


class _Cursor:
    rowcount = 1

    def __init__(self):
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def execute(self, statement: object, parameters: tuple[object, ...]):
        self.calls.append((statement, parameters))


class _Store:
    def __init__(self):
        self.cursor = _Cursor()

    def _col(self):
        from mem0.vector_stores.pgvector import sql

        return sql.Identifier("memories")

    @contextlib.contextmanager
    def _get_cursor(self, *, commit: bool = False):
        yield self.cursor


def test_update_vectors_uses_expected_vector_for_optimistic_guard():
    store = _Store()
    reembed_memories._update_vectors(
        store,
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "vector": [3.0, 4.0],
                "expected_vector": [1.0, 2.0],
            }
        ],
    )

    statement, parameters = store.cursor.calls[0]
    assert "AND vector = %s" in statement.as_string(None)
    assert parameters == ([3.0, 4.0], "11111111-1111-1111-1111-111111111111", [1.0, 2.0])


def test_update_vectors_guards_an_expected_null_vector():
    store = _Store()
    reembed_memories._update_vectors(
        store,
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "vector": [3.0, 4.0],
                "expected_vector": None,
            }
        ],
    )

    statement, parameters = store.cursor.calls[0]
    assert "AND vector IS NULL" in statement.as_string(None)
    assert parameters == ([3.0, 4.0], "11111111-1111-1111-1111-111111111111")


def test_read_backup_requires_collection_header(tmp_path: Path):
    path = tmp_path / "backup.jsonl"
    path.write_text(json.dumps({"kind": "yiqiao-reembed-backup", "version": 1}) + "\n", encoding="utf-8")

    with pytest.raises(reembed_memories.ReembedUsageError, match="no collection"):
        reembed_memories._read_backup(path)
