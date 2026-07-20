from copy import deepcopy

from scripts.verify_chat_import_consistency import (
    REPORT_SCHEMA,
    _fetch_all,
    build_report,
    compare_idempotency,
    deterministic_memory_id,
    fingerprint_ids,
)


def _snapshot():
    project_id = "benchmark-tiered"
    entities = {"user_id": "benchmark-user"}
    facts = [
        ("chunk-1", "conversation-1", "a" * 32),
        ("chunk-2", "conversation-2", "b" * 32),
    ]
    memory_ids = [
        deterministic_memory_id(project_id, entities, conversation_id, memory_hash)
        for _chunk_id, conversation_id, memory_hash in facts
    ]
    chunks = []
    manifests = []
    hashes = []
    graph_items = []
    vector_rows = []
    history_rows = []
    neo4j_nodes = []
    for index, ((chunk_id, conversation_id, memory_hash), memory_id) in enumerate(zip(facts, memory_ids, strict=True)):
        import_key = f"import-key-{index}"
        chunks.append(
            {
                "id": chunk_id,
                "import_key": import_key,
                "conversation_id": conversation_id,
                "status": "succeeded",
                "attempt": 1,
                "retry_count": 0,
                "chunk_index": 0,
                "chunk_count": 1,
                "source_message_indices": [index * 2, index * 2 + 1],
                "core_source_message_indices": [index * 2, index * 2 + 1],
                "source_message_start": index * 2,
                "source_message_end": index * 2 + 1,
                "source_turn_start": index * 2,
                "source_turn_end": index * 2 + 1,
                "parent_import_key": None,
                "split_depth": 0,
                "overlap_turns": 0,
                "source_path": f"chat-{index}.md",
                "memory_ids": [memory_id],
                "claimed_memory_hashes": [memory_hash],
                "error_type": None,
            }
        )
        manifests.append(
            {
                "id": f"manifest-{index}",
                "import_key": import_key,
                "job_id": "job-1",
                "chunk_id": chunk_id,
                "status": "succeeded",
                "attempts": 1,
                "memory_ids": [memory_id],
            }
        )
        hashes.append(
            {
                "id": f"hash-{index}",
                "conversation_id": f"scoped-{conversation_id}",
                "memory_hash": memory_hash,
                "job_id": "job-1",
                "chunk_id": chunk_id,
                "status": "succeeded",
                "memory_id": memory_id,
                "raw_conversation_id": conversation_id,
            }
        )
        graph_items.append(
            {
                "id": f"graph-{index}",
                "chunk_id": chunk_id,
                "item_key": f"item-{index}",
                "memory_id": memory_id,
                "payload": {"memory_id": memory_id, "project_id": project_id},
                "status": "synced",
                "attempts": 1,
            }
        )
        vector_rows.append(
            {
                "id": memory_id,
                "vector": "[0.1,0.2]",
                "payload": {"project_id": project_id, "hash": memory_hash},
                "dimensions": 2,
            }
        )
        history_rows.append(
            {
                "id": f"history-{index}",
                "memory_id": memory_id,
                "old_memory": None,
                "new_memory": f"memory-{index}",
                "event": "ADD",
                "created_at": "2026-07-14T00:00:00+00:00",
                "updated_at": None,
                "is_deleted": 0,
                "actor_id": None,
                "role": None,
                "project_id": project_id,
            }
        )
        neo4j_nodes.append(
            {
                "id": memory_id,
                "labels": ["Memory"],
                "properties": {"id": memory_id, "project_id": project_id},
            }
        )
    return {
        "app": {
            "job": {
                "id": "job-1",
                "project_id": project_id,
                "entities": entities,
                "status": "completed",
                "graph_status": "completed",
                "total_chunks": 2,
                "processed_chunks": 2,
                "imported_chunks": 2,
                "skipped_chunks": 0,
                "failed_chunks": 0,
                "split_chunks": 0,
                "memories_created": 2,
            },
            "chunks": chunks,
            "manifests": manifests,
            "hashes": hashes,
            "graph_items": graph_items,
        },
        "vector_rows": vector_rows,
        "history_rows": history_rows,
        "neo4j_nodes": neo4j_nodes,
        "neo4j_relationships": [],
    }


