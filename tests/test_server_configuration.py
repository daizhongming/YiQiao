import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)


_PROVIDER_ENV_NAMES = {
    "YIQIAO_LLM_PROVIDER",
    "YIQIAO_LLM_MODEL",
    "YIQIAO_LLM_BASE_URL",
    "YIQIAO_LLM_MAX_TOKENS",
    "YIQIAO_LLM_TIMEOUT_SECONDS",
    "YIQIAO_LLM_MAX_RETRIES",
    "YIQIAO_DEFAULT_INFER",
    "YIQIAO_EMBEDDER_PROVIDER",
    "YIQIAO_EMBEDDER_MODEL",
    "YIQIAO_EMBEDDER_BASE_URL",
    "YIQIAO_EMBEDDER_DIMS",
    "YIQIAO_EMBEDDER_ALLOWED_OPENAI_PARAMS",
    "YIQIAO_RERANK_PROVIDER",
    "YIQIAO_RERANK_LLM_PROVIDER",
    "YIQIAO_RERANK_MODEL",
    "YIQIAO_RERANK_BASE_URL",
    "YIQIAO_RERANK_TOP_K",
    "YIQIAO_RERANK_TEMPERATURE",
    "YIQIAO_RERANK_MAX_TOKENS",
    "MEM0_LLM_PROVIDER",
    "MEM0_LLM_MODEL",
    "MEM0_DEFAULT_LLM_MODEL",
    "MEM0_LLM_BASE_URL",
    "MEM0_LLM_MAX_TOKENS",
    "MEM0_LLM_TIMEOUT_SECONDS",
    "MEM0_LLM_MAX_RETRIES",
    "MEM0_DEFAULT_INFER",
    "MEM0_EMBEDDER_PROVIDER",
    "MEM0_EMBEDDER_MODEL",
    "MEM0_DEFAULT_EMBEDDER_MODEL",
    "MEM0_EMBEDDER_BASE_URL",
    "MEM0_EMBEDDER_DIMS",
    "MEM0_EMBEDDING_DIMS",
    "MEM0_EMBEDDER_ALLOWED_OPENAI_PARAMS",
    "MEM0_RERANK_PROVIDER",
    "MEM0_RERANK_LLM_PROVIDER",
    "MEM0_RERANK_MODEL",
    "MEM0_RERANK_BASE_URL",
    "MEM0_RERANK_TOP_K",
    "MEM0_RERANK_TEMPERATURE",
    "MEM0_RERANK_MAX_TOKENS",
}

_LEGACY_PROVIDER_ENV = {
    "AUTH_DISABLED": "true",
    "JWT_SECRET": "test-secret",
    "LLM_API_KEY": "llm-secret",
    "EMBEDDING_API_KEY": "embedding-secret",
    "RERANK_API_KEY": "rerank-secret",
    "MEM0_LLM_PROVIDER": "openai",
    "MEM0_LLM_MODEL": "llm-model",
    "MEM0_LLM_BASE_URL": "https://llm.example/v1",
    "MEM0_LLM_MAX_TOKENS": "3500",
    "MEM0_LLM_TIMEOUT_SECONDS": "45.5",
    "MEM0_LLM_MAX_RETRIES": "2",
    "MEM0_DEFAULT_INFER": "off",
    "MEMORY_IMPORT_LLM_PROVIDER": "",
    "MEMORY_IMPORT_LLM_BASE_URL": "",
    "MEMORY_IMPORT_LLM_API_KEY_ENV": "",
    "MEM0_EMBEDDER_PROVIDER": "openai",
    "MEM0_EMBEDDER_MODEL": "embedding-model",
    "MEM0_EMBEDDER_BASE_URL": "https://embedding.example/v1",
    "MEM0_EMBEDDER_DIMS": "768",
    "MEM0_EMBEDDER_ALLOWED_OPENAI_PARAMS": "dimensions, encoding_format",
    "MEM0_RERANK_PROVIDER": "llm_reranker",
    "MEM0_RERANK_MODEL": "rerank-model",
    "MEM0_RERANK_BASE_URL": "https://rerank.example/v1",
    "MEM0_RERANK_LLM_PROVIDER": "openai",
    "MEM0_RERANK_TOP_K": "7",
    "MEM0_RERANK_TEMPERATURE": "0.35",
    "MEM0_RERANK_MAX_TOKENS": "41",
}


