import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.dont_write_bytecode = True
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chat_import_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("chat_import_benchmark", _SCRIPT)
chat_import_benchmark = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules[_SPEC.name] = chat_import_benchmark
_SPEC.loader.exec_module(chat_import_benchmark)


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "chat.md").write_text(
        "# Preferences\n\n"
        "#### You:\nI prefer oolong and the private phrase is alpha-bravo.\n\n"
        "#### Plugin (image):\n![upload](data:image/png;base64,QUJDRA==)\n\n"
        "#### ChatGPT:\nUnderstood.\n",
        encoding="utf-8",
    )
    return root


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _detached_launch(tmp_path: Path):
    dataset = _dataset(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = chat_import_benchmark.build_manifest(dataset)
    _write_manifest(manifest_path, manifest)
    run_id = "tiered-detached-r01"
    project_id = "benchmark-tiered-detached-r01"
    entity_id = f"benchmark-user-{manifest['dataset_sha256'][:16]}"
    output = tmp_path / "runs" / run_id
    output.mkdir(parents=True)
    _write_manifest(
        output / "environment.json",
        {
            "captured_at": "2026-07-14T00:00:00+00:00",
            "docker": {"compose_project": "test-compose"},
            "manifest": {
                "dataset_sha256": manifest["dataset_sha256"],
                "file_sha256": chat_import_benchmark._sha256_file(manifest_path),
                "manifest_sha256": manifest["manifest_sha256"],
            },
            "run": {
                "entity_id": entity_id,
                "phase": "tiered",
                "project_id": project_id,
                "run_id": run_id,
            },
        },
    )
    _write_manifest(
        output / "preflight.json",
        {"project_isolation": {"status": "empty"}, "status": "ok"},
    )
    _write_manifest(
        output / "run-config.json",
        {
            "api": {
                "api_key": "[redacted]",
                "api_url": "http://api.example",
                "project_id": project_id,
                "request_timeout_seconds": 180.0,
            },
            "artifact_policy": {
                "include_sensitive_artifacts": False,
                "source_messages_included": False,
            },
            "options": {
                "audit_ratio": 0.07,
                "chunk_max_tokens": 6000,
                "chunk_overlap_turns": 1,
                "chunk_target_tokens": 5000,
                "entities": [{"id": entity_id, "type": "user"}],
                "fallback_model": "gemini-2.5-pro",
                "fast_model": "gemini-2.5-flash",
                "model_tiering_enabled": True,
                "source_app": "auto",
                "workers": 3,
            },
        },
    )
    api = chat_import_benchmark.ApiSettings("http://api.example", project_id=project_id)
    return api, manifest, manifest_path, output, entity_id


def _completed_detached_job(manifest: dict, **updates):
    job = {
        "completed_at": "2026-07-14T00:01:00+00:00",
        "created_at": "2026-07-14T00:00:05+00:00",
        "error_count": 1,
        "failed_chunks": 0,
        "id": "job-detached",
        "configured_workers": 3,
        "entities": {"user_id": f"benchmark-user-{manifest['dataset_sha256'][:16]}"},
        "infer": True,
        "input_files": ["chat.md"],
        "memories_created": 1,
        "parsed_files": manifest["summary"]["file_count"],
        "processed_chunks": 1,
        "retry_count": 1,
        "source_app": "auto",
        "status": "completed",
        "total_chunks": 1,
        "total_conversations": manifest["summary"]["conversation_count"],
        "total_input_files": manifest["summary"]["file_count"],
    }
    job.update(updates)
    return job


def test_manifest_is_deterministic_text_free_and_uses_raw_dataset_identity(tmp_path):
    root = _dataset(tmp_path)

    first = chat_import_benchmark.build_manifest(root)
    second = chat_import_benchmark.build_manifest(root)

    assert first == second
    serialized = json.dumps(first, ensure_ascii=False)
    assert "alpha-bravo" not in serialized
    assert "QUJDRA==" not in serialized
    assert "conversation_title" not in serialized
    assert '"path"' not in serialized
    assert '"id"' not in serialized
    assert first["summary"]["file_count"] == 1
    assert first["summary"]["conversation_count"] == 1
    assert first["summary"]["message_count"] == 3
    assert first["summary"]["inline_base64_data_uri_count"] == 1
    assert first["files"][0]["asset_flags"] == ["inline_base64_data_uri"]
    assert first["files"][0]["path_sha256"] == hashlib.sha256(b"chat.md").hexdigest()
    assert first["files"][0]["conversations"][0]["id_sha256"] == hashlib.sha256(b"chat").hexdigest()

    raw = (root / "chat.md").read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()
    identity = f"chat.md\t{len(raw)}\t{raw_sha}\n".encode()
    assert first["dataset_sha256"] == hashlib.sha256(identity).hexdigest()
    chat_import_benchmark.verify_manifest_integrity(first)


def test_validate_manifest_detects_source_and_manifest_mutations(tmp_path):
    root = _dataset(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = chat_import_benchmark.build_manifest(root)
    _write_manifest(manifest_path, manifest)

    loaded = chat_import_benchmark.load_manifest(manifest_path)
    assert chat_import_benchmark.validate_manifest(root, loaded)["status"] == "valid"

    (root / "chat.md").write_text(
        (root / "chat.md").read_text(encoding="utf-8") + "\nAdditional source material.\n",
        encoding="utf-8",
    )
    with pytest.raises(chat_import_benchmark.ManifestMismatch, match="changed="):
        chat_import_benchmark.validate_manifest(root, loaded)

    tampered = dict(loaded)
    tampered["summary"] = dict(tampered["summary"], file_count=99)
    _write_manifest(manifest_path, tampered)
    with pytest.raises(chat_import_benchmark.ManifestMismatch, match="self-hash mismatch"):
        chat_import_benchmark.load_manifest(manifest_path)


def test_preflight_probes_health_config_all_models_and_embedder():
    probes = []

    def handler(request: httpx.Request) -> httpx.Response:
        probes.append((request.method, request.url.path, request.content))
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/configure":
            return httpx.Response(
                200,
                json={
                    "llm": {
                        "provider": "openai",
                        "config": {
                            "api_key": "[redacted]",
                            "model": "gemini-2.5-pro",
                            "openai_base_url": "https://gateway.example/v1",
                        },
                    },
                    "embedder": {
                        "provider": "openai",
                        "config": {"api_key": "[redacted]", "model": "embedding-model"},
                    },
                },
            )
        if request.url.path == "/settings/workspace":
            return httpx.Response(
                200,
                json={
                    "categories": [{"name": "preference", "description": "Durable preferences"}],
                    "extraction": {"multilingual": True},
                    "retention": {"memory_decay": True, "expiration_date": None},
                },
            )
        if request.url.path == "/configure/test":
            body = json.loads(request.content)
            if body["kind"] == "llm":
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "preview": "YiQiao OK",
                        "latency_ms": 12.5,
                        "route": body.get("route", "runtime"),
                        "route_configured": True,
                        "route_effective": True,
                        "route_model": body["config"]["model"],
                        "route_provider": "openai",
                        "route_base_url_sha256": "a" * 64,
                    },
                )
            return httpx.Response(200, json={"status": "ok", "dimensions": 1024, "latency_ms": 4.0})
        raise AssertionError(request.url)

    settings = chat_import_benchmark.ApiSettings(
        "https://api.example", api_key="secret", project_id="benchmark-project"
    )
    with chat_import_benchmark.ApiClient(settings, transport=httpx.MockTransport(handler)) as client:
        result = chat_import_benchmark.perform_preflight(
            client,
            models=["gemini-2.5-pro", "gemini-2.5-flash"],
            require_runtime_model="gemini-2.5-pro",
            model_route="memory_import",
        )

    assert result["status"] == "ok"
    assert [item["model"] for item in result["models"]] == ["gemini-2.5-pro", "gemini-2.5-flash"]
    assert result["embedder"]["dimensions"] == 1024
    assert result["configured_services"]["model_route"] == "memory_import"
    assert all(item["route"] == "memory_import" for item in result["models"])
    assert all(item["route_model"] == item["model"] for item in result["models"])
    assert result["workspace_settings"]["category_count"] == 1
    assert len([probe for probe in probes if probe[1] == "/configure/test"]) == 3
    assert all(b"secret" not in content for _, _, content in probes)
    llm_probe_bodies = [json.loads(content) for _, path, content in probes if path == "/configure/test"][:2]
    assert all(body["route"] == "memory_import" for body in llm_probe_bodies)
    serialized = json.dumps(result)
    assert "secret" not in serialized
    assert "https://gateway.example" not in serialized