def _split_snapshot():
    snapshot = _snapshot()
    app = snapshot["app"]
    project_id = app["job"]["project_id"]
    entities = app["job"]["entities"]
    parent_key = "split-parent-key"
    first_child = app["chunks"][0]
    first_child.update(
        chunk_index=0,
        chunk_count=2,
        source_message_indices=[0],
        core_source_message_indices=[0],
        source_message_start=0,
        source_message_end=0,
        source_turn_start=0,
        source_turn_end=0,
        parent_import_key=parent_key,
        split_depth=1,
    )
    parent = {
        "id": "split-parent-chunk",
        "import_key": parent_key,
        "conversation_id": first_child["conversation_id"],
        "status": "split",
        "attempt": 3,
        "retry_count": 2,
        "chunk_index": 0,
        "chunk_count": 1,
        "source_message_indices": [0, 1],
        "core_source_message_indices": [0, 1],
        "source_message_start": 0,
        "source_message_end": 1,
        "source_turn_start": 0,
        "source_turn_end": 1,
        "parent_import_key": None,
        "split_depth": 0,
        "overlap_turns": 0,
        "source_path": first_child["source_path"],
        "memory_ids": [],
        "claimed_memory_hashes": [],
        "error_type": "adaptive_split",
    }
    memory_hash = "c" * 32
    memory_id = deterministic_memory_id(
        project_id,
        entities,
        first_child["conversation_id"],
        memory_hash,
    )
    second_child = {
        "id": "split-child-chunk",
        "import_key": "split-child-key",
        "conversation_id": first_child["conversation_id"],
        "status": "succeeded",
        "attempt": 1,
        "retry_count": 0,
        "chunk_index": 1,
        "chunk_count": 2,
        "source_message_indices": [1],
        "core_source_message_indices": [1],
        "source_message_start": 1,
        "source_message_end": 1,
        "source_turn_start": 1,
        "source_turn_end": 1,
        "parent_import_key": parent_key,
        "split_depth": 1,
        "overlap_turns": 0,
        "source_path": first_child["source_path"],
        "memory_ids": [memory_id],
        "claimed_memory_hashes": [memory_hash],
        "error_type": None,
    }
    app["chunks"] = [parent, *app["chunks"], second_child]
    app["manifests"].extend(
        [
            {
                "id": "split-parent-manifest",
                "import_key": parent_key,
                "job_id": "job-1",
                "chunk_id": parent["id"],
                "status": "split",
                "attempts": 1,
                "memory_ids": [],
            },
            {
                "id": "split-child-manifest",
                "import_key": second_child["import_key"],
                "job_id": "job-1",
                "chunk_id": second_child["id"],
                "status": "succeeded",
                "attempts": 1,
                "memory_ids": [memory_id],
            },
        ]
    )
    app["hashes"].append(
        {
            "id": "split-child-hash",
            "conversation_id": "scoped-conversation-1",
            "memory_hash": memory_hash,
            "job_id": "job-1",
            "chunk_id": second_child["id"],
            "status": "succeeded",
            "memory_id": memory_id,
            "raw_conversation_id": second_child["conversation_id"],
        }
    )
    app["graph_items"].append(
        {
            "id": "split-child-graph",
            "chunk_id": second_child["id"],
            "item_key": "split-child-item",
            "memory_id": memory_id,
            "payload": {"memory_id": memory_id, "project_id": project_id},
            "status": "synced",
            "attempts": 1,
        }
    )
    snapshot["vector_rows"].append(
        {
            "id": memory_id,
            "vector": "[0.1,0.2]",
            "payload": {"project_id": project_id, "hash": memory_hash},
            "dimensions": 2,
        }
    )
    snapshot["history_rows"].append(
        {
            "id": "split-child-history",
            "memory_id": memory_id,
            "old_memory": None,
            "new_memory": "split child memory",
            "event": "ADD",
            "created_at": "2026-07-14T00:00:00+00:00",
            "updated_at": None,
            "is_deleted": 0,
            "actor_id": None,
            "role": None,
            "project_id": project_id,
        }
    )
    snapshot["neo4j_nodes"].append(
        {
            "id": memory_id,
            "labels": ["Memory"],
            "properties": {"id": memory_id, "project_id": project_id},
        }
    )
    app["job"].update(
        total_chunks=3,
        processed_chunks=3,
        imported_chunks=3,
        split_chunks=1,
        memories_created=3,
    )
    return snapshot


