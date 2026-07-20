import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chat_import_artifact_compare.py"
_SQL = Path(__file__).resolve().parents[1] / "scripts" / "chat_import_quality_queries.sql"
_SPEC = importlib.util.spec_from_file_location("chat_import_artifact_compare", _SCRIPT)
artifact_compare = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = artifact_compare
_SPEC.loader.exec_module(artifact_compare)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _manifest(parser_hash: str, *, dataset_hash: str | None = None) -> dict:
    value = {
        "schema": "yiqiao.chat-import-dataset/v1",
        "dataset_sha256": dataset_hash or _sha("dataset"),
        "files": [
            {
                "path_sha256": _sha("first.md"),
                "conversations": [{"id_sha256": _sha("first"), "message_count": 5}],
            },
            {
                "path_sha256": _sha("second.md"),
                "conversations": [{"id_sha256": _sha("second"), "message_count": 3}],
            },
        ],
        "parse_options": {"source_app": "auto"},
        "parser": {"module": "server/chat_import.py", "sha256": parser_hash},
        "summary": {"file_count": 2, "message_count": 8, "source_tokens": 1000},
        "tokenizer": {"encoding": "cl100k_base", "version": "0.12.0"},
    }
    value["manifest_sha256"] = hashlib.sha256(artifact_compare._canonical_json(value)).hexdigest()
    return value