def _server_with_environment(overrides):
    mock_memory = MagicMock()
    env = {name: value for name, value in os.environ.items() if name not in _PROVIDER_ENV_NAMES}
    env.update(overrides)
    with patch.dict(os.environ, env, clear=True):
        with patch("mem0.Memory.from_config", return_value=mock_memory):
            import auth as server_auth

            importlib.reload(server_auth)
            import server.main as server_main

            importlib.reload(server_main)
            server_main._persist_request_log = MagicMock()
            server_main.app.dependency_overrides[server_main.require_admin] = lambda: None
            try:
                yield server_main
            finally:
                server_main.app.dependency_overrides.clear()


@pytest.fixture
def configured_server():
    yield from _server_with_environment(_LEGACY_PROVIDER_ENV)


@pytest.fixture
def yiqiao_precedence_server():
    env = {
        **_LEGACY_PROVIDER_ENV,
        "MEM0_LLM_PROVIDER": "anthropic",
        "MEM0_EMBEDDER_PROVIDER": "gemini",
        "MEM0_RERANK_PROVIDER": "legacy-reranker",
        "MEM0_RERANK_LLM_PROVIDER": "anthropic",
        "YIQIAO_LLM_PROVIDER": "openai",
        "YIQIAO_LLM_MODEL": "yiqiao-llm-model",
        "YIQIAO_LLM_BASE_URL": "https://yiqiao-llm.example/v1",
        "YIQIAO_LLM_MAX_TOKENS": "4200",
        "YIQIAO_LLM_TIMEOUT_SECONDS": "12.25",
        "YIQIAO_LLM_MAX_RETRIES": "5",
        "YIQIAO_DEFAULT_INFER": "yes",
        "YIQIAO_EMBEDDER_PROVIDER": "openai",
        "YIQIAO_EMBEDDER_MODEL": "yiqiao-embedding-model",
        "YIQIAO_EMBEDDER_BASE_URL": "https://yiqiao-embedding.example/v1",
        "YIQIAO_EMBEDDER_DIMS": "1536",
        "YIQIAO_EMBEDDER_ALLOWED_OPENAI_PARAMS": "dimensions,encoding_format,user",
        "YIQIAO_RERANK_PROVIDER": "llm_reranker",
        "YIQIAO_RERANK_LLM_PROVIDER": "openai",
        "YIQIAO_RERANK_MODEL": "yiqiao-rerank-model",
        "YIQIAO_RERANK_BASE_URL": "https://yiqiao-rerank.example/v1",
        "YIQIAO_RERANK_TOP_K": "11",
        "YIQIAO_RERANK_TEMPERATURE": "0.15",
        "YIQIAO_RERANK_MAX_TOKENS": "32",
    }
    yield from _server_with_environment(env)


def test_environment_builds_independent_model_configs(configured_server):
    config = configured_server.DEFAULT_CONFIG

    assert config["llm"]["provider"] == "openai"
    assert config["llm"]["config"]["model"] == "llm-model"
    assert config["llm"]["config"]["api_key"] == "llm-secret"
    assert config["llm"]["config"]["openai_base_url"] == "https://llm.example/v1"
    assert config["llm"]["config"]["max_tokens"] == 3500
    assert config["llm"]["config"]["request_timeout"] == 45.5
    assert config["llm"]["config"]["max_retries"] == 2
    assert configured_server.DEFAULT_MEMORY_INFER is False
    assert config["embedder"]["provider"] == "openai"
    assert config["embedder"]["config"]["model"] == "embedding-model"
    assert config["embedder"]["config"]["api_key"] == "embedding-secret"
    assert config["embedder"]["config"]["openai_base_url"] == "https://embedding.example/v1"
    assert config["embedder"]["config"]["embedding_dims"] == 768
    assert config["embedder"]["config"]["allowed_openai_params"] == ["dimensions", "encoding_format"]
    assert config["vector_store"]["config"]["embedding_model_dims"] == 768
    assert config["reranker"]["config"]["api_key"] == "rerank-secret"
    assert config["reranker"]["config"]["llm"]["config"]["api_key"] == "rerank-secret"
    assert config["reranker"]["config"]["llm"]["config"]["openai_base_url"] == "https://rerank.example/v1"
    assert config["reranker"]["config"]["top_k"] == 7
    assert config["reranker"]["config"]["temperature"] == 0.35
    assert config["reranker"]["config"]["max_tokens"] == 41