def test_cross_store_report_passes_and_uses_one_canonical_id_fingerprint():
    snapshot = _snapshot()
    report = build_report(snapshot)

    assert report["schema"] == REPORT_SCHEMA
    assert report["result"] == "passed"
    assert report["exact_id_set"]["all_store_sets_equal"] is True
    fingerprints = {store["sha256"] for store in report["stores"].values()}
    assert fingerprints == {report["snapshot_fingerprints"]["canonical_memory_id_set_sha256"]}
    assert report["idempotency"] == {
        "deterministic_uuid5_mismatches": 0,
        "unresolved_hash_chunk_links": 0,
        "claimed_hash_set_equals_hash_table_set": True,
    }


def test_cross_store_report_accepts_retained_split_parent_tree():
    report = build_report(_split_snapshot())

    assert report["result"] == "passed"
    assert report["row_counts"]["chunks"] == 4
    assert report["row_counts"]["leaf_chunks"] == 3
    assert report["row_counts"]["split_parent_chunks"] == 1
    assert report["row_counts"]["split_state_parent_manifests"] == 1
    assert report["row_counts"]["legacy_released_split_parent_manifests"] == 0
    assert not any(report["split_tree"].values())
    assert not any(report["manifest_alignment"].values())


def test_cross_store_report_accepts_legacy_released_split_manifest():
    snapshot = _split_snapshot()
    snapshot["app"]["manifests"][-2]["status"] = "released"

    report = build_report(snapshot)

    assert report["result"] == "passed"
    assert report["row_counts"]["split_state_parent_manifests"] == 0
    assert report["row_counts"]["legacy_released_split_parent_manifests"] == 1


def test_cross_store_report_rejects_incomplete_split_tree():
    snapshot = _split_snapshot()
    snapshot["app"]["chunks"][-1]["parent_import_key"] = None

    report = build_report(snapshot)

    assert report["result"] == "failed"
    assert report["split_tree"]["split_child_count_mismatches"] == 1


def test_cross_store_report_rejects_split_core_omission():
    snapshot = _split_snapshot()
    snapshot["app"]["chunks"][-1].update(
        core_source_message_indices=[],
        source_turn_start=None,
        source_turn_end=None,
    )

    report = build_report(snapshot)

    assert report["result"] == "failed"
    assert report["split_tree"]["core_coverage_mismatches"] == 1


def test_cross_store_report_rejects_split_core_duplication():
    snapshot = _split_snapshot()
    snapshot["app"]["chunks"][-1].update(
        source_message_indices=[0, 1],
        core_source_message_indices=[0],
        source_message_start=0,
        source_message_end=1,
        source_turn_start=0,
        source_turn_end=0,
    )

    report = build_report(snapshot)

    assert report["result"] == "failed"
    assert report["split_tree"]["core_duplication_mismatches"] == 1
    assert report["split_tree"]["overlap_on_parent_core_mismatches"] == 1


def test_cross_store_report_fails_when_history_is_missing_an_id():
    snapshot = _snapshot()
    snapshot["history_rows"].pop()

    report = build_report(snapshot)

    assert report["result"] == "failed"
    assert report["exact_id_set"]["all_store_sets_equal"] is False
    assert report["exact_id_set"]["differences"]["sqlite_history_add_rows"] == {
        "missing_from_pgvector": 0,
        "missing_from_store": 1,
    }