@pytest.mark.parametrize(
    ("route_response", "error"),
    [
        (
            {"route": "memory_import", "route_configured": False, "route_effective": False},
            "explicitly configured, effective",
        ),
        (
            {"route": "runtime", "route_configured": True, "route_effective": True},
            "reported route",
        ),
        (
            {
                "route": "memory_import",
                "route_configured": True,
                "route_effective": True,
                "route_base_url_sha256": None,
            },
            "base URL fingerprint",
        ),
        (
            {
                "route": "memory_import",
                "route_configured": True,
                "route_effective": True,
                "route_provider": "anthropic",
            },
            "expected 'openai'",
        ),
        (
            {
                "route": "memory_import",
                "route_configured": True,
                "route_effective": True,
                "route_model": "runtime-model",
            },
            "every requested model must be probed",
        ),
        (
            {
                "route": "memory_import",
                "route_configured": True,
                "route_effective": True,
                "route_api_key": "leaked-secret",
            },
            "unsupported route metadata",
        ),
    ],
)
def test_tiered_preflight_rejects_spoofed_or_unverifiable_routes(route_response, error):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/configure":
            return httpx.Response(
                200,
                json={
                    "llm": {"provider": "openai", "config": {"model": "runtime-model"}},
                    "embedder": {"provider": "openai", "config": {"model": "embedding-model"}},
                },
            )
        if request.url.path == "/settings/workspace":
            return httpx.Response(200, json={"categories": [], "extraction": {}, "retention": {}})
        if request.url.path == "/configure/test":
            body = json.loads(request.content)
            if body["kind"] == "embedder":
                return httpx.Response(200, json={"status": "ok", "dimensions": 3})
            response = {
                "status": "ok",
                "preview": "YiQiao OK",
                "route": "memory_import",
                "route_configured": True,
                "route_effective": True,
                "route_model": body["config"]["model"],
                "route_provider": "openai",
                "route_base_url_sha256": "a" * 64,
            }
            response.update(route_response)
            return httpx.Response(200, json=response)
        raise AssertionError(request.url)

    api = chat_import_benchmark.ApiSettings("https://api.example", project_id="benchmark-project")
    with chat_import_benchmark.ApiClient(api, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(chat_import_benchmark.BenchmarkError, match=error) as caught:
            chat_import_benchmark.perform_preflight(
                client,
                models=["fast-model", "fallback-model"],
                model_route="memory_import",
            )
    assert "leaked-secret" not in str(caught.value)


def test_run_command_preflight_failure_creates_no_output(tmp_path, monkeypatch):
    root = _dataset(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    _write_manifest(manifest_path, chat_import_benchmark.build_manifest(root))
    output = tmp_path / "benchmark-output"

    def fail_preflight(*_args, **_kwargs):
        raise chat_import_benchmark.BenchmarkError("provider unavailable")

    monkeypatch.setattr(chat_import_benchmark, "perform_preflight", fail_preflight)
    args = argparse.Namespace(
        api_key_env="",
        api_url="http://api.example",
        audit_ratio=0.07,
        compose_project="test-compose",
        fast_model="gemini-2.5-flash",
        input=root,
        include_sensitive_artifacts=False,
        manifest=manifest_path,
        max_tokens=6000,
        out=output,
        overlap_turns=1,
        phase="optimized_pro",
        poll_seconds=0.001,
        pro_model="gemini-2.5-pro",
        project_id=None,
        repeat=1,
        request_timeout_seconds=5.0,
        run_id="failed-preflight",
        run_timeout_seconds=10.0,
        target_tokens=5000,
        workers=3,
    )

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="provider unavailable"):
        chat_import_benchmark.command_run(args)

    assert not output.exists()


def test_default_diagnostic_artifacts_hash_sensitive_values():
    error = {
        "details": {"upstream": "private details"},
        "message": "private provider failure",
        "phase": "extracting",
        "source": "private-chat-title.md",
        "type": "provider_error",
    }
    artifact = chat_import_benchmark._artifact_error(error, include_sensitive=False)

    serialized = json.dumps(artifact)
    assert "private provider failure" not in serialized
    assert "private-chat-title" not in serialized
    assert "private details" not in serialized
    assert artifact["message_sha256"] == hashlib.sha256(b"private provider failure").hexdigest()
    assert artifact["source_sha256"] == hashlib.sha256(b"private-chat-title.md").hexdigest()


def test_run_once_captures_terminal_results_and_omits_source_chat_text(tmp_path, monkeypatch):
    root = _dataset(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = chat_import_benchmark.build_manifest(root)
    _write_manifest(manifest_path, manifest)
    output = tmp_path / "runs"

    job = {
        "completed_at": "2026-07-14T00:00:10+00:00",
        "created_at": "2026-07-14T00:00:00+00:00",
        "error_count": 0,
        "failed_chunks": 0,
        "id": "job-1",
        "memories_created": 1,
        "parsed_files": manifest["summary"]["file_count"],
        "processed_chunks": 1,
        "retry_count": 0,
        "status": "completed",
        "total_chunks": 1,
        "total_conversations": manifest["summary"]["conversation_count"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/memory-imports":
            return httpx.Response(202, json={"id": "job-1", "status": "queued"})
        if request.method == "GET" and request.url.path == "/memory-imports/job-1":
            return httpx.Response(200, json=job)
        if request.method == "GET" and request.url.path == "/memory-imports/job-1/errors":
            return httpx.Response(200, json={"results": [], "total": 0})
        if request.method == "POST" and request.url.path == "/memories/query":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "categories": ["preference"],
                            "id": "memory-1",
                            "memory": "The user prefers oolong.",
                            "metadata": {
                                "conversation_id": "chat",
                                "source_message_indices": [0],
                                "source_messages": [{"content": "private source chat"}],
                                "source_path": "chat.md",
                            },
                        }
                    ],
                    "total": 1,
                    "total_pages": 1,
                },
            )
        raise AssertionError((request.method, request.url.path))

    monkeypatch.setattr(
        chat_import_benchmark,
        "capture_environment",
        lambda *_args, **_kwargs: {"captured": True},
    )
    settings = chat_import_benchmark.RunSettings(
        phase="tiered",
        run_id="run-1",
        project_id="benchmark-run-1",
        entity_id="benchmark-user-run-1",
        input_root=root,
        manifest_path=manifest_path,
        output_root=output,
        poll_seconds=0.001,
        run_timeout_seconds=5.0,
    )
    api = chat_import_benchmark.ApiSettings("http://api.example", project_id=settings.project_id)

    result = chat_import_benchmark.run_once(
        settings,
        api,
        manifest,
        {"status": "ok"},
        {"status": "empty"},
        transport=httpx.MockTransport(handler),
    )

    assert result["summary"]["run"]["valid_complete_run"] is True
    assert result["summary"]["run"]["operationally_complete"] is True
    assert result["summary"]["acceptance"]["operational_completion"] == {
        "accepted": True,
        "status": "accepted",
    }
    assert result["summary"]["acceptance"]["quality"]["accepted"] is False
    assert result["summary"]["acceptance"]["quality"]["status"] == "not_evaluated"
    assert result["summary"]["acceptance"]["overall"]["accepted"] is False
    assert result["summary"]["acceptance"]["overall"]["status"] == "not_evaluated"
    semantic = result["summary"]["quality_proxies"]["semantic_evaluation"]
    assert semantic["accuracy"] is None
    assert semantic["recall"] is None
    assert "do not establish semantic accuracy or recall" in semantic["reason"]
    assert result["summary"]["run"]["source_tokens_per_minute"] == pytest.approx(
        manifest["summary"]["source_tokens"] * 6
    )
    assert result["summary"]["quality_proxies"]["source_index_structure_validity"] == 1.0
    memory_output = (output / "run-1" / "memories.jsonl").read_text(encoding="utf-8")
    assert "The user prefers oolong" not in memory_output
    assert "private source chat" not in memory_output
    memory_artifact = json.loads(memory_output)
    assert memory_artifact["memory_sha256"] == hashlib.sha256(b"The user prefers oolong.").hexdigest()
    assert memory_artifact["source_message_indices"] == [0]
    assert not (output / "run-1" / "failure.json").exists()


