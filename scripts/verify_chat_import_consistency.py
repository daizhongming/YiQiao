#!/usr/bin/env python3
"""Read-only cross-store verification for a completed YiQiao chat import.

The checker emits aggregate counts and SHA-256 fingerprints only. It never writes to
PostgreSQL, SQLite, Neo4j, or the benchmark output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

REPORT_SCHEMA = "yiqiao.chat-import-consistency/v2"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENTITY_KEYS = ("user_id", "agent_id", "app_id", "run_id")
_TERMINAL_JOB_STATUSES = {"completed", "completed_with_errors"}


def _json_default(value: Any) -> Any:
    for method_name in ("iso_format", "isoformat"):
        method = getattr(value, method_name, None)
        if callable(method):
            return method()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def fingerprint_rows(rows: Iterable[Any]) -> str:
    lines = sorted(canonical_json(row) for row in rows)
    material = "".join(f"{line}\n" for line in lines).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def canonical_id_values(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip().lower() for value in values if str(value).strip()})


def fingerprint_ids(values: Iterable[Any]) -> str:
    material = "".join(f"{value}\n" for value in canonical_id_values(values)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def summarize_ids(values: Iterable[Any]) -> dict[str, Any]:
    raw = [str(value).strip().lower() for value in values if str(value).strip()]
    distinct = sorted(set(raw))
    invalid_uuid_count = 0
    for value in distinct:
        try:
            uuid.UUID(value)
        except ValueError:
            invalid_uuid_count += 1
    return {
        "total": len(raw),
        "distinct": len(distinct),
        "duplicates": len(raw) - len(distinct),
        "invalid_uuid_count": invalid_uuid_count,
        "sha256": fingerprint_ids(distinct),
    }


def _flatten(rows: Iterable[Mapping[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for row in rows:
        raw = row.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(str(value) for value in raw if value)
    return values


def _canonical_entity_scope(entities: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(entities[key]).strip()
        for key in _ENTITY_KEYS
        if entities.get(key) is not None and str(entities[key]).strip()
    }


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def deterministic_memory_id(
    project_id: str,
    entities: Mapping[str, Any],
    conversation_id: str,
    memory_hash: str,
) -> str:
    scope_hash = _stable_hash(_canonical_entity_scope(entities))
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"yiqiao:{project_id}:{scope_hash}:{conversation_id}:{memory_hash}",
        )
    )


def _business_rows(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    return [{key: row.get(key) for key in keys} for row in rows]


def _chunk_topology_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "import_key": str(row.get("import_key") or ""),
            "parent_import_key": (
                str(row["parent_import_key"])
                if row.get("parent_import_key") is not None and str(row["parent_import_key"]).strip()
                else None
            ),
            "split_depth": int(row.get("split_depth") or 0),
        }
        for row in rows
    ]


def _provenance_indices(row: Mapping[str, Any], key: str) -> tuple[list[int], bool]:
    raw = row.get(key)
    if not isinstance(raw, (list, tuple)):
        return [], True
    values: list[int] = []
    invalid = False
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int):
            invalid = True
            continue
        values.append(value)
    if len(values) != len(set(values)):
        invalid = True
    return values, invalid


def build_report(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    app = snapshot["app"]
    job = app["job"]
    chunks = list(app["chunks"])
    manifests = list(app["manifests"])
    hashes = list(app["hashes"])
    graph_items = list(app["graph_items"])
    vector_rows = list(snapshot["vector_rows"])
    history_rows = list(snapshot["history_rows"])
    neo4j_nodes = list(snapshot["neo4j_nodes"])
    neo4j_relationships = list(snapshot["neo4j_relationships"])

    split_chunks = [row for row in chunks if row.get("status") == "split"]
    leaf_chunks = [row for row in chunks if row.get("status") != "split"]
    successful_chunks = [row for row in leaf_chunks if row.get("status") == "succeeded"]
    skipped_chunks = [row for row in leaf_chunks if row.get("status") == "skipped"]
    failed_chunks = [row for row in leaf_chunks if row.get("status") == "failed"]
    successful_manifests = [row for row in manifests if row.get("status") == "succeeded"]
    successful_hashes = [row for row in hashes if row.get("status") == "succeeded" and row.get("memory_id")]
    synced_graph_items = [row for row in graph_items if row.get("status") == "synced" and row.get("memory_id")]
    active_history = [row for row in history_rows if row.get("event") == "ADD" and int(row.get("is_deleted") or 0) == 0]
    memory_nodes = [row for row in neo4j_nodes if "Memory" in set(row.get("labels") or [])]

    chunk_keys = [str(row.get("import_key") or "") for row in chunks]
    manifest_keys = [str(row.get("import_key") or "") for row in manifests]
    chunk_key_counts = Counter(chunk_keys)
    manifest_key_counts = Counter(manifest_keys)
    missing_chunk_import_keys = chunk_key_counts.get("", 0)
    duplicate_chunk_import_keys = sum(max(0, count - 1) for key, count in chunk_key_counts.items() if key)
    missing_manifest_import_keys = manifest_key_counts.get("", 0)
    duplicate_manifest_import_keys = sum(max(0, count - 1) for key, count in manifest_key_counts.items() if key)
    chunk_by_key = {key: row for key, row in zip(chunk_keys, chunks, strict=True) if key}
    manifest_by_key = {key: row for key, row in zip(manifest_keys, manifests, strict=True) if key}
    children_by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in chunks:
        parent_key = row.get("parent_import_key")
        if parent_key is not None and str(parent_key).strip():
            children_by_parent[str(parent_key)].append(row)
    missing_parent_links = 0
    non_split_parent_links = 0
    parent_depth_mismatches = 0
    parent_conversation_mismatches = 0
    orphan_split_depth_rows = 0
    for row in chunks:
        raw_parent_key = row.get("parent_import_key")
        if raw_parent_key is None or not str(raw_parent_key).strip():
            if int(row.get("split_depth") or 0) != 0:
                orphan_split_depth_rows += 1
            continue
        parent = chunk_by_key.get(str(raw_parent_key))
        if parent is None:
            missing_parent_links += 1
            continue
        if parent.get("status") != "split":
            non_split_parent_links += 1
        if int(row.get("split_depth") or 0) != int(parent.get("split_depth") or 0) + 1:
            parent_depth_mismatches += 1
        if str(row.get("conversation_id")) != str(parent.get("conversation_id")):
            parent_conversation_mismatches += 1
    split_child_count_mismatches = sum(
        len(children_by_parent.get(str(row.get("import_key") or ""), [])) != 2 for row in split_chunks
    )
    split_parent_state_rows = sum(
        bool(row.get("memory_ids") or row.get("claimed_memory_hashes")) for row in split_chunks
    )

    provenance: dict[str, tuple[set[int], set[int]]] = {}
    invalid_provenance_rows = 0
    core_outside_source_rows = 0
    message_boundary_mismatches = 0
    turn_boundary_mismatches = 0
    chunk_position_mismatches = 0
    missing_source_path_rows = 0
    for import_key, row in chunk_by_key.items():
        source_values, invalid_source = _provenance_indices(row, "source_message_indices")
        core_values, invalid_core = _provenance_indices(row, "core_source_message_indices")
        source_indices = set(source_values)
        core_indices = set(core_values)
        provenance[import_key] = (source_indices, core_indices)
        if invalid_source or invalid_core or not source_indices or not core_indices:
            invalid_provenance_rows += 1
        if not core_indices.issubset(source_indices):
            core_outside_source_rows += 1
        expected_message_bounds = (min(source_indices), max(source_indices)) if source_indices else (None, None)
        observed_message_bounds = (
            row.get("source_message_start"),
            row.get("source_message_end"),
        )
        if observed_message_bounds != expected_message_bounds:
            message_boundary_mismatches += 1
        expected_turn_bounds = (min(core_indices), max(core_indices)) if core_indices else (None, None)
        observed_turn_bounds = (row.get("source_turn_start"), row.get("source_turn_end"))
        if observed_turn_bounds != expected_turn_bounds:
            turn_boundary_mismatches += 1
        chunk_index = int(row.get("chunk_index") or 0)
        chunk_count = int(row.get("chunk_count") or 0)
        if chunk_index < 0 or chunk_count < 1 or chunk_index >= chunk_count:
            chunk_position_mismatches += 1
        if not str(row.get("source_path") or "").strip():
            missing_source_path_rows += 1

    core_coverage_mismatches = 0
    core_duplication_mismatches = 0
    core_outside_parent_mismatches = 0
    source_coverage_mismatches = 0
    source_outside_parent_mismatches = 0
    overlap_on_parent_core_mismatches = 0
    split_child_position_mismatches = 0
    split_source_path_mismatches = 0
    for parent in split_chunks:
        parent_key = str(parent.get("import_key") or "")
        children = children_by_parent.get(parent_key, [])
        if len(children) != 2 or parent_key not in provenance:
            continue
        parent_source, parent_core = provenance[parent_key]
        child_provenance = [provenance.get(str(child.get("import_key") or ""), (set(), set())) for child in children]
        child_sources = [item[0] for item in child_provenance]
        child_cores = [item[1] for item in child_provenance]
        if set().union(*child_cores) != parent_core:
            core_coverage_mismatches += 1
        if child_cores[0] & child_cores[1]:
            core_duplication_mismatches += 1
        if any(not child_core.issubset(parent_core) for child_core in child_cores):
            core_outside_parent_mismatches += 1
        if set().union(*child_sources) != parent_source:
            source_coverage_mismatches += 1
        if any(not child_source.issubset(parent_source) for child_source in child_sources):
            source_outside_parent_mismatches += 1
        if (child_sources[0] & child_sources[1]) & parent_core:
            overlap_on_parent_core_mismatches += 1
        if sorted(int(child.get("chunk_index") or 0) for child in children) != [0, 1] or any(
            int(child.get("chunk_count") or 0) != 2 for child in children
        ):
            split_child_position_mismatches += 1
        if any(str(child.get("source_path") or "") != str(parent.get("source_path") or "") for child in children):
            split_source_path_mismatches += 1

    chunk_key_set = {key for key in chunk_keys if key}
    manifest_key_set = {key for key in manifest_keys if key}
    manifest_owner_mismatches = 0
    split_manifest_status_mismatches = 0
    split_manifest_memory_rows = 0
    leaf_manifest_status_mismatches = 0
    leaf_manifest_memory_id_mismatches = 0
    split_state_manifests = 0
    legacy_released_split_manifests = 0
    for import_key in chunk_key_set & manifest_key_set:
        chunk = chunk_by_key[import_key]
        manifest = manifest_by_key[import_key]
        if str(manifest.get("job_id")) != str(job.get("id")) or str(manifest.get("chunk_id")) != str(chunk.get("id")):
            manifest_owner_mismatches += 1
        if chunk.get("status") == "split":
            if manifest.get("status") == "split":
                split_state_manifests += 1
            elif manifest.get("status") == "released":
                legacy_released_split_manifests += 1
            else:
                split_manifest_status_mismatches += 1
            if manifest.get("memory_ids"):
                split_manifest_memory_rows += 1
        else:
            if manifest.get("status") != "succeeded":
                leaf_manifest_status_mismatches += 1
            if canonical_id_values(manifest.get("memory_ids") or []) != canonical_id_values(
                chunk.get("memory_ids") or []
            ):
                leaf_manifest_memory_id_mismatches += 1

    store_ids = {
        "pgvector": [row["id"] for row in vector_rows],
        "chunk_memory_ids": _flatten(successful_chunks, "memory_ids"),
        "manifest_memory_ids": _flatten(successful_manifests, "memory_ids"),
        "memory_hash_claims": [row["memory_id"] for row in successful_hashes],
        "graph_queue": [row["memory_id"] for row in synced_graph_items],
        "sqlite_history_add_rows": [row["memory_id"] for row in active_history],
        "neo4j_memory_nodes": [row["id"] for row in memory_nodes],
    }
    summaries = {name: summarize_ids(values) for name, values in store_ids.items()}
    canonical_sets = {name: set(canonical_id_values(values)) for name, values in store_ids.items()}
    pg_ids = canonical_sets["pgvector"]
    differences = {
        name: {
            "missing_from_pgvector": len(values - pg_ids),
            "missing_from_store": len(pg_ids - values),
        }
        for name, values in canonical_sets.items()
        if name != "pgvector"
    }
    all_store_sets_equal = all(values == pg_ids for values in canonical_sets.values())

    entities = job.get("entities") or {}
    deterministic_mismatches = 0
    unresolved_hash_chunks = 0
    for row in successful_hashes:
        conversation_id = row.get("raw_conversation_id")
        if not conversation_id:
            unresolved_hash_chunks += 1
            continue
        expected = deterministic_memory_id(
            str(job["project_id"]),
            entities,
            str(conversation_id),
            str(row["memory_hash"]),
        )
        if expected.lower() != str(row["memory_id"]).lower():
            deterministic_mismatches += 1

    claimed_hashes = _flatten(successful_chunks, "claimed_memory_hashes")
    hash_values = [str(row["memory_hash"]) for row in successful_hashes]
    claimed_hash_set_equal = set(claimed_hashes) == set(hash_values)

    dimensions = sorted({int(row["dimensions"]) for row in vector_rows})
    vector_fingerprint_rows = [
        {
            "id": str(row["id"]).lower(),
            "vector": row["vector"],
            "payload": row["payload"],
        }
        for row in vector_rows
    ]
    chunk_fingerprint_rows = _business_rows(
        chunks,
        (
            "id",
            "import_key",
            "conversation_id",
            "status",
            "attempt",
            "retry_count",
            "chunk_index",
            "chunk_count",
            "source_message_indices",
            "core_source_message_indices",
            "source_message_start",
            "source_message_end",
            "source_turn_start",
            "source_turn_end",
            "parent_import_key",
            "split_depth",
            "overlap_turns",
            "source_path",
            "memory_ids",
            "claimed_memory_hashes",
            "error_type",
        ),
    )
    manifest_fingerprint_rows = _business_rows(
        manifests,
        ("id", "import_key", "job_id", "chunk_id", "status", "attempts", "memory_ids"),
    )
    hash_fingerprint_rows = _business_rows(
        hashes,
        (
            "id",
            "conversation_id",
            "memory_hash",
            "job_id",
            "chunk_id",
            "status",
            "memory_id",
        ),
    )
    graph_fingerprint_rows = _business_rows(
        graph_items,
        ("id", "chunk_id", "item_key", "memory_id", "payload", "status", "attempts"),
    )
    history_fingerprint_rows = _business_rows(
        active_history,
        (
            "id",
            "memory_id",
            "old_memory",
            "new_memory",
            "event",
            "created_at",
            "updated_at",
            "is_deleted",
            "actor_id",
            "role",
            "project_id",
        ),
    )

    fingerprints = {
        "canonical_memory_id_set_sha256": summaries["pgvector"]["sha256"],
        "original_leaf_import_key_set_sha256": fingerprint_ids(row.get("import_key") for row in leaf_chunks),
        "original_leaf_topology_sha256": fingerprint_rows(_chunk_topology_rows(leaf_chunks)),
        "pgvector_rows_sha256": fingerprint_rows(vector_fingerprint_rows),
        "original_job_chunks_sha256": fingerprint_rows(chunk_fingerprint_rows),
        "project_manifests_sha256": fingerprint_rows(manifest_fingerprint_rows),
        "project_hash_claims_sha256": fingerprint_rows(hash_fingerprint_rows),
        "original_job_graph_queue_sha256": fingerprint_rows(graph_fingerprint_rows),
        "sqlite_history_add_rows_sha256": fingerprint_rows(history_fingerprint_rows),
        "neo4j_project_nodes_sha256": fingerprint_rows(neo4j_nodes),
        "neo4j_project_relationships_sha256": fingerprint_rows(neo4j_relationships),
    }

    violations: list[str] = []
    if job.get("status") not in _TERMINAL_JOB_STATUSES:
        violations.append("target job is not terminal")
    if job.get("status") != "completed":
        violations.append("target job did not complete without errors")
    if int(job.get("failed_chunks") or 0) != 0:
        violations.append("target job has failed chunks")
    if job.get("graph_status") != "completed":
        violations.append("target job graph status is not completed")
    expected_leaf_chunks = int(job.get("total_chunks") or 0)
    if len(leaf_chunks) != expected_leaf_chunks:
        violations.append("target job leaf chunk count differs from total_chunks")
    if len(chunks) != expected_leaf_chunks + len(split_chunks):
        violations.append("target job chunk rows do not equal leaves plus split parents")
    if int(job.get("split_chunks") or 0) != len(split_chunks):
        violations.append("target job split_chunks differs from retained split parents")
    if len(successful_chunks) != len(leaf_chunks):
        violations.append("target job contains a non-succeeded leaf chunk")
    if int(job.get("processed_chunks") or 0) != expected_leaf_chunks:
        violations.append("target job processed_chunks differs from total_chunks")
    if (
        int(job.get("imported_chunks") or 0) + int(job.get("skipped_chunks") or 0) + int(job.get("failed_chunks") or 0)
        != expected_leaf_chunks
    ):
        violations.append("target job leaf counters do not account for total_chunks")
    if int(job.get("imported_chunks") or 0) != len(successful_chunks):
        violations.append("target job imported_chunks differs from succeeded leaves")
    if int(job.get("skipped_chunks") or 0) != len(skipped_chunks):
        violations.append("target job skipped_chunks differs from skipped leaves")
    if int(job.get("failed_chunks") or 0) != len(failed_chunks):
        violations.append("target job failed_chunks differs from failed leaves")
    if missing_chunk_import_keys or duplicate_chunk_import_keys:
        violations.append("target job chunk import keys are missing or duplicated")
    if missing_manifest_import_keys or duplicate_manifest_import_keys:
        violations.append("project manifest import keys are missing or duplicated")
    if missing_parent_links or non_split_parent_links:
        violations.append("target job split tree contains an invalid parent link")
    if parent_depth_mismatches or parent_conversation_mismatches or orphan_split_depth_rows:
        violations.append("target job split tree contains inconsistent child metadata")
    if split_child_count_mismatches:
        violations.append("target job split parent does not have exactly two children")
    if split_parent_state_rows:
        violations.append("target job split parent contains persisted memory state")
    if invalid_provenance_rows or core_outside_source_rows:
        violations.append("target job chunk provenance indices are invalid")
    if message_boundary_mismatches or turn_boundary_mismatches or chunk_position_mismatches:
        violations.append("target job chunk provenance boundaries are inconsistent")
    if core_coverage_mismatches or core_duplication_mismatches or core_outside_parent_mismatches:
        violations.append("split children do not exactly partition parent core evidence")
    if source_coverage_mismatches or source_outside_parent_mismatches:
        violations.append("split children do not preserve parent source evidence")
    if overlap_on_parent_core_mismatches:
        violations.append("split child overlap duplicates parent core evidence")
    if split_child_position_mismatches or split_source_path_mismatches or missing_source_path_rows:
        violations.append("chunk audit provenance or split source lineage is invalid")
    if chunk_key_set != manifest_key_set:
        violations.append("project manifest keys differ from original job chunk keys")
    if manifest_owner_mismatches:
        violations.append("project manifest ownership differs from original job chunks")
    if split_manifest_status_mismatches or split_manifest_memory_rows:
        violations.append("split parent manifests are not empty split or legacy released rows")
    if leaf_manifest_status_mismatches or leaf_manifest_memory_id_mismatches:
        violations.append("leaf manifests do not match succeeded chunk memory IDs")
    if len(successful_manifests) != len(leaf_chunks):
        violations.append("project succeeded manifest count differs from leaf chunk count")
    if len(successful_hashes) != len(hashes):
        violations.append("project contains a non-succeeded or unbound hash claim")
    if len(synced_graph_items) != len(graph_items):
        violations.append("target graph queue contains a non-synced item")
    if not all_store_sets_equal:
        violations.append("cross-store memory ID sets differ")
    if any(summary["duplicates"] for summary in summaries.values()):
        violations.append("a store contains duplicate memory IDs")
    if any(summary["invalid_uuid_count"] for summary in summaries.values()):
        violations.append("a store contains invalid memory UUIDs")
    if deterministic_mismatches or unresolved_hash_chunks:
        violations.append("deterministic memory UUID validation failed")
    if not claimed_hash_set_equal:
        violations.append("chunk claimed hashes differ from succeeded hash rows")
    if len(dimensions) != 1:
        violations.append("PGVector rows do not have one embedding dimension")
    if int(job.get("memories_created") or 0) != summaries["pgvector"]["distinct"]:
        violations.append("job memories_created differs from PGVector count")

    report = {
        "schema": REPORT_SCHEMA,
        "privacy": {
            "aggregate_only": True,
            "id_set_fingerprints_only": True,
            "contains_raw_ids_text_vectors_or_payloads": False,
        },
        "target": {
            "job_id": str(job["id"]),
            "project_id": str(job["project_id"]),
            "status": job.get("status"),
            "graph_status": job.get("graph_status"),
            "total_chunks": int(job.get("total_chunks") or 0),
            "processed_chunks": int(job.get("processed_chunks") or 0),
            "imported_chunks": int(job.get("imported_chunks") or 0),
            "skipped_chunks": int(job.get("skipped_chunks") or 0),
            "failed_chunks": int(job.get("failed_chunks") or 0),
            "split_chunks": int(job.get("split_chunks") or 0),
            "memories_created": int(job.get("memories_created") or 0),
        },
        "stores": summaries,
        "pgvector_dimensions": dimensions,
        "row_counts": {
            "chunks": len(chunks),
            "leaf_chunks": len(leaf_chunks),
            "split_parent_chunks": len(split_chunks),
            "successful_chunks": len(successful_chunks),
            "manifests": len(manifests),
            "successful_manifests": len(successful_manifests),
            "split_state_parent_manifests": split_state_manifests,
            "legacy_released_split_parent_manifests": legacy_released_split_manifests,
            "hash_claims": len(hashes),
            "successful_hash_claims": len(successful_hashes),
            "graph_queue_items": len(graph_items),
            "synced_graph_queue_items": len(synced_graph_items),
            "history_add_rows": len(active_history),
            "neo4j_project_nodes": len(neo4j_nodes),
            "neo4j_project_relationships": len(neo4j_relationships),
        },
        "split_tree": {
            "missing_chunk_import_keys": missing_chunk_import_keys,
            "duplicate_chunk_import_keys": duplicate_chunk_import_keys,
            "missing_parent_links": missing_parent_links,
            "non_split_parent_links": non_split_parent_links,
            "parent_depth_mismatches": parent_depth_mismatches,
            "parent_conversation_mismatches": parent_conversation_mismatches,
            "orphan_split_depth_rows": orphan_split_depth_rows,
            "split_child_count_mismatches": split_child_count_mismatches,
            "split_parent_rows_with_memory_state": split_parent_state_rows,
            "invalid_provenance_rows": invalid_provenance_rows,
            "core_outside_source_rows": core_outside_source_rows,
            "message_boundary_mismatches": message_boundary_mismatches,
            "turn_boundary_mismatches": turn_boundary_mismatches,
            "chunk_position_mismatches": chunk_position_mismatches,
            "missing_source_path_rows": missing_source_path_rows,
            "core_coverage_mismatches": core_coverage_mismatches,
            "core_duplication_mismatches": core_duplication_mismatches,
            "core_outside_parent_mismatches": core_outside_parent_mismatches,
            "source_coverage_mismatches": source_coverage_mismatches,
            "source_outside_parent_mismatches": source_outside_parent_mismatches,
            "overlap_on_parent_core_mismatches": overlap_on_parent_core_mismatches,
            "split_child_position_mismatches": split_child_position_mismatches,
            "split_source_path_mismatches": split_source_path_mismatches,
        },
        "manifest_alignment": {
            "missing_for_chunks": len(chunk_key_set - manifest_key_set),
            "unexpected_for_project": len(manifest_key_set - chunk_key_set),
            "missing_import_keys": missing_manifest_import_keys,
            "duplicate_import_keys": duplicate_manifest_import_keys,
            "owner_mismatches": manifest_owner_mismatches,
            "split_status_mismatches": split_manifest_status_mismatches,
            "split_rows_with_memory_ids": split_manifest_memory_rows,
            "leaf_status_mismatches": leaf_manifest_status_mismatches,
            "leaf_memory_id_mismatches": leaf_manifest_memory_id_mismatches,
        },
        "exact_id_set": {
            "canonicalization": "lexicographically sorted lowercase IDs, each followed by LF, SHA-256",
            "all_store_sets_equal": all_store_sets_equal,
            "differences": differences,
        },
        "idempotency": {
            "deterministic_uuid5_mismatches": deterministic_mismatches,
            "unresolved_hash_chunk_links": unresolved_hash_chunks,
            "claimed_hash_set_equals_hash_table_set": claimed_hash_set_equal,
        },
        "snapshot_fingerprints": fingerprints,
        "violations": violations,
        "result": "passed" if not violations else "failed",
    }
    return report


def compare_idempotency(
    current: dict[str, Any],
    baseline: Mapping[str, Any],
    rerun: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_fingerprints = dict(baseline.get("snapshot_fingerprints") or {})
    current_fingerprints = dict(current.get("snapshot_fingerprints") or {})
    changed = sorted(
        key
        for key in set(baseline_fingerprints) | set(current_fingerprints)
        if baseline_fingerprints.get(key) != current_fingerprints.get(key)
    )
    baseline_target = baseline.get("target") or {}
    violations: list[str] = []
    if baseline.get("schema") != REPORT_SCHEMA or baseline.get("result") != "passed":
        violations.append("baseline is not a passed report for the current schema")
    if str(baseline_target.get("job_id")) != str(current["target"]["job_id"]):
        violations.append("baseline target job differs from current target job")
    if str(baseline_target.get("project_id")) != str(current["target"]["project_id"]):
        violations.append("baseline project differs from current project")
    if changed:
        violations.append("project or original-job fingerprints changed after rerun")

    expected_chunks = int(baseline_target.get("total_chunks") or 0)
    rerun_chunks = list(rerun.get("chunks") or [])
    rerun_job = rerun.get("job") or {}
    skipped_rows = [row for row in rerun_chunks if row.get("status") == "skipped"]
    nonempty_memory_rows = [row for row in rerun_chunks if row.get("memory_ids")]
    rerun_import_key_fingerprint = fingerprint_ids(row.get("import_key") or "" for row in rerun_chunks)
    rerun_topology_fingerprint = fingerprint_rows(_chunk_topology_rows(rerun_chunks))
    checks = {
        "same_project": str(rerun_job.get("project_id")) == str(current["target"]["project_id"]),
        "status_completed": rerun_job.get("status") == "completed",
        "total_chunks_match": int(rerun_job.get("total_chunks") or 0) == expected_chunks,
        "processed_chunks_match": int(rerun_job.get("processed_chunks") or 0) == expected_chunks,
        "chunk_row_count_match": len(rerun_chunks) == expected_chunks,
        "leaf_import_keys_match": rerun_import_key_fingerprint
        == current_fingerprints.get("original_leaf_import_key_set_sha256"),
        "leaf_topology_matches": rerun_topology_fingerprint
        == current_fingerprints.get("original_leaf_topology_sha256"),
        "all_chunks_skipped": int(rerun_job.get("skipped_chunks") or 0) == expected_chunks
        and len(skipped_rows) == expected_chunks,
        "zero_imported_chunks": int(rerun_job.get("imported_chunks") or 0) == 0,
        "zero_failed_chunks": int(rerun_job.get("failed_chunks") or 0) == 0,
        "zero_split_chunks": int(rerun_job.get("split_chunks") or 0) == 0,
        "zero_memories_created": int(rerun_job.get("memories_created") or 0) == 0,
        "no_rerun_chunk_memory_ids": not nonempty_memory_rows,
        "graph_skipped": rerun_job.get("graph_status") == "skipped",
    }
    failed_checks = sorted(key for key, value in checks.items() if not value)
    if failed_checks:
        violations.append("rerun job is not a complete skip-only execution")
    result = {
        "baseline_schema": baseline.get("schema"),
        "rerun_job_id": str(rerun_job.get("id") or ""),
        "expected_chunks": expected_chunks,
        "observed_rerun_chunk_rows": len(rerun_chunks),
        "rerun_import_key_set_sha256": rerun_import_key_fingerprint,
        "rerun_topology_sha256": rerun_topology_fingerprint,
        "checks": checks,
        "failed_checks": failed_checks,
        "changed_fingerprints": changed,
        "violations": violations,
        "result": "passed" if not violations else "failed",
    }
    current["same_project_idempotency"] = result
    if violations:
        current["violations"].extend(f"idempotency: {item}" for item in violations)
        current["result"] = "failed"
    return current


def _postgres_dsn(database_env: str, default_database: str | None = None) -> str:
    database = os.environ.get(database_env, default_database or "").strip()
    if not database:
        raise ValueError(f"{database_env} is required when an explicit DSN is not supplied")
    user = quote(os.environ.get("POSTGRES_USER", "postgres"), safe="")
    password = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{quote(database, safe='')}"


def _qualified_identifier(name: str):
    try:
        from psycopg import sql
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("psycopg is required for consistency verification") from exc
    parts = name.split(".")
    if not parts or any(not _IDENTIFIER.fullmatch(part) for part in parts):
        raise ValueError("vector table must be an unquoted PostgreSQL identifier")
    return sql.SQL(".").join(sql.Identifier(part) for part in parts)


def _fetch_all(cursor, query: Any, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor.execute(query, params)
    columns = []
    for item in cursor.description:
        name = getattr(item, "name", None)
        columns.append(str(name if name is not None else item[0]))
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def load_app_snapshot(
    dsn: str,
    job_id: str,
    project_id: str,
    rerun_job_id: str | None = None,
) -> dict[str, Any]:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("psycopg is required for consistency verification") from exc

    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            with connection.cursor() as cursor:
                jobs = _fetch_all(
                    cursor,
                    """
                    SELECT id, project_id, status, phase, entities, total_chunks, processed_chunks,
                           imported_chunks, skipped_chunks, failed_chunks, memories_created,
                           split_chunks, graph_status, graph_pending_items, graph_synced_items,
                           graph_failed_items
                    FROM memory_import_jobs
                    WHERE id = %s AND project_id = %s
                    """,
                    (job_id, project_id),
                )
                if len(jobs) != 1:
                    raise RuntimeError("target job was not found in the requested project")
                chunks = _fetch_all(
                    cursor,
                    """
                    SELECT id, import_key, conversation_id, status, attempt, retry_count,
                           chunk_index, chunk_count, source_message_indices,
                           core_source_message_indices, source_message_start, source_message_end,
                           source_turn_start, source_turn_end, parent_import_key, split_depth,
                           overlap_turns, source_path, memory_ids, claimed_memory_hashes, error_type
                    FROM memory_import_chunks
                    WHERE job_id = %s
                    ORDER BY import_key
                    """,
                    (job_id,),
                )
                manifests = _fetch_all(
                    cursor,
                    """
                    SELECT id, import_key, job_id, chunk_id, status, attempts, memory_ids
                    FROM memory_import_manifests
                    WHERE project_id = %s
                    ORDER BY import_key
                    """,
                    (project_id,),
                )
                hashes = _fetch_all(
                    cursor,
                    """
                    SELECT h.id, h.conversation_id, h.memory_hash, h.job_id, h.chunk_id,
                           h.status, h.memory_id, c.conversation_id AS raw_conversation_id
                    FROM memory_import_hashes AS h
                    LEFT JOIN memory_import_chunks AS c ON c.id = h.chunk_id
                    WHERE h.project_id = %s
                    ORDER BY h.conversation_id, h.memory_hash
                    """,
                    (project_id,),
                )
                graph_items = _fetch_all(
                    cursor,
                    """
                    SELECT id, chunk_id, item_key, memory_id, payload, status, attempts
                    FROM memory_import_graph_items
                    WHERE job_id = %s
                    ORDER BY item_key
                    """,
                    (job_id,),
                )
                result: dict[str, Any] = {
                    "job": jobs[0],
                    "chunks": chunks,
                    "manifests": manifests,
                    "hashes": hashes,
                    "graph_items": graph_items,
                }
                if rerun_job_id:
                    rerun_jobs = _fetch_all(
                        cursor,
                        """
                        SELECT id, project_id, status, phase, total_chunks, processed_chunks,
                               imported_chunks, skipped_chunks, failed_chunks, memories_created,
                               split_chunks, graph_status
                        FROM memory_import_jobs
                        WHERE id = %s AND project_id = %s
                        """,
                        (rerun_job_id, project_id),
                    )
                    if len(rerun_jobs) != 1:
                        raise RuntimeError("rerun job was not found in the requested project")
                    rerun_chunks = _fetch_all(
                        cursor,
                        """
                        SELECT import_key, status, memory_ids, parent_import_key, split_depth
                        FROM memory_import_chunks
                        WHERE job_id = %s
                        ORDER BY import_key
                        """,
                        (rerun_job_id,),
                    )
                    result["rerun"] = {"job": rerun_jobs[0], "chunks": rerun_chunks}
                return result


def load_vector_rows(dsn: str, table: str, project_id: str) -> list[dict[str, Any]]:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("psycopg is required for consistency verification") from exc

    query = sql.SQL(
        "SELECT id::text AS id, vector::text AS vector, payload, "
        "vector_dims(vector) AS dimensions FROM {} "
        "WHERE payload->>'project_id' = %s ORDER BY id"
    ).format(_qualified_identifier(table))
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            with connection.cursor() as cursor:
                return _fetch_all(cursor, query, (project_id,))


def load_history_rows(path: Path, project_id: str) -> list[dict[str, Any]]:
    resolved = path.resolve(strict=True)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT id, memory_id, old_memory, new_memory, event, created_at, updated_at,
                   is_deleted, actor_id, role, project_id
            FROM history
            WHERE project_id = ? AND event = 'ADD' AND COALESCE(is_deleted, 0) = 0
            ORDER BY memory_id, id
            """,
            (project_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def load_neo4j_rows(
    uri: str,
    username: str,
    password: str,
    database: str,
    project_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        from neo4j import READ_ACCESS, GraphDatabase
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("neo4j is required for consistency verification") from exc

    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            nodes = [
                {
                    "id": str(record["id"]),
                    "labels": sorted(record["labels"]),
                    "properties": dict(record["properties"]),
                }
                for record in session.run(
                    """
                    MATCH (node {project_id: $project_id})
                    RETURN coalesce(node.id, node.key, node.name) AS id,
                           labels(node) AS labels,
                           properties(node) AS properties
                    ORDER BY id
                    """,
                    project_id=project_id,
                )
            ]
            relationships = [
                {
                    "source": str(record["source"]),
                    "target": str(record["target"]),
                    "type": str(record["type"]),
                    "properties": dict(record["properties"]),
                }
                for record in session.run(
                    """
                    MATCH (source {project_id: $project_id})-[relationship]->
                          (target {project_id: $project_id})
                    RETURN coalesce(source.id, source.key, source.name) AS source,
                           coalesce(target.id, target.key, target.name) AS target,
                           type(relationship) AS type,
                           properties(relationship) AS properties
                    ORDER BY source, type, target
                    """,
                    project_id=project_id,
                )
            ]
            return nodes, relationships
    finally:
        driver.close()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--vector-table", required=True)
    parser.add_argument("--app-dsn", default=os.environ.get("YIQIAO_VERIFY_APP_DSN"))
    parser.add_argument("--vector-dsn", default=os.environ.get("YIQIAO_VERIFY_VECTOR_DSN"))
    parser.add_argument(
        "--history-db",
        type=Path,
        default=Path(os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")),
    )
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://neo4j:7687"))
    parser.add_argument("--neo4j-username", default=os.environ.get("NEO4J_USERNAME", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-database", default=os.environ.get("NEO4J_DATABASE", "neo4j"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--rerun-job-id")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if bool(args.baseline) != bool(args.rerun_job_id):
        raise ValueError("--baseline and --rerun-job-id must be supplied together")
    app_dsn = args.app_dsn or _postgres_dsn("APP_DB_NAME")
    vector_dsn = args.vector_dsn or _postgres_dsn("POSTGRES_DB", "yiqiao")
    if not args.neo4j_password:
        raise ValueError("NEO4J_PASSWORD is required")

    app = load_app_snapshot(
        app_dsn,
        args.job_id,
        args.project_id,
        rerun_job_id=args.rerun_job_id,
    )
    neo4j_nodes, neo4j_relationships = load_neo4j_rows(
        args.neo4j_uri,
        args.neo4j_username,
        args.neo4j_password,
        args.neo4j_database,
        args.project_id,
    )
    report = build_report(
        {
            "app": app,
            "vector_rows": load_vector_rows(vector_dsn, args.vector_table, args.project_id),
            "history_rows": load_history_rows(args.history_db, args.project_id),
            "neo4j_nodes": neo4j_nodes,
            "neo4j_relationships": neo4j_relationships,
        }
    )
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report = compare_idempotency(report, baseline, app["rerun"])
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"consistency verification failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