def test_same_project_rerun_requires_skip_only_job_and_unchanged_fingerprints():
    baseline = build_report(_snapshot())
    current = build_report(_snapshot())
    rerun = {
        "job": {
            "id": "job-2",
            "project_id": "benchmark-tiered",
            "status": "completed",
            "total_chunks": 2,
            "processed_chunks": 2,
            "imported_chunks": 0,
            "skipped_chunks": 2,
            "failed_chunks": 0,
            "memories_created": 0,
            "graph_status": "skipped",
        },
        "chunks": [
            {"import_key": "import-key-0", "status": "skipped", "memory_ids": []},
            {"import_key": "import-key-1", "status": "skipped", "memory_ids": []},
        ],
    }

    compared = compare_idempotency(current, baseline, rerun)

    assert compared["result"] == "passed"
    assert compared["same_project_idempotency"]["result"] == "passed"
    assert compared["same_project_idempotency"]["changed_fingerprints"] == []

    drifted = deepcopy(current)
    drifted["snapshot_fingerprints"]["pgvector_rows_sha256"] = "0" * 64
    compared = compare_idempotency(drifted, baseline, rerun)
    assert compared["result"] == "failed"
    assert compared["same_project_idempotency"]["changed_fingerprints"] == ["pgvector_rows_sha256"]


def test_same_project_rerun_matches_split_baseline_leaf_keys_only():
    baseline = build_report(_split_snapshot())
    current = build_report(_split_snapshot())
    leaf_topology = [
        ("import-key-0", "split-parent-key", 1),
        ("import-key-1", None, 0),
        ("split-child-key", "split-parent-key", 1),
    ]
    rerun = {
        "job": {
            "id": "job-2",
            "project_id": "benchmark-tiered",
            "status": "completed",
            "total_chunks": 3,
            "processed_chunks": 3,
            "imported_chunks": 0,
            "skipped_chunks": 3,
            "failed_chunks": 0,
            "split_chunks": 0,
            "memories_created": 0,
            "graph_status": "skipped",
        },
        "chunks": [
            {
                "import_key": import_key,
                "status": "skipped",
                "memory_ids": [],
                "parent_import_key": parent_import_key,
                "split_depth": split_depth,
            }
            for import_key, parent_import_key, split_depth in leaf_topology
        ],
    }

    compared = compare_idempotency(current, baseline, rerun)

    assert compared["result"] == "passed"
    assert compared["same_project_idempotency"]["checks"]["leaf_import_keys_match"] is True
    assert compared["same_project_idempotency"]["checks"]["leaf_topology_matches"] is True

    rerun["chunks"][-1]["import_key"] = "wrong-leaf-key"
    compared = compare_idempotency(build_report(_split_snapshot()), baseline, rerun)
    assert compared["result"] == "failed"
    assert compared["same_project_idempotency"]["checks"]["leaf_import_keys_match"] is False

    rerun["chunks"][-1]["import_key"] = "split-child-key"
    rerun["chunks"][-1]["parent_import_key"] = None
    compared = compare_idempotency(build_report(_split_snapshot()), baseline, rerun)
    assert compared["result"] == "failed"
    assert compared["same_project_idempotency"]["checks"]["leaf_import_keys_match"] is True
    assert compared["same_project_idempotency"]["checks"]["leaf_topology_matches"] is False


def test_id_fingerprint_is_order_independent_and_lowercase():
    assert fingerprint_ids(["B", "a", "A"]) == fingerprint_ids(["a", "b"])


def test_fetch_all_accepts_tuple_style_cursor_descriptions():
    class Cursor:
        description = [("id",), ("status",)]

        def execute(self, _query, _params):
            return None

        def fetchall(self):
            return [("row-1", "succeeded")]

    assert _fetch_all(Cursor(), "SELECT 1") == [{"id": "row-1", "status": "succeeded"}]