def test_completed_zero_memory_and_partial_runs_fail_operational_completion(tmp_path):
    manifest = chat_import_benchmark.build_manifest(_dataset(tmp_path))
    settings = chat_import_benchmark.RunSettings(
        phase="optimized_pro",
        run_id="invalid-run",
        project_id="invalid-project",
        entity_id="benchmark-user",
        input_root=tmp_path,
        manifest_path=tmp_path / "manifest.json",
        output_root=tmp_path / "output",
    )
    base_job = {
        "status": "completed",
        "parsed_files": manifest["summary"]["file_count"],
        "total_conversations": manifest["summary"]["conversation_count"],
        "total_chunks": 10,
        "processed_chunks": 10,
        "failed_chunks": 0,
        "memories_created": 0,
    }

    empty = chat_import_benchmark.build_summary(settings, manifest, base_job, [], wall_seconds=10)
    partial = chat_import_benchmark.build_summary(
        settings,
        manifest,
        {**base_job, "status": "cancelled", "processed_chunks": 5, "memories_created": 1},
        [{"memory": "fact", "source_message_indices": [0]}],
        wall_seconds=10,
    )

    assert empty["run"]["valid_complete_run"] is False
    assert empty["run"]["operationally_complete"] is False
    assert empty["run"]["source_tokens_per_minute"] is None
    assert empty["acceptance"]["operational_completion"]["accepted"] is False
    assert empty["acceptance"]["overall"] == {
        "accepted": False,
        "reason": "Operational completion requirements were not met.",
        "status": "rejected",
    }
    assert partial["run"]["valid_complete_run"] is False
    assert partial["run"]["source_tokens_per_minute"] is None
    assert partial["acceptance"]["operational_completion"]["accepted"] is False
    assert partial["acceptance"]["quality"]["status"] == "not_evaluated"