def _memory(name: str, *, source: str, conversation: str, category: str, attribution: str = "user") -> dict:
    return {
        "attributed_to": attribution,
        "attributed_to_sha256": None,
        "categories_sha256": [_sha(category)],
        "category_count": 1,
        "confidence": 0.9,
        "conversation_id_sha256": _sha(conversation),
        "core_source_message_indices": [0, 1],
        "id": _sha(f"id:{name}")[:8],
        "import_key": _sha(f"import:{name}"),
        "memory_characters": len(name),
        "memory_sha256": _sha(name),
        "normalized_memory_sha256": _sha(name.strip().casefold()),
        "source_app": "chatgpt",
        "source_message_indices": [1],
        "source_path_sha256": _sha(source),
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _loaded_manifest(
    root: Path,
    name: str,
    parser_hash: str,
    *,
    dataset_hash: str | None = None,
):
    path = root / name
    _write_json(path, _manifest(parser_hash, dataset_hash=dataset_hash))
    return artifact_compare.load_manifest(path)


def _preflight(phase: str) -> dict:
    route = "memory_import" if phase == "tiered" else "runtime"
    models = ["fast-model", "pro-model"] if phase == "tiered" else ["pro-model"]
    probes = []
    for model in models:
        probe = {"model": model, "route": route, "status": "ok"}
        if route == "memory_import":
            probe.update(
                {
                    "route_base_url_sha256": _sha("llm-base-url"),
                    "route_configured": True,
                    "route_effective": True,
                    "route_model": model,
                    "route_provider": "openai",
                }
            )
        probes.append(probe)
    return {
        "api_health": "ok",
        "checked_at": "volatile",
        "configured_services": {
            "embedder": {
                "base_url_sha256": [_sha("embedder-base-url")],
                "configured": True,
                "model": "embedding-model",
                "provider": "openai",
            },
            "llm": {
                "base_url_sha256": [_sha("llm-base-url")],
                "configured": True,
                "model": "pro-model",
                "provider": "openai",
            },
            "model_route": route,
        },
        "embedder": {"dimensions": 1024, "latency_ms": 12.3, "status": "ok"},
        "models": probes,
        "project_isolation": {"jobs": 0, "memories": 0, "status": "empty"},
        "status": "ok",
        "workspace_settings": {
            "category_count": 10,
            "extraction_field_count": 6,
            "sections_sha256": _sha("workspace"),
        },
    }


def _write_run(
    root: Path,
    memories: list[dict],
    *,
    manifest,
    phase: str,
    elapsed: float,
    code_hash: str | None = None,
    harness_hash: str | None = None,
    preflight: dict | None = None,
) -> None:
    root.mkdir()
    dataset_hash = manifest["dataset_sha256"]
    code_hash = code_hash or manifest["parser"]["sha256"]
    harness_hash = harness_hash or code_hash
    preflight = preflight or _preflight(phase)
    _write_json(
        root / "environment.json",
        {
            "manifest": {
                "dataset_sha256": dataset_hash,
                "file_sha256": manifest.file_sha256,
                "manifest_sha256": manifest["manifest_sha256"],
            },
            "preflight_services": preflight["configured_services"],
            "relevant_file_sha256": {
                "mem0/memory/main.py": code_hash,
                "mem0/memory/storage.py": code_hash,
                "scripts/chat_import_benchmark.py": harness_hash,
                "server/chat_import.py": code_hash,
                "server/import_repository.py": code_hash,
                "server/main.py": code_hash,
            },
            "run": {"phase": phase},
        },
    )
    _write_json(
        root / "job.json",
        {
            "retry_count": 1,
            "status": "completed",
            "total_chunks": 2,
        },
    )
    _write_json(
        root / "preflight.json",
        preflight,
    )
    _write_json(
        root / "run-config.json",
        {
            "api": {"project_id": f"project-{phase}"},
            "artifact_policy": {
                "include_sensitive_artifacts": False,
                "source_messages_included": False,
            },
            "options": {
                "entities": [{"type": "user", "id": f"user-{phase}"}],
                "fallback_model": "pro-model",
                "fast_model": "fast-model",
                "model_tiering_enabled": phase == "tiered",
                "workers": 3,
            },
        },
    )
    _write_json(
        root / "summary.json",
        {
            "dataset": {"dataset_sha256": dataset_hash, "source_tokens": 1000},
            "run": {"elapsed_seconds": elapsed, "phase": phase, "valid_complete_run": True},
        },
    )
    (root / "memories.jsonl").write_text(
        "".join(json.dumps(memory, sort_keys=True) + "\n" for memory in memories),
        encoding="utf-8",
    )


def test_compare_runs_reports_aggregate_overlap_drift_and_non_pure_ab(tmp_path):
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    baseline_memories = [
        _memory("Tea preference", source="first.md", conversation="first", category="preference"),
        _memory(
            "Coffee preference",
            source="first.md",
            conversation="first",
            category="preference",
            attribution="assistant",
        ),
    ]
    candidate_memories = [
        _memory("Tea preference", source="first.md", conversation="first", category="preference"),
        _memory("Travel plan", source="second.md", conversation="second", category="planning"),
    ]
    baseline_manifest = _loaded_manifest(tmp_path, "baseline-manifest.json", _sha("old"))
    candidate_manifest = _loaded_manifest(tmp_path, "candidate-manifest.json", _sha("new"))
    _write_run(
        baseline_root,
        baseline_memories,
        manifest=baseline_manifest,
        phase="optimized_pro",
        elapsed=100,
    )
    _write_run(
        candidate_root,
        candidate_memories,
        manifest=candidate_manifest,
        phase="tiered",
        elapsed=50,
    )

    result = artifact_compare.compare_runs(
        artifact_compare.load_run(baseline_root),
        artifact_compare.load_run(candidate_root),
        baseline_manifest,
        candidate_manifest,
    )

    assert result["privacy"]["aggregate_only"] is True
    assert result["comparability"]["dataset_records_equal_ignoring_parser_identity"] is True
    assert result["comparability"]["pure_model_routing_ab"] is False
    assert "server/chat_import.py" in result["comparability"]["runtime_hash_changed_files"]
    assert result["comparability"]["unexpected_config_difference_paths"] == []
    assert result["comparison"]["exact_text"]["baseline_to_candidate"] == {
        "matches": 1,
        "n": 2,
        "rate": 0.5,
    }
    assert result["comparison"]["source_emission"]["intersection"] == 1
    assert result["comparison"]["source_emission"]["right_only"] == 1
    assert result["comparison"]["performance"]["candidate_throughput_ratio"] == 2.0
    assert result["comparison"]["performance"]["elapsed_speedup_baseline_over_candidate"] == 2.0
    assert result["runs"]["baseline"]["citations"]["combined_citation_core_valid_rate"] == 1.0
    assert result["comparison"]["category_distribution"]["total_variation_distance"] == 0.5
    serialized = json.dumps(result)
    assert "Tea preference" not in serialized
    assert _sha("Tea preference") not in serialized


def test_load_run_refuses_incomplete_and_raw_artifacts(tmp_path):
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    with pytest.raises(artifact_compare.ComparisonError, match="missing"):
        artifact_compare.load_run(incomplete)

    raw = tmp_path / "raw"
    manifest = _loaded_manifest(tmp_path, "raw-manifest.json", _sha("raw-parser"))
    memories = [_memory("Private memory", source="first.md", conversation="first", category="private")]
    memories[0]["memory"] = "Private memory"
    _write_run(raw, memories, manifest=manifest, phase="tiered", elapsed=10)

    with pytest.raises(artifact_compare.ComparisonError, match="forbidden raw fields"):
        artifact_compare.load_run(raw)


def test_citation_metric_requires_in_range_core_evidence_intersection(tmp_path):
    root = tmp_path / "run"
    manifest = _loaded_manifest(tmp_path, "manifest.json", _sha("current"))
    valid = _memory("Valid", source="first.md", conversation="first", category="fact")
    core_out_of_range = _memory("Out of range", source="first.md", conversation="first", category="fact")
    core_out_of_range["core_source_message_indices"] = [9]
    no_core_intersection = _memory("No core", source="first.md", conversation="first", category="fact")
    no_core_intersection["core_source_message_indices"] = [2]
    _write_run(
        root,
        [valid, core_out_of_range, no_core_intersection],
        manifest=manifest,
        phase="tiered",
        elapsed=10,
    )

    run = artifact_compare.load_run(root)
    metrics = artifact_compare._run_metrics(run, artifact_compare._bind_manifest(run, manifest, "test"))

    assert metrics["citations"] == {
        "chunk_source_membership_requires_database": True,
        "citation_manifest_range_valid_rows": 3,
        "citation_manifest_valid_rate": 1.0,
        "citation_subset_of_core_rows": 1,
        "combined_citation_core_valid_rate": 0.333333,
        "core_evidence_comparable_rows": 2,
        "core_evidence_intersection_valid_rows": 1,
        "core_evidence_invalid_rows": 2,
        "core_evidence_valid_rate": 0.333333,
        "core_manifest_range_valid_rows": 2,
        "core_structure_valid_rows": 3,
        "overlap_only_citation_rows": 1,
        "structure_valid_rows": 3,
    }


def test_compare_runs_refuses_dataset_identity_mismatch(tmp_path):
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    memories = [_memory("Fact", source="first.md", conversation="first", category="fact")]
    baseline_manifest = _loaded_manifest(tmp_path, "baseline-manifest.json", _sha("parser"))
    candidate_manifest = _loaded_manifest(
        tmp_path,
        "candidate-manifest.json",
        _sha("parser"),
        dataset_hash=_sha("other-dataset"),
    )
    _write_run(baseline_root, memories, manifest=baseline_manifest, phase="optimized_pro", elapsed=10)
    _write_run(candidate_root, memories, manifest=candidate_manifest, phase="tiered", elapsed=10)

    with pytest.raises(artifact_compare.ComparisonError, match="one raw dataset identity"):
        artifact_compare.compare_runs(
            artifact_compare.load_run(baseline_root),
            artifact_compare.load_run(candidate_root),
            baseline_manifest,
            candidate_manifest,
        )


def test_compare_runs_refuses_same_dataset_wrong_manifest(tmp_path):
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    memories = [_memory("Fact", source="first.md", conversation="first", category="fact")]
    frozen_manifest = _loaded_manifest(tmp_path, "frozen-manifest.json", _sha("frozen-parser"))
    wrong_manifest = _loaded_manifest(tmp_path, "wrong-manifest.json", _sha("wrong-parser"))
    _write_run(
        baseline_root,
        memories,
        manifest=frozen_manifest,
        phase="optimized_pro",
        elapsed=10,
    )
    _write_run(candidate_root, memories, manifest=frozen_manifest, phase="tiered", elapsed=10)

    with pytest.raises(artifact_compare.ComparisonError, match="frozen environment identity"):
        artifact_compare.compare_runs(
            artifact_compare.load_run(baseline_root),
            artifact_compare.load_run(candidate_root),
            wrong_manifest,
            frozen_manifest,
        )


def test_preflight_mismatch_prevents_pure_model_routing_ab(tmp_path):
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    manifest = _loaded_manifest(tmp_path, "manifest.json", _sha("parser"))
    memories = [_memory("Fact", source="first.md", conversation="first", category="fact")]
    baseline_preflight = _preflight("optimized_pro")
    candidate_preflight = _preflight("tiered")
    candidate_preflight["workspace_settings"]["sections_sha256"] = _sha("different-workspace")
    _write_run(
        baseline_root,
        memories,
        manifest=manifest,
        phase="optimized_pro",
        elapsed=10,
        preflight=baseline_preflight,
    )
    _write_run(
        candidate_root,
        memories,
        manifest=manifest,
        phase="tiered",
        elapsed=10,
        preflight=candidate_preflight,
    )

    result = artifact_compare.compare_runs(
        artifact_compare.load_run(baseline_root),
        artifact_compare.load_run(candidate_root),
        manifest,
        manifest,
    )

    comparability = result["comparability"]
    assert comparability["pure_model_routing_ab"] is False
    assert comparability["stable_preflight_identity_equal"] is False
    assert "workspace_settings.sections_sha256" in comparability["stable_preflight_difference_paths"]


def test_harness_only_hash_drift_prevents_pure_model_routing_ab(tmp_path):
    baseline_root = tmp_path / "baseline"
    candidate_root = tmp_path / "candidate"
    manifest = _loaded_manifest(tmp_path, "manifest.json", _sha("parser"))
    memories = [_memory("Fact", source="first.md", conversation="first", category="fact")]
    _write_run(
        baseline_root,
        memories,
        manifest=manifest,
        phase="optimized_pro",
        elapsed=10,
        harness_hash=_sha("old-harness"),
    )
    _write_run(
        candidate_root,
        memories,
        manifest=manifest,
        phase="tiered",
        elapsed=10,
        harness_hash=_sha("new-harness"),
    )

    result = artifact_compare.compare_runs(
        artifact_compare.load_run(baseline_root),
        artifact_compare.load_run(candidate_root),
        manifest,
        manifest,
    )

    comparability = result["comparability"]
    assert comparability["pure_model_routing_ab"] is False
    assert comparability["harness_hash_changed"] is True
    assert comparability["runtime_hash_changed_files"] == []
    assert comparability["model_route_identity"]["baseline"]["verified"] is True
    assert comparability["model_route_identity"]["candidate"]["verified"] is True


def test_quality_query_pack_is_parameterized_and_read_only():
    sql = _SQL.read_text(encoding="utf-8")

    assert ":'historical_batch'" in sql
    assert ":'pro_project'" in sql
    assert ":'tiered_project'" in sql
    assert 'FROM :"historical_table"' in sql
    assert 'FROM :"candidate_table"' in sql
    assert 'JOIN :"candidate_table"' in sql
    assert r"\set historical_table 'memories'" in sql
    assert r"\set candidate_table 'memories_bench_20260714'" in sql
    assert r"\set pro_job_id '8790c03f-44e6-4786-8dec-4e07e80683c2'" in sql
    assert r"\set tiered_job_id '40aac686-b03a-46ef-a874-512be1e5007c'" in sql
    assert "chunk.job_id = :'tiered_job_id'::uuid" in sql
    assert "('full_pro', :'pro_project', :'pro_job_id'::uuid)" in sql
    assert "('tiered', :'tiered_project', :'tiered_job_id'::uuid)" in sql
    assert "ORDER BY created_at DESC" not in sql
    assert "citation_in_chunk_sources" in sql
    assert "cites_core_evidence" in sql
    assert "historical_to_candidate" in sql
    assert "full_pro_to_tiered" in sql
    assert r"\if :{?expected_historical_id_set_md5}" in sql
    assert r"\gset historical_gate_" in sql
    assert r"\gset basename_collision_gate_" in sql
    assert sql.count(r"\quit 3") >= 4
    candidate_to_historical = sql.split("), candidate_to_historical AS (", maxsplit=1)[1].split(
        "), scores AS (", maxsplit=1
    )[0]
    assert "LEFT JOIN historical" in candidate_to_historical
    assert "coalesce(max(1 - (candidate.vector <=> historical.vector)), 0)" in candidate_to_historical
    assert not re.search(r"\b(?:alter|create|delete|drop|insert|truncate|update)\b", sql, re.IGNORECASE)
