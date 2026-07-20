#!/usr/bin/env python3
"""Compare two completed, sanitized chat-import benchmark artifact sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "yiqiao.chat-import-artifact-comparison/v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_RUN_FILES = (
    "environment.json",
    "job.json",
    "memories.jsonl",
    "preflight.json",
    "run-config.json",
    "summary.json",
)
ALLOWED_CONFIG_DIFFERENCES = {
    "api.project_id",
    "options.entities",
    "options.model_tiering_enabled",
}
RAW_MEMORY_KEYS = {"data", "memory", "metadata", "text", "text_lemmatized"}
RUNTIME_FILES = {
    "mem0/memory/main.py",
    "mem0/memory/storage.py",
    "server/chat_import.py",
    "server/import_repository.py",
    "server/main.py",
}
HARNESS_FILE = "scripts/chat_import_benchmark.py"
REQUIRED_CODE_FILES = RUNTIME_FILES | {HARNESS_FILE}


class ComparisonError(RuntimeError):
    """The supplied artifacts cannot support a safe comparison."""


class LoadedManifest(dict[str, Any]):
    """A verified manifest plus the hash of the exact supplied file bytes."""

    def __init__(self, value: Mapping[str, Any], *, file_sha256: str, source: Path) -> None:
        super().__init__(value)
        self.file_sha256 = file_sha256
        self.source = source


@dataclass(frozen=True)
class RunArtifacts:
    root: Path
    environment: dict[str, Any]
    job: dict[str, Any]
    memories: list[dict[str, Any]]
    preflight: dict[str, Any]
    run_config: dict[str, Any]
    summary: dict[str, Any]
    file_sha256: dict[str, str]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ComparisonError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except ComparisonError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"JSON artifact must contain an object: {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line, object_pairs_hook=_reject_duplicate_keys)
                if not isinstance(value, dict):
                    raise ComparisonError(f"JSONL row {line_number} must contain an object: {path}")
                rows.append(value)
    except ComparisonError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read JSONL artifact {path}: {exc}") from exc
    return rows


def _verify_manifest_integrity(manifest: Mapping[str, Any], description: str) -> None:
    recorded = manifest.get("manifest_sha256")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    actual = hashlib.sha256(_canonical_json(body)).hexdigest()
    if not isinstance(recorded, str) or SHA256_RE.fullmatch(recorded) is None or recorded != actual:
        raise ComparisonError(f"Manifest self-hash mismatch: {description}")
    dataset_sha256 = manifest.get("dataset_sha256")
    if not isinstance(dataset_sha256, str) or SHA256_RE.fullmatch(dataset_sha256) is None:
        raise ComparisonError(f"Manifest has an invalid dataset SHA-256: {description}")
    if not isinstance(manifest.get("files"), list) or not manifest["files"]:
        raise ComparisonError(f"Manifest contains no file records: {description}")
    parser = manifest.get("parser")
    if (
        not isinstance(parser, Mapping)
        or parser.get("module") != "server/chat_import.py"
        or not isinstance(parser.get("sha256"), str)
        or SHA256_RE.fullmatch(parser["sha256"]) is None
    ):
        raise ComparisonError(f"Manifest has an invalid parser identity: {description}")


def load_manifest(path: Path) -> LoadedManifest:
    path = path.resolve()
    manifest = _load_json(path)
    _verify_manifest_integrity(manifest, str(path))
    return LoadedManifest(manifest, file_sha256=_sha256_file(path), source=path)


def _valid_complete_run(summary: Mapping[str, Any], job: Mapping[str, Any]) -> bool:
    run = summary.get("run") if isinstance(summary.get("run"), Mapping) else {}
    return bool(run.get("valid_complete_run") or run.get("operationally_complete")) and job.get("status") == "completed"


def load_run(root: Path) -> RunArtifacts:
    root = root.resolve()
    missing = [name for name in REQUIRED_RUN_FILES if not (root / name).is_file()]
    if missing:
        raise ComparisonError(f"Run artifacts are incomplete at {root}; missing: {', '.join(missing)}")
    if (root / "failure.json").exists():
        raise ComparisonError(f"Run contains failure.json and is not eligible for comparison: {root}")

    environment = _load_json(root / "environment.json")
    job = _load_json(root / "job.json")
    preflight = _load_json(root / "preflight.json")
    run_config = _load_json(root / "run-config.json")
    summary = _load_json(root / "summary.json")
    memories = _load_jsonl(root / "memories.jsonl")
    policy = run_config.get("artifact_policy") if isinstance(run_config.get("artifact_policy"), Mapping) else {}
    if policy.get("include_sensitive_artifacts") is not False or policy.get("source_messages_included") is not False:
        raise ComparisonError(f"Run does not prove a sanitized artifact policy: {root}")
    if not _valid_complete_run(summary, job):
        raise ComparisonError(f"Run did not satisfy operational completion requirements: {root}")
    if not memories:
        raise ComparisonError(f"Run contains no sanitized memories: {root}")

    for index, memory in enumerate(memories, start=1):
        forbidden = RAW_MEMORY_KEYS & set(memory)
        if forbidden:
            raise ComparisonError(
                f"Sanitized memory row {index} contains forbidden raw fields: {', '.join(sorted(forbidden))}"
            )
        for key in ("conversation_id_sha256", "memory_sha256", "normalized_memory_sha256", "source_path_sha256"):
            if not isinstance(memory.get(key), str) or not SHA256_RE.fullmatch(memory[key]):
                raise ComparisonError(f"Sanitized memory row {index} has an invalid {key}.")
        category_hashes = memory.get("categories_sha256")
        if not isinstance(category_hashes, list) or any(
            not isinstance(value, str) or not SHA256_RE.fullmatch(value) for value in category_hashes
        ):
            raise ComparisonError(f"Sanitized memory row {index} has invalid category hashes.")

    file_sha256 = {name: _sha256_file(root / name) for name in REQUIRED_RUN_FILES}
    return RunArtifacts(root, environment, job, memories, preflight, run_config, summary, file_sha256)


def _bind_manifest(run: RunArtifacts, manifest: Mapping[str, Any], label: str) -> LoadedManifest:
    _verify_manifest_integrity(manifest, f"{label} manifest")
    if not isinstance(manifest, LoadedManifest):
        raise ComparisonError(f"{label.capitalize()} manifest was not loaded from a supplied file.")

    environment_manifest = run.environment.get("manifest")
    if not isinstance(environment_manifest, Mapping):
        raise ComparisonError(f"{label.capitalize()} environment has no frozen manifest identity.")
    expected_identity = {
        "dataset_sha256": manifest.get("dataset_sha256"),
        "file_sha256": manifest.file_sha256,
        "manifest_sha256": manifest.get("manifest_sha256"),
    }
    mismatches = [key for key, expected in expected_identity.items() if environment_manifest.get(key) != expected]
    if mismatches:
        raise ComparisonError(
            f"{label.capitalize()} supplied manifest does not match its frozen environment identity: "
            f"{', '.join(mismatches)}."
        )

    summary_dataset = run.summary.get("dataset")
    summary_dataset_sha256 = summary_dataset.get("dataset_sha256") if isinstance(summary_dataset, Mapping) else None
    if summary_dataset_sha256 != manifest.get("dataset_sha256"):
        raise ComparisonError(f"{label.capitalize()} summary and frozen manifest dataset identities differ.")

    relevant_hashes = run.environment.get("relevant_file_sha256")
    captured_parser_sha256 = (
        relevant_hashes.get("server/chat_import.py") if isinstance(relevant_hashes, Mapping) else None
    )
    parser = manifest.get("parser")
    manifest_parser_sha256 = parser.get("sha256") if isinstance(parser, Mapping) else None
    if captured_parser_sha256 != manifest_parser_sha256:
        raise ComparisonError(
            f"{label.capitalize()} manifest parser SHA-256 does not match the captured server/chat_import.py hash."
        )
    return manifest


def _manifest_view(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key not in {"manifest_sha256", "parser"}}


def _manifest_limits(manifest: Mapping[str, Any]) -> dict[tuple[str, str], int]:
    limits: dict[tuple[str, str], int] = {}
    for file_record in manifest.get("files", []):
        if not isinstance(file_record, Mapping):
            continue
        path_hash = file_record.get("path_sha256")
        for conversation in file_record.get("conversations", []):
            if isinstance(conversation, Mapping) and path_hash and conversation.get("id_sha256"):
                limits[(str(path_hash), str(conversation["id_sha256"]))] = int(conversation.get("message_count", 0))
    return limits


def _valid_indices(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(index, int) and not isinstance(index, bool) and index >= 0 for index in value)
        and len(value) == len(set(value))
    )


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "mean": None, "median": None, "p90": None, "p95": None, "max": None}
    numeric = [float(value) for value in values]
    return {
        "min": round(min(numeric), 6),
        "mean": round(statistics.fmean(numeric), 6),
        "median": round(float(statistics.median(numeric)), 6),
        "p90": round(float(_percentile(numeric, 0.9)), 6),
        "p95": round(float(_percentile(numeric, 0.95)), 6),
        "max": round(max(numeric), 6),
    }


def _id_set_fingerprint(memories: Sequence[Mapping[str, Any]]) -> str:
    ids = sorted(str(memory.get("id") or "").lower() for memory in memories)
    return hashlib.sha256("".join(f"{memory_id}\n" for memory_id in ids).encode("utf-8")).hexdigest()


def _run_metrics(run: RunArtifacts, manifest: LoadedManifest) -> dict[str, Any]:
    memories = run.memories
    limits = _manifest_limits(manifest)
    exact_hashes = [str(memory["memory_sha256"]) for memory in memories]
    normalized_hashes = [str(memory["normalized_memory_sha256"]) for memory in memories]
    sources = {str(memory["source_path_sha256"]) for memory in memories}
    conversations = {str(memory["conversation_id_sha256"]) for memory in memories}
    source_pairs = {(str(memory["source_path_sha256"]), str(memory["conversation_id_sha256"])) for memory in memories}
    category_assignments = [
        str(category_hash) for memory in memories for category_hash in memory.get("categories_sha256", [])
    ]
    attribution = Counter()
    for memory in memories:
        value = memory.get("attributed_to")
        if value in {"assistant", "system", "user"}:
            attribution[str(value)] += 1
        elif memory.get("attributed_to_sha256"):
            attribution["invalid"] += 1
        else:
            attribution["missing"] += 1

    structurally_valid = 0
    citation_manifest_valid = 0
    core_structurally_valid = 0
    core_manifest_valid = 0
    core_evidence_comparable = 0
    core_evidence_valid = 0
    overlap_only_citations = 0
    citation_subset_of_core = 0
    for memory in memories:
        indices = memory.get("source_message_indices")
        core_indices = memory.get("core_source_message_indices")
        pair = (str(memory["source_path_sha256"]), str(memory["conversation_id_sha256"]))
        limit = limits.get(pair)
        citations_in_range = False
        if _valid_indices(indices):
            structurally_valid += 1
            if limit is not None and all(index < limit for index in indices):
                citation_manifest_valid += 1
                citations_in_range = True
        if _valid_indices(core_indices):
            core_structurally_valid += 1
            if limit is not None and all(index < limit for index in core_indices):
                core_manifest_valid += 1
                if citations_in_range:
                    core_evidence_comparable += 1
                    if set(indices).intersection(core_indices):
                        core_evidence_valid += 1
                    else:
                        overlap_only_citations += 1
                    if set(indices).issubset(core_indices):
                        citation_subset_of_core += 1

    confidence = [
        float(memory["confidence"])
        for memory in memories
        if isinstance(memory.get("confidence"), (int, float))
        and not isinstance(memory.get("confidence"), bool)
        and 0 <= float(memory["confidence"]) <= 1
    ]
    characters = [
        float(memory["memory_characters"])
        for memory in memories
        if isinstance(memory.get("memory_characters"), int) and memory["memory_characters"] >= 0
    ]
    elapsed = float((run.summary.get("run") or {}).get("elapsed_seconds") or 0)
    source_tokens = int((run.summary.get("dataset") or {}).get("source_tokens") or 0)
    total_chunks = int(run.job.get("total_chunks") or 0)
    memory_count = len(memories)
    return {
        "artifacts_sha256": run.file_sha256,
        "attribution": dict(sorted(attribution.items())),
        "categories": {
            "assignment_count": len(category_assignments),
            "categorized_rows": sum(bool(memory.get("categories_sha256")) for memory in memories),
            "coverage": round(sum(bool(memory.get("categories_sha256")) for memory in memories) / memory_count, 6),
            "label_union_count": len(set(category_assignments)),
        },
        "citations": {
            "chunk_source_membership_requires_database": True,
            "citation_manifest_range_valid_rows": citation_manifest_valid,
            "citation_manifest_valid_rate": round(citation_manifest_valid / memory_count, 6),
            "citation_subset_of_core_rows": citation_subset_of_core,
            "combined_citation_core_valid_rate": round(core_evidence_valid / memory_count, 6),
            "core_evidence_comparable_rows": core_evidence_comparable,
            "core_evidence_intersection_valid_rows": core_evidence_valid,
            "core_evidence_invalid_rows": memory_count - core_evidence_valid,
            "core_evidence_valid_rate": round(core_evidence_valid / memory_count, 6),
            "core_manifest_range_valid_rows": core_manifest_valid,
            "core_structure_valid_rows": core_structurally_valid,
            "overlap_only_citation_rows": overlap_only_citations,
            "structure_valid_rows": structurally_valid,
        },
        "confidence": {"numeric_in_range_rows": len(confidence), **_distribution(confidence)},
        "conversation_count": len(conversations),
        "exact_distinct_texts": len(set(exact_hashes)),
        "exact_duplicate_rows": memory_count - len(set(exact_hashes)),
        "id_set_sha256": _id_set_fingerprint(memories),
        "memory_characters": _distribution(characters),
        "memory_count": memory_count,
        "normalized_distinct_texts": len(set(normalized_hashes)),
        "normalized_duplicate_rows": memory_count - len(set(normalized_hashes)),
        "performance": {
            "chunks_per_minute": round(total_chunks / elapsed * 60, 6) if elapsed > 0 else None,
            "elapsed_seconds": round(elapsed, 3),
            "memories_per_minute": round(memory_count / elapsed * 60, 6) if elapsed > 0 else None,
            "source_tokens_per_minute": round(source_tokens / elapsed * 60, 6) if elapsed > 0 else None,
        },
        "retry_count": int(run.job.get("retry_count") or 0),
        "failed_chunks": int(run.job.get("failed_chunks") or 0),
        "imported_chunks": int(run.job.get("imported_chunks") or 0),
        "processed_chunks": int(run.job.get("processed_chunks") or 0),
        "source_count": len(sources),
        "source_pair_count": len(source_pairs),
        "split_chunks": int(run.job.get("split_chunks") or 0),
        "status": run.job.get("status"),
        "total_chunks": total_chunks,
    }


def _directional_overlap(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, Any]:
    right_values = {str(item[key]) for item in right}
    matches = sum(str(item[key]) in right_values for item in left)
    return {"matches": matches, "n": len(left), "rate": round(matches / len(left), 6) if left else None}


def _set_overlap(left: set[Any], right: set[Any]) -> dict[str, Any]:
    intersection = len(left & right)
    union = len(left | right)
    return {
        "intersection": intersection,
        "jaccard": round(intersection / union, 6) if union else None,
        "left_only": len(left - right),
        "right_only": len(right - left),
        "union": union,
    }


def _distribution_distance(left: Counter[str], right: Counter[str]) -> dict[str, Any]:
    labels = set(left) | set(right)
    left_total = sum(left.values())
    right_total = sum(right.values())
    if not labels or not left_total or not right_total:
        return {"jensen_shannon_divergence_natural_log": None, "total_variation_distance": None}
    jsd = 0.0
    tvd = 0.0
    for label in labels:
        left_probability = left[label] / left_total
        right_probability = right[label] / right_total
        midpoint = (left_probability + right_probability) / 2
        if left_probability:
            jsd += 0.5 * left_probability * math.log(left_probability / midpoint)
        if right_probability:
            jsd += 0.5 * right_probability * math.log(right_probability / midpoint)
        tvd += abs(left_probability - right_probability)
    return {
        "jensen_shannon_divergence_natural_log": round(jsd, 6),
        "total_variation_distance": round(tvd / 2, 6),
    }


def _config_difference_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if type(left) is not type(right):
        return [prefix]
    if isinstance(left, Mapping):
        differences: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                differences.append(path)
            else:
                differences.extend(_config_difference_paths(left[key], right[key], path))
        return differences
    if isinstance(left, list):
        return [] if left == right else [prefix]
    return [] if left == right else [prefix]


def _service_identity(preflight: Mapping[str, Any], service: str) -> dict[str, Any] | None:
    configured_services = preflight.get("configured_services")
    if not isinstance(configured_services, Mapping):
        return None
    value = configured_services.get(service)
    if not isinstance(value, Mapping):
        return None
    base_urls = value.get("base_url_sha256")
    return {
        "base_url_sha256": sorted(base_urls) if isinstance(base_urls, list) else base_urls,
        "configured": value.get("configured"),
        "model": value.get("model"),
        "provider": value.get("provider"),
    }


def _valid_service_identity(identity: Mapping[str, Any] | None) -> bool:
    if not isinstance(identity, Mapping):
        return False
    base_urls = identity.get("base_url_sha256")
    return bool(
        identity.get("configured") is True
        and isinstance(identity.get("model"), str)
        and identity["model"]
        and isinstance(identity.get("provider"), str)
        and identity["provider"]
        and isinstance(base_urls, list)
        and all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in base_urls)
    )


def _stable_preflight_view(preflight: Mapping[str, Any]) -> dict[str, Any]:
    embedder = preflight.get("embedder") if isinstance(preflight.get("embedder"), Mapping) else {}
    isolation = preflight.get("project_isolation") if isinstance(preflight.get("project_isolation"), Mapping) else {}
    workspace = preflight.get("workspace_settings")
    return {
        "api_health": preflight.get("api_health"),
        "configured_services": {
            "embedder": _service_identity(preflight, "embedder"),
            "llm": _service_identity(preflight, "llm"),
        },
        "embedder": {"dimensions": embedder.get("dimensions"), "status": embedder.get("status")},
        "project_isolation_status": isolation.get("status"),
        "status": preflight.get("status"),
        "workspace_settings": dict(workspace) if isinstance(workspace, Mapping) else None,
    }


def _valid_stable_preflight(preflight: Mapping[str, Any]) -> bool:
    view = _stable_preflight_view(preflight)
    embedder = view["embedder"]
    dimensions = embedder.get("dimensions")
    return bool(
        view["status"] == "ok"
        and view["api_health"] == "ok"
        and embedder.get("status") == "ok"
        and isinstance(dimensions, int)
        and not isinstance(dimensions, bool)
        and dimensions > 0
        and view["project_isolation_status"] == "empty"
        and isinstance(view["workspace_settings"], Mapping)
        and _valid_service_identity(view["configured_services"]["llm"])
        and _valid_service_identity(view["configured_services"]["embedder"])
    )


def _environment_preflight_services_match(run: RunArtifacts) -> bool:
    environment_services = run.environment.get("preflight_services")
    preflight_services = run.preflight.get("configured_services")
    return (
        isinstance(environment_services, Mapping)
        and isinstance(preflight_services, Mapping)
        and dict(environment_services) == dict(preflight_services)
    )


def _model_route_identity(run: RunArtifacts) -> dict[str, Any]:
    environment_run = run.environment.get("run") if isinstance(run.environment.get("run"), Mapping) else {}
    summary_run = run.summary.get("run") if isinstance(run.summary.get("run"), Mapping) else {}
    options = run.run_config.get("options") if isinstance(run.run_config.get("options"), Mapping) else {}
    services = (
        run.preflight.get("configured_services")
        if isinstance(run.preflight.get("configured_services"), Mapping)
        else {}
    )
    llm_identity = _service_identity(run.preflight, "llm") or {}
    phase_values = [value for value in (environment_run.get("phase"), summary_run.get("phase")) if value]
    phase = str(phase_values[0]) if phase_values else ""
    expected_route = "memory_import" if phase == "tiered" else "runtime" if phase == "optimized_pro" else None
    expected_models = (
        [options.get("fast_model"), options.get("fallback_model")]
        if phase == "tiered"
        else [options.get("fallback_model") or llm_identity.get("model")]
    )
    expected_models = [str(model) for model in expected_models if isinstance(model, str) and model]
    models = run.preflight.get("models")
    probes = models if isinstance(models, list) else []
    failures: list[str] = []
    if not phase_values or len(set(phase_values)) != 1 or expected_route is None:
        failures.append("run phase is missing, inconsistent, or unsupported")
    if options.get("model_tiering_enabled") is not (phase == "tiered"):
        failures.append("run phase and model_tiering_enabled disagree")
    configured_route = services.get("model_route")
    if configured_route != expected_route:
        failures.append("configured model route does not match the run phase")
    if not probes or any(not isinstance(probe, Mapping) for probe in probes):
        failures.append("model probe evidence is missing or invalid")
        probe_mappings: list[Mapping[str, Any]] = []
    else:
        probe_mappings = [probe for probe in probes if isinstance(probe, Mapping)]
    probed_models = [str(probe.get("model") or "") for probe in probe_mappings]
    if probed_models != expected_models:
        failures.append("probed models do not match the phase model inputs")

    route_providers: set[str] = set()
    route_base_urls: set[str] = set()
    for probe in probe_mappings:
        model = str(probe.get("model") or "")
        if probe.get("status") != "ok" or probe.get("route") != expected_route:
            failures.append(f"model probe {model!r} did not verify the expected route")
            continue
        if expected_route != "memory_import":
            continue
        route_provider = probe.get("route_provider")
        route_model = probe.get("route_model")
        route_base_url = probe.get("route_base_url_sha256")
        if (
            probe.get("route_configured") is not True
            or probe.get("route_effective") is not True
            or route_provider != llm_identity.get("provider")
            or route_model != model
            or not isinstance(route_base_url, str)
            or SHA256_RE.fullmatch(route_base_url) is None
        ):
            failures.append(f"model probe {model!r} has invalid effective-route evidence")
            continue
        route_providers.add(str(route_provider))
        route_base_urls.add(route_base_url)

    return {
        "configured_route": configured_route,
        "expected_route": expected_route,
        "phase": phase or None,
        "probed_models": probed_models,
        "route_base_url_sha256": sorted(route_base_urls),
        "route_providers": sorted(route_providers),
        "verification_failures": failures,
        "verified": not failures,
    }


def _comparability(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
    baseline_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    baseline_hashes = baseline.environment.get("relevant_file_sha256") or {}
    candidate_hashes = candidate.environment.get("relevant_file_sha256") or {}
    if not isinstance(baseline_hashes, Mapping):
        baseline_hashes = {}
    if not isinstance(candidate_hashes, Mapping):
        candidate_hashes = {}
    files = sorted(set(baseline_hashes) | set(candidate_hashes))
    changed_files = [name for name in files if baseline_hashes.get(name) != candidate_hashes.get(name)]
    runtime_changed = [name for name in changed_files if name in RUNTIME_FILES]
    baseline_invalid_code_hashes = sorted(
        name
        for name in REQUIRED_CODE_FILES
        if not isinstance(baseline_hashes.get(name), str) or SHA256_RE.fullmatch(baseline_hashes[name]) is None
    )
    candidate_invalid_code_hashes = sorted(
        name
        for name in REQUIRED_CODE_FILES
        if not isinstance(candidate_hashes.get(name), str) or SHA256_RE.fullmatch(candidate_hashes[name]) is None
    )
    config_differences = _config_difference_paths(baseline.run_config, candidate.run_config)
    unexpected_config = [path for path in config_differences if path not in ALLOWED_CONFIG_DIFFERENCES]
    same_dataset = baseline_manifest.get("dataset_sha256") == candidate_manifest.get("dataset_sha256")
    same_dataset_records = _manifest_view(baseline_manifest) == _manifest_view(candidate_manifest)
    baseline_preflight = _stable_preflight_view(baseline.preflight)
    candidate_preflight = _stable_preflight_view(candidate.preflight)
    preflight_differences = _config_difference_paths(baseline_preflight, candidate_preflight)
    baseline_preflight_valid = _valid_stable_preflight(baseline.preflight)
    candidate_preflight_valid = _valid_stable_preflight(candidate.preflight)
    baseline_services_bound = _environment_preflight_services_match(baseline)
    candidate_services_bound = _environment_preflight_services_match(candidate)
    baseline_llm = _service_identity(baseline.preflight, "llm")
    candidate_llm = _service_identity(candidate.preflight, "llm")
    baseline_embedder = _service_identity(baseline.preflight, "embedder")
    candidate_embedder = _service_identity(candidate.preflight, "embedder")
    llm_identity_equal = (
        _valid_service_identity(baseline_llm)
        and _valid_service_identity(candidate_llm)
        and baseline_llm == candidate_llm
    )
    embedder_identity_equal = (
        _valid_service_identity(baseline_embedder)
        and _valid_service_identity(candidate_embedder)
        and baseline_embedder == candidate_embedder
    )
    baseline_route = _model_route_identity(baseline)
    candidate_route = _model_route_identity(candidate)
    runtime_hashes_equal = (
        not runtime_changed
        and not (set(baseline_invalid_code_hashes) & RUNTIME_FILES)
        and not (set(candidate_invalid_code_hashes) & RUNTIME_FILES)
    )
    harness_hash_equal = (
        HARNESS_FILE not in changed_files
        and HARNESS_FILE not in baseline_invalid_code_hashes
        and HARNESS_FILE not in candidate_invalid_code_hashes
    )
    stable_preflight_equal = not preflight_differences
    pure_routing_ab = bool(
        same_dataset_records
        and not unexpected_config
        and runtime_hashes_equal
        and harness_hash_equal
        and stable_preflight_equal
        and baseline_preflight_valid
        and candidate_preflight_valid
        and baseline_services_bound
        and candidate_services_bound
        and llm_identity_equal
        and embedder_identity_equal
        and baseline_route["verified"]
        and candidate_route["verified"]
    )
    reasons: list[str] = []
    if not same_dataset_records:
        reasons.append("Frozen manifest records differ beyond parser identity.")
    if unexpected_config:
        reasons.append("Run configuration differs outside the expected project, entity, and tiering fields.")
    if not runtime_hashes_equal:
        reasons.append("Captured runtime code hashes are incomplete, invalid, or differ between runs.")
    if not harness_hash_equal:
        reasons.append("Captured benchmark harness hashes are incomplete, invalid, or differ between runs.")
    if not stable_preflight_equal:
        reasons.append("Stable preflight identities differ between runs.")
    if not baseline_preflight_valid or not candidate_preflight_valid:
        reasons.append("One or both stable preflight identities are incomplete or invalid.")
    if not baseline_services_bound or not candidate_services_bound:
        reasons.append("One or both preflight service identities do not match their frozen environments.")
    if not llm_identity_equal:
        reasons.append("Configured LLM provider identities are missing, invalid, or different.")
    if not embedder_identity_equal:
        reasons.append("Configured embedder identities are missing, invalid, or different.")
    if not baseline_route["verified"] or not candidate_route["verified"]:
        reasons.append("One or both model-route identities are not verified against their run phases.")
    return {
        "allowed_config_difference_paths": sorted(ALLOWED_CONFIG_DIFFERENCES & set(config_differences)),
        "baseline_invalid_or_missing_code_hashes": baseline_invalid_code_hashes,
        "changed_relevant_files": changed_files,
        "configured_embedder_identity_equal": embedder_identity_equal,
        "configured_llm_identity_equal": llm_identity_equal,
        "candidate_invalid_or_missing_code_hashes": candidate_invalid_code_hashes,
        "dataset_records_equal_ignoring_parser_identity": same_dataset_records,
        "harness_hash_changed": "scripts/chat_import_benchmark.py" in changed_files,
        "harness_hash_equal_and_valid": harness_hash_equal,
        "model_route_identity": {"baseline": baseline_route, "candidate": candidate_route},
        "preflight_environment_services_match": {
            "baseline": baseline_services_bound,
            "candidate": candidate_services_bound,
        },
        "stable_preflight_difference_paths": preflight_differences,
        "stable_preflight_identity_equal": stable_preflight_equal,
        "stable_preflight_identity_valid": {
            "baseline": baseline_preflight_valid,
            "candidate": candidate_preflight_valid,
        },
        "pure_model_routing_ab": pure_routing_ab,
        "raw_dataset_identity_equal": same_dataset,
        "reasons": reasons,
        "runtime_hashes_equal_and_valid": runtime_hashes_equal,
        "runtime_hash_changed_files": runtime_changed,
        "unexpected_config_difference_paths": unexpected_config,
        "unchanged_relevant_files": [name for name in files if name not in changed_files],
    }


def compare_runs(
    baseline: RunArtifacts,
    candidate: RunArtifacts,
    baseline_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    bound_baseline_manifest = _bind_manifest(baseline, baseline_manifest, "baseline")
    bound_candidate_manifest = _bind_manifest(candidate, candidate_manifest, "candidate")
    baseline_dataset = (baseline.summary.get("dataset") or {}).get("dataset_sha256")
    candidate_dataset = (candidate.summary.get("dataset") or {}).get("dataset_sha256")
    manifest_dataset = bound_baseline_manifest.get("dataset_sha256")
    if (
        len(
            {
                baseline_dataset,
                candidate_dataset,
                manifest_dataset,
                bound_candidate_manifest.get("dataset_sha256"),
            }
        )
        != 1
    ):
        raise ComparisonError("Run summaries and manifests do not share one raw dataset identity.")

    baseline_metrics = _run_metrics(baseline, bound_baseline_manifest)
    candidate_metrics = _run_metrics(candidate, bound_candidate_manifest)
    baseline_sources = {memory["source_path_sha256"] for memory in baseline.memories}
    candidate_sources = {memory["source_path_sha256"] for memory in candidate.memories}
    baseline_conversations = {memory["conversation_id_sha256"] for memory in baseline.memories}
    candidate_conversations = {memory["conversation_id_sha256"] for memory in candidate.memories}
    baseline_pairs = {(memory["source_path_sha256"], memory["conversation_id_sha256"]) for memory in baseline.memories}
    candidate_pairs = {
        (memory["source_path_sha256"], memory["conversation_id_sha256"]) for memory in candidate.memories
    }
    baseline_categories = Counter(
        category for memory in baseline.memories for category in memory.get("categories_sha256", [])
    )
    candidate_categories = Counter(
        category for memory in candidate.memories for category in memory.get("categories_sha256", [])
    )
    baseline_attribution = Counter(str(memory.get("attributed_to") or "missing") for memory in baseline.memories)
    candidate_attribution = Counter(str(memory.get("attributed_to") or "missing") for memory in candidate.memories)
    baseline_elapsed = baseline_metrics["performance"]["elapsed_seconds"]
    candidate_elapsed = candidate_metrics["performance"]["elapsed_seconds"]
    baseline_throughput = baseline_metrics["performance"]["source_tokens_per_minute"]
    candidate_throughput = candidate_metrics["performance"]["source_tokens_per_minute"]

    return {
        "schema": SCHEMA,
        "privacy": {
            "aggregate_only": True,
            "contains_per_memory_hashes": False,
            "contains_raw_memory_or_source_text": False,
            "input_artifacts_required_sanitized": True,
        },
        "dataset": {
            "dataset_sha256": manifest_dataset,
            "baseline_manifest_file_sha256": bound_baseline_manifest.file_sha256,
            "baseline_manifest_sha256": bound_baseline_manifest.get("manifest_sha256"),
            "candidate_manifest_file_sha256": bound_candidate_manifest.file_sha256,
            "candidate_manifest_sha256": bound_candidate_manifest.get("manifest_sha256"),
            "file_count": (bound_candidate_manifest.get("summary") or {}).get("file_count"),
            "message_count": (bound_candidate_manifest.get("summary") or {}).get("message_count"),
            "source_tokens": (bound_candidate_manifest.get("summary") or {}).get("source_tokens"),
        },
        "comparability": _comparability(
            baseline,
            candidate,
            bound_baseline_manifest,
            bound_candidate_manifest,
        ),
        "runs": {"baseline": baseline_metrics, "candidate": candidate_metrics},
        "comparison": {
            "attribution_distribution": _distribution_distance(baseline_attribution, candidate_attribution),
            "category_coverage_absolute_change": round(
                candidate_metrics["categories"]["coverage"] - baseline_metrics["categories"]["coverage"], 6
            ),
            "category_distribution": {
                "label_union_count": len(set(baseline_categories) | set(candidate_categories)),
                **_distribution_distance(baseline_categories, candidate_categories),
            },
            "conversation_emission": _set_overlap(baseline_conversations, candidate_conversations),
            "exact_text": {
                "baseline_to_candidate": _directional_overlap(baseline.memories, candidate.memories, "memory_sha256"),
                "candidate_to_baseline": _directional_overlap(candidate.memories, baseline.memories, "memory_sha256"),
                "set": _set_overlap(
                    {memory["memory_sha256"] for memory in baseline.memories},
                    {memory["memory_sha256"] for memory in candidate.memories},
                ),
            },
            "normalized_text": {
                "baseline_to_candidate": _directional_overlap(
                    baseline.memories, candidate.memories, "normalized_memory_sha256"
                ),
                "candidate_to_baseline": _directional_overlap(
                    candidate.memories, baseline.memories, "normalized_memory_sha256"
                ),
                "set": _set_overlap(
                    {memory["normalized_memory_sha256"] for memory in baseline.memories},
                    {memory["normalized_memory_sha256"] for memory in candidate.memories},
                ),
            },
            "performance": {
                "candidate_memory_count_ratio": round(
                    candidate_metrics["memory_count"] / baseline_metrics["memory_count"], 6
                ),
                "candidate_throughput_ratio": round(candidate_throughput / baseline_throughput, 6)
                if baseline_throughput
                else None,
                "elapsed_speedup_baseline_over_candidate": round(baseline_elapsed / candidate_elapsed, 6)
                if candidate_elapsed
                else None,
            },
            "source_emission": _set_overlap(baseline_sources, candidate_sources),
            "source_pair_emission": _set_overlap(baseline_pairs, candidate_pairs),
        },
        "interpretation": {
            "exact_hash_overlap_is_wording_sensitive": True,
            "semantic_accuracy_or_recall_scored": False,
            "semantic_comparison_requires_separate_vector_queries": True,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--candidate-run", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = compare_runs(
            load_run(args.baseline_run),
            load_run(args.candidate_run),
            load_manifest(args.baseline_manifest),
            load_manifest(args.candidate_manifest),
        )
    except ComparisonError as exc:
        raise SystemExit(f"comparison refused: {exc}") from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