def test_operational_rejection_preserves_safe_terminal_artifacts(tmp_path, monkeypatch):
    root = _dataset(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = chat_import_benchmark.build_manifest(root)
    _write_manifest(manifest_path, manifest)
    output = tmp_path / "runs"
    job = {
        "completed_at": "2026-07-14T00:00:10+00:00",
        "created_at": "2026-07-14T00:00:00+00:00",
        "current_file": "private-chat-title.md",
        "error_count": 0,
        "failed_chunks": 0,
        "id": "job-empty",
        "memories_created": 0,
        "parsed_files": manifest["summary"]["file_count"],
        "processed_chunks": 1,
        "status": "completed",
        "total_chunks": 1,
        "total_conversations": manifest["summary"]["conversation_count"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/memory-imports":
            return httpx.Response(202, json=job)
        if request.method == "GET" and request.url.path == "/memory-imports/job-empty/errors":
            return httpx.Response(200, json={"results": [], "total": 0})
        if request.method == "POST" and request.url.path == "/memories/query":
            return httpx.Response(200, json={"results": [], "total": 0, "total_pages": 0})
        raise AssertionError((request.method, request.url.path))

    monkeypatch.setattr(chat_import_benchmark, "capture_environment", lambda *_args: {"captured": True})
    settings = chat_import_benchmark.RunSettings(
        phase="optimized_pro",
        run_id="run-empty",
        project_id="benchmark-run-empty",
        entity_id="benchmark-user-run-empty",
        input_root=root,
        manifest_path=manifest_path,
        output_root=output,
    )
    api = chat_import_benchmark.ApiSettings(
        "http://api.example",
        api_key="benchmark-api-secret",
        project_id=settings.project_id,
    )

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="complete-run coverage requirements"):
        chat_import_benchmark.run_once(
            settings,
            api,
            manifest,
            {"status": "ok"},
            {"status": "empty"},
            transport=httpx.MockTransport(handler),
        )

    run_output = output / "run-empty"
    assert {path.name for path in run_output.iterdir()} >= {
        "errors.json",
        "failure.json",
        "job.json",
        "memories.jsonl",
        "summary.json",
    }
    serialized = "\n".join(path.read_text(encoding="utf-8") for path in run_output.iterdir() if path.is_file())
    assert "alpha-bravo" not in serialized
    assert "private-chat-title" not in serialized
    assert "benchmark-api-secret" not in serialized
    assert (
        json.loads((run_output / "summary.json").read_text(encoding="utf-8"))["acceptance"]["overall"]["status"]
        == "rejected"
    )


def test_detached_capture_publishes_sanitized_terminal_artifacts_without_mutations(tmp_path):
    api, manifest, manifest_path, output, entity_id = _detached_launch(tmp_path)
    job = _completed_detached_job(manifest)
    requests = []
    launch_before = {name: (output / name).read_bytes() for name in chat_import_benchmark.LAUNCH_ARTIFACTS}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        assert request.headers["X-Project-ID"] == api.project_id
        if request.method == "GET" and request.url.path == "/memory-imports/job-detached":
            return httpx.Response(200, json=job)
        if request.method == "GET" and request.url.path == "/memory-imports/job-detached/errors":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "message": "private retry detail",
                            "source": "private-chat-title.md",
                            "type": "provider_pressure",
                        }
                    ],
                    "total": 1,
                },
            )
        if request.method == "POST" and request.url.path == "/memories/query":
            body = json.loads(request.content)
            assert body["filters"] == [{"entity_type": "user", "field": "entity", "value": entity_id}]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "categories": ["preference"],
                            "id": "memory-1",
                            "memory": "The user prefers private oolong.",
                            "metadata": {
                                "conversation_id": "chat",
                                "core_source_message_indices": [0, 1, 2],
                                "project_id": api.project_id,
                                "source_message_indices": [0],
                                "source_messages": [{"content": "private source chat"}],
                                "source_path": "chat.md",
                                "user_id": entity_id,
                            },
                        }
                    ],
                    "total": 1,
                    "total_pages": 1,
                },
            )
        raise AssertionError((request.method, request.url.path))

    result = chat_import_benchmark.capture_existing_run(
        api,
        "job-detached",
        output,
        manifest_path,
        transport=httpx.MockTransport(handler),
    )

    assert result["capture_mode"] == "detached_domain_read_only"
    assert requests == [
        ("GET", "/memory-imports/job-detached"),
        ("GET", "/memory-imports/job-detached/errors"),
        ("POST", "/memories/query"),
    ]
    assert {name for name in chat_import_benchmark.FINAL_RUN_ARTIFACTS if (output / name).is_file()} == set(
        chat_import_benchmark.FINAL_RUN_ARTIFACTS
    )
    assert not (output / "failure.json").exists()
    assert launch_before == {name: (output / name).read_bytes() for name in chat_import_benchmark.LAUNCH_ARTIFACTS}
    serialized = "\n".join(
        (output / name).read_text(encoding="utf-8") for name in chat_import_benchmark.FINAL_RUN_ARTIFACTS
    )
    assert "private oolong" not in serialized
    assert "private source chat" not in serialized
    assert "private retry detail" not in serialized
    assert "private-chat-title" not in serialized
    memory_artifact = json.loads((output / "memories.jsonl").read_text(encoding="utf-8"))
    assert memory_artifact["memory_sha256"] == hashlib.sha256(b"The user prefers private oolong.").hexdigest()
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["run"]["valid_complete_run"] is True
    assert summary["run"]["wall_seconds"] == 60.0


