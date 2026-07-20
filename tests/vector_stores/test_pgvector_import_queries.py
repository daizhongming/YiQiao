from unittest.mock import MagicMock

from mem0.vector_stores.pgvector import PGVector


def _store_with_rows(rows):
    store = object.__new__(PGVector)
    store.collection_name = "memories"
    store._ensure_collection = MagicMock()

    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    store._get_cursor = MagicMock(return_value=cursor_context)
    return store, cursor


def test_existing_payload_values_binds_select_candidate_and_scope_parameters_in_order():
    store, cursor = _store_with_rows([("import-1",), ("import-2",)])

    result = store.existing_payload_values(
        "import_key",
        ["import-1", "import-2", "import-1"],
        filters={"project_id": "project-1"},
    )

    assert result == {"import-1", "import-2"}
    assert cursor.execute.call_args.args[1] == (
        "import_key",
        "import_key",
        ["import-1", "import-2"],
        "project_id",
        "project-1",
    )


def test_existing_memory_hashes_binds_project_scope_and_returns_identity_map():
    store, cursor = _store_with_rows(
        [
            ("memory-1", "conversation-1", "hash-1"),
            ("memory-ignored", None, "hash-ignored"),
        ]
    )

    result = store.existing_memory_hashes(filters={"project_id": "project-1"})

    assert result == {("conversation-1", "hash-1"): "memory-1"}
    assert cursor.execute.call_args.args[1] == ("project_id", "project-1")


def test_atomic_payload_list_union_uses_one_row_locked_jsonb_update():
    store, cursor = _store_with_rows([])
    cursor.fetchone.return_value = ({"linked_memory_ids": ["memory-1", "memory-2"]},)

    updated = store.atomic_payload_list_union(
        vector_id="00000000-0000-0000-0000-000000000001",
        key="linked_memory_ids",
        values=["memory-2", "memory-1", "memory-2"],
    )

    assert updated is True
    store._get_cursor.assert_called_once_with(commit=True)
    query = str(cursor.execute.call_args.args[0]).lower()
    assert "update" in query
    assert "jsonb_set" in query
    assert "jsonb_array_elements_text" in query
    assert "union all" in query
    assert "select distinct" in query
    assert "order by" in query
    assert "returning" in query
    assert cursor.execute.call_args.args[1] == (
        ["linked_memory_ids"],
        "linked_memory_ids",
        "linked_memory_ids",
        ["memory-1", "memory-2"],
        "00000000-0000-0000-0000-000000000001",
    )


def test_atomic_payload_list_union_reports_missing_row_without_read_modify_write():
    store, cursor = _store_with_rows([])
    cursor.fetchone.return_value = None

    updated = store.atomic_payload_list_union(
        vector_id="00000000-0000-0000-0000-000000000001",
        key="linked_memory_ids",
        values=["memory-1"],
    )

    assert updated is False
    assert cursor.execute.call_count == 1