def test_yiqiao_provider_environment_takes_precedence(yiqiao_precedence_server):
    config = yiqiao_precedence_server.DEFAULT_CONFIG

    assert config["llm"]["provider"] == "openai"
    assert config["llm"]["config"]["model"] == "yiqiao-llm-model"
    assert config["llm"]["config"]["openai_base_url"] == "https://yiqiao-llm.example/v1"
    assert config["llm"]["config"]["max_tokens"] == 4200
    assert config["llm"]["config"]["request_timeout"] == 12.25
    assert config["llm"]["config"]["max_retries"] == 5
    assert yiqiao_precedence_server.DEFAULT_MEMORY_INFER is True

    assert config["embedder"]["provider"] == "openai"
    assert config["embedder"]["config"]["model"] == "yiqiao-embedding-model"
    assert config["embedder"]["config"]["openai_base_url"] == "https://yiqiao-embedding.example/v1"
    assert config["embedder"]["config"]["embedding_dims"] == 1536
    assert config["embedder"]["config"]["allowed_openai_params"] == [
        "dimensions",
        "encoding_format",
        "user",
    ]
    assert config["vector_store"]["config"]["embedding_model_dims"] == 1536

    reranker = config["reranker"]
    assert reranker["provider"] == "llm_reranker"
    assert reranker["config"]["model"] == "yiqiao-rerank-model"
    assert reranker["config"]["top_k"] == 11
    assert reranker["config"]["temperature"] == 0.15
    assert reranker["config"]["max_tokens"] == 32
    assert reranker["config"]["llm"]["provider"] == "openai"
    assert reranker["config"]["llm"]["config"]["openai_base_url"] == "https://yiqiao-rerank.example/v1"


def test_older_default_model_environment_names_remain_last_resort(configured_server, monkeypatch):
    monkeypatch.delenv("YIQIAO_LLM_MODEL", raising=False)
    monkeypatch.delenv("MEM0_LLM_MODEL", raising=False)
    monkeypatch.setenv("MEM0_DEFAULT_LLM_MODEL", "older-llm-model")
    monkeypatch.delenv("YIQIAO_EMBEDDER_MODEL", raising=False)
    monkeypatch.delenv("MEM0_EMBEDDER_MODEL", raising=False)
    monkeypatch.setenv("MEM0_DEFAULT_EMBEDDER_MODEL", "older-embedder-model")

    assert (
        configured_server._env_value(
            "YIQIAO_LLM_MODEL",
            "MEM0_LLM_MODEL",
            "MEM0_DEFAULT_LLM_MODEL",
        )
        == "older-llm-model"
    )
    assert (
        configured_server._env_value(
            "YIQIAO_EMBEDDER_MODEL",
            "MEM0_EMBEDDER_MODEL",
            "MEM0_DEFAULT_EMBEDDER_MODEL",
        )
        == "older-embedder-model"
    )