@pytest.mark.parametrize("status", ["importing", "failed", "completed_with_errors", "cancelled"])
def test_detached_capture_refuses_nonclean_terminal_or_nonterminal_status(tmp_path, status):
    api, manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    job = _completed_detached_job(manifest, status=status)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/memory-imports/job-detached"
        return httpx.Response(200, json=job)

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="requires status=completed"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(handler),
        )

    assert not any((output / name).exists() for name in chat_import_benchmark.FINAL_RUN_ARTIFACTS)


def test_detached_capture_refuses_completed_job_with_error_counters(tmp_path):
    api, manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    job = _completed_detached_job(manifest, failed_chunks=1)

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="clean completion counters"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=job)),
        )


def test_detached_capture_refuses_mismatched_job_response(tmp_path):
    api, manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    job = _completed_detached_job(manifest, id="different-job")

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="different job ID"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=job)),
        )


def test_detached_capture_refuses_job_manifest_or_run_config_identity_mismatch(tmp_path):
    api, manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    job = _completed_detached_job(manifest, input_files=["different.md"])

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="job identity does not match"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=job)),
        )


def test_detached_capture_refuses_overwrite_before_api_access(tmp_path):
    api, _manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    (output / "job.json").write_text("existing evidence", encoding="utf-8")

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("overwrite refusal must happen before API access")

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="refuses to overwrite"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(unexpected_request),
        )

    assert (output / "job.json").read_text(encoding="utf-8") == "existing evidence"


def test_detached_capture_refuses_sensitive_launch_policy_before_api_access(tmp_path):
    api, _manifest, manifest_path, output, _entity_id = _detached_launch(tmp_path)
    run_config_path = output / "run-config.json"
    run_config = json.loads(run_config_path.read_text(encoding="utf-8"))
    run_config["artifact_policy"]["include_sensitive_artifacts"] = True
    _write_manifest(run_config_path, run_config)

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("privacy refusal must happen before API access")

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="sanitized launch artifact policy"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            manifest_path,
            transport=httpx.MockTransport(unexpected_request),
        )


def test_detached_capture_refuses_manifest_identity_mismatch_before_api_access(tmp_path):
    api, manifest, _manifest_path, output, _entity_id = _detached_launch(tmp_path)
    mismatched_manifest = dict(manifest)
    mismatched_manifest["parser"] = {"module": "server/chat_import.py", "sha256": "0" * 64}
    mismatched_manifest.pop("manifest_sha256")
    mismatched_manifest["manifest_sha256"] = hashlib.sha256(
        chat_import_benchmark._canonical_json(mismatched_manifest)
    ).hexdigest()
    mismatched_path = tmp_path / "mismatched-manifest.json"
    _write_manifest(mismatched_path, mismatched_manifest)

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        pytest.fail("manifest identity refusal must happen before API access")

    with pytest.raises(chat_import_benchmark.BenchmarkError, match="manifest does not match"):
        chat_import_benchmark.capture_existing_run(
            api,
            "job-detached",
            output,
            mismatched_path,
            transport=httpx.MockTransport(unexpected_request),
        )