def test_tiered_import_llm_route_is_independent_from_runtime_config(configured_server, monkeypatch):
    runtime = {
        "llm": {
            "provider": "openai",
            "config": {
                "api_key": "runtime-secret",
                "model": "runtime-model",
                "openai_base_url": "https://runtime.example/v1",
                "temperature": 0.2,
                "top_p": 0.8,
            },
        }
    }
    configured_server.get_current_config = MagicMock(return_value=runtime)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", True)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_PROVIDER", "openai")
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_BASE_URL", "https://import.example/v1")
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_API_KEY_ENV", "IMPORT_ROUTE_KEY")
    monkeypatch.setenv("IMPORT_ROUTE_KEY", "import-secret")
    configured_server._import_llm_cache.clear()
    routed_llm = MagicMock()

    with patch.object(configured_server.LlmFactory, "create", return_value=routed_llm) as create:
        result = configured_server._import_llm("gemini-fast", use_import_route=True)

    assert result is routed_llm
    assert create.call_args.args[0] == "openai"
    routed_config = create.call_args.args[1]
    assert routed_config["model"] == "gemini-fast"
    assert routed_config["api_key"] == "import-secret"
    assert routed_config["openai_base_url"] == "https://import.example/v1"
    assert routed_config["temperature"] == 0.2
    assert routed_config["top_p"] == 0.8
    assert "runtime-secret" not in routed_config.values()
    assert "https://runtime.example/v1" not in routed_config.values()
    assert configured_server.get_current_config() == runtime


def test_import_llm_without_explicit_route_keeps_runtime_config(configured_server, monkeypatch):
    runtime_config = {
        "api_key": "runtime-secret",
        "model": "runtime-model",
        "openai_base_url": "https://runtime.example/v1",
    }
    configured_server.get_current_config = MagicMock(
        return_value={"llm": {"provider": "openai", "config": runtime_config}}
    )
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", False)
    configured_server._import_llm_cache.clear()

    with patch.object(configured_server.LlmFactory, "create", return_value=MagicMock()) as create:
        configured_server._import_llm("runtime-model", use_import_route=True)

    assert create.call_args.args[1]["api_key"] == "runtime-secret"
    assert create.call_args.args[1]["openai_base_url"] == "https://runtime.example/v1"


def test_import_llm_route_validation_requires_complete_nonsecret_indirection(configured_server, monkeypatch):
    assert configured_server._validate_import_llm_route("", "", "") is False

    with pytest.raises(RuntimeError, match="must be configured together"):
        configured_server._validate_import_llm_route("openai", "", "IMPORT_ROUTE_KEY")

    monkeypatch.delenv("IMPORT_ROUTE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="IMPORT_ROUTE_KEY") as error:
        configured_server._validate_import_llm_route(
            "openai",
            "https://import.example/v1",
            "IMPORT_ROUTE_KEY",
        )
    assert "runtime-secret" not in str(error.value)


def test_llm_configuration_test_uses_current_secret_when_form_leaves_key_blank(configured_server):
    client = TestClient(configured_server.app)
    llm = MagicMock()
    llm.generate_response.return_value = "YiQiao OK"
    configured_server.get_current_config = MagicMock(
        return_value={"llm": {"provider": "openai", "config": {"api_key": "stored-secret"}}}
    )

    with patch.object(configured_server.LlmFactory, "create", return_value=llm) as create:
        response = client.post(
            "/configure/test",
            json={
                "kind": "llm",
                "provider": "openai",
                "config": {"model": "llm-model", "openai_base_url": "https://llm.example/v1"},
            },
        )

    assert response.status_code == 200
    assert response.json()["preview"] == "YiQiao OK"
    assert create.call_args.args[1]["api_key"] == "stored-secret"


def test_llm_configuration_test_can_probe_memory_import_route_without_exposing_secret(
    configured_server,
    monkeypatch,
):
    client = TestClient(configured_server.app)
    llm = MagicMock()
    llm.generate_response.return_value = "YiQiao OK"
    llm.config.openai_base_url = "https://import.example/v1"
    llm.config.model = "gemini-fast"
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", True)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_PROVIDER", "openai")

    with patch.object(configured_server, "_import_llm", return_value=llm) as import_llm:
        response = client.post(
            "/configure/test",
            json={
                "kind": "llm",
                "provider": "openai",
                "config": {"model": "gemini-fast", "api_key": "request-secret"},
                "route": "memory_import",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["preview"] == "YiQiao OK"
    assert body["route"] == "memory_import"
    assert body["route_configured"] is True
    assert body["route_effective"] is True
    assert body["route_model"] == "gemini-fast"
    assert body["route_provider"] == "openai"
    assert len(body["route_base_url_sha256"]) == 64
    assert "request-secret" not in response.text
    assert "https://import.example" not in response.text
    import_llm.assert_called_once_with("gemini-fast", use_import_route=True)


def test_llm_configuration_test_rejects_memory_import_runtime_fallback(configured_server, monkeypatch):
    client = TestClient(configured_server.app)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", False)

    with patch.object(configured_server, "_import_llm") as import_llm:
        response = client.post(
            "/configure/test",
            json={
                "kind": "llm",
                "provider": "openai",
                "config": {"model": "gemini-fast"},
                "route": "memory_import",
            },
        )

    assert response.status_code == 400
    assert "explicit memory-import LLM route is not configured" in response.json()["detail"]
    import_llm.assert_not_called()


def test_llm_configuration_test_redacts_explicit_route_failure(configured_server, monkeypatch):
    client = TestClient(configured_server.app)
    route_secret = "server-only-import-secret"
    route_url = "https://private-import.example/v1"
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_ROUTE_ENABLED", True)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_PROVIDER", "openai")
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_BASE_URL", route_url)
    monkeypatch.setattr(configured_server, "MEMORY_IMPORT_LLM_API_KEY_ENV", "IMPORT_ROUTE_KEY")
    monkeypatch.setenv("IMPORT_ROUTE_KEY", route_secret)

    with patch.object(
        configured_server,
        "_import_llm",
        side_effect=RuntimeError(f"provider rejected {route_secret} at {route_url}"),
    ):
        response = client.post(
            "/configure/test",
            json={
                "kind": "llm",
                "provider": "openai",
                "config": {"model": "gemini-fast"},
                "route": "memory_import",
            },
        )

    assert response.status_code == 400
    assert route_secret not in response.text
    assert route_url not in response.text
    assert response.json()["detail"].count("[redacted]") == 2


def test_embedding_configuration_test_reports_dimensions(configured_server):
    client = TestClient(configured_server.app)
    embedder = MagicMock()
    embedder.embed.return_value = [0.0] * 768

    with patch.object(configured_server.EmbedderFactory, "create", return_value=embedder):
        response = client.post(
            "/configure/test",
            json={
                "kind": "embedder",
                "provider": "openai",
                "config": {"model": "embedding-model", "embedding_dims": 768},
            },
        )

    assert response.status_code == 200
    assert response.json()["dimensions"] == 768
    embedder.embed.assert_called_once_with("YiQiao embedding connection test", memory_action="search")


def test_embedding_configuration_test_rejects_dimension_mismatch(configured_server):
    client = TestClient(configured_server.app)
    embedder = MagicMock()
    embedder.embed.return_value = [0.0] * 512

    with patch.object(configured_server.EmbedderFactory, "create", return_value=embedder):
        response = client.post(
            "/configure/test",
            json={
                "kind": "embedder",
                "provider": "openai",
                "config": {"model": "embedding-model", "embedding_dims": 768},
            },
        )

    assert response.status_code == 400
    assert "configured 768, provider returned 512" in response.json()["detail"]


def test_reranker_configuration_test_returns_ranked_results(configured_server):
    client = TestClient(configured_server.app)
    reranker = MagicMock()
    reranker.rerank.return_value = [
        {"id": "job", "memory": "job", "rerank_score": 0.95},
        {"id": "other", "memory": "other", "rerank_score": 0.12},
    ]

    with patch.object(configured_server.RerankerFactory, "create", return_value=reranker):
        response = client.post(
            "/configure/test",
            json={
                "kind": "reranker",
                "provider": "llm_reranker",
                "config": {
                    "model": "rerank-model",
                    "llm": {
                        "provider": "openai",
                        "config": {"model": "rerank-model", "api_key": "rerank-secret"},
                    },
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["results"][0] == {"id": "job", "score": 0.95}


def test_provider_change_replaces_incompatible_provider_config():
    from server.server_state import merge_config

    merged = merge_config(
        {"provider": "anthropic", "config": {"api_key": "old", "anthropic_base_url": "https://old"}},
        {"provider": "openai", "config": {"model": "new-model"}},
    )

    assert merged == {"provider": "openai", "config": {"model": "new-model"}}
