from unittest.mock import MagicMock

import pytest

from server import server_state

HOSTED_PROVIDERS = {"openai", "anthropic", "gemini"}


def _hosted_config(api_key=None):
    return {
        "llm": {"provider": "openai", "config": {"api_key": api_key, "model": "gpt-test"}},
        "embedder": {
            "provider": "openai",
            "config": {"api_key": api_key, "model": "text-embedding-test"},
        },
    }


@pytest.fixture(autouse=True)
def isolated_server_state(monkeypatch):
    monkeypatch.setattr(server_state, "_credential_required_providers", frozenset())
    monkeypatch.setattr(server_state, "_provider_credential_fallbacks", {})
    monkeypatch.setattr(server_state, "_current_config", {})
    monkeypatch.setattr(server_state, "_memory_instance", None)
    monkeypatch.setattr(server_state, "_waiting_for_provider_credentials", False)
    monkeypatch.setattr(server_state, "_session_factory", None)
    monkeypatch.setattr(server_state, "_load_overrides", lambda: {})
    monkeypatch.setattr(server_state, "_save_overrides", lambda _overrides: None)


def test_initialize_state_eagerly_builds_configured_hosted_providers(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_uses_database_overrides_before_credential_check(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    monkeypatch.setattr(
        server_state,
        "_load_overrides",
        lambda: {
            "llm": {"config": {"api_key": "database-llm-key"}},
            "embedder": {"config": {"api_key": "database-embedder-key"}},
        },
    )

    server_state.initialize_state(_hosted_config(), credential_required_providers=HOSTED_PROVIDERS)

    effective_config = from_config.call_args.args[0]
    assert effective_config["llm"]["config"]["api_key"] == "database-llm-key"
    assert effective_config["embedder"]["config"]["api_key"] == "database-embedder-key"
    assert server_state.get_memory_instance() is memory


def test_initialize_state_keeps_keyless_custom_providers_eager(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = {
        "llm": {"provider": "ollama", "config": {"model": "local-llm"}},
        "embedder": {"provider": "huggingface", "config": {"model": "local-embedder"}},
    }

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_accepts_ambient_provider_credentials(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)

    server_state.initialize_state(
        _hosted_config(),
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"openai": "environment-key"},
    )

    from_config.assert_called_once()
    assert server_state.get_memory_instance() is memory


def test_initialize_state_scopes_openrouter_fallback_to_llm(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config()
    config["embedder"]["config"]["api_key"] = "embedding-key"

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"llm:openai": "openrouter-key"},
    )

    from_config.assert_called_once()
    assert server_state.get_memory_instance() is memory


def test_initialize_state_does_not_use_vertex_fallback_when_disabled(monkeypatch):
    from_config = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["llm"] = {"provider": "gemini", "config": {"vertexai": False}}

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"llm:gemini": True},
    )

    from_config.assert_not_called()
    with pytest.raises(server_state.ProviderConfigurationRequiredError):
        server_state.get_memory_instance()


def test_initialize_state_accepts_gemini_vertex_auth_without_api_key(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["llm"] = {
        "provider": "gemini",
        "config": {"vertexai": True, "project": "test-project", "location": "us-central1"},
    }

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_accepts_ambient_vertex_auth_when_vertex_is_null(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["llm"] = {"provider": "gemini", "config": {"vertexai": None}}

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"llm:gemini": True},
    )

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_accepts_gemini_embedder_vertex_auth_without_api_key(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["embedder"] = {
        "provider": "gemini",
        "config": {"model": "text-embedding-test"},
    }

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"embedder:gemini": True},
    )

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_does_not_eagerly_accept_vertex_flag_for_gemini_embedder(monkeypatch):
    from_config = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["embedder"] = {"provider": "gemini", "config": {"vertexai": True}}

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"embedder:gemini": True},
    )

    from_config.assert_not_called()
    with pytest.raises(server_state.ProviderConfigurationRequiredError):
        server_state.get_memory_instance()


def test_initialize_state_accepts_gemini_embedder_api_key_with_vertex_flag(monkeypatch):
    memory = MagicMock()
    from_config = MagicMock(return_value=memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["embedder"] = {"provider": "gemini", "config": {"vertexai": False}}

    server_state.initialize_state(
        config,
        credential_required_providers=HOSTED_PROVIDERS,
        provider_credential_fallbacks={"embedder:gemini": "google-api-key"},
    )

    from_config.assert_called_once_with(config)
    assert server_state.get_memory_instance() is memory


def test_initialize_state_defers_without_calling_memory_factory(monkeypatch):
    from_config = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config()

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_not_called()
    assert server_state.get_current_config() == config
    with pytest.raises(
        server_state.ProviderConfigurationRequiredError, match="provider credentials have not been configured"
    ):
        server_state.get_memory_instance()


def test_initialize_state_defers_for_unconfigured_nested_llm_reranker(monkeypatch):
    from_config = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["reranker"] = {
        "provider": "llm_reranker",
        "config": {
            "llm": {
                "provider": "gemini",
                "config": {"api_key": "", "model": "gemini-reranker"},
            }
        },
    }

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_not_called()
    with pytest.raises(
        server_state.ProviderConfigurationRequiredError, match="provider credentials have not been configured"
    ):
        server_state.get_memory_instance()


def test_initialize_state_defers_for_nested_llm_reranker_default_openai(monkeypatch):
    from_config = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    config = _hosted_config("configured-key")
    config["reranker"] = {"provider": "llm_reranker", "config": {"llm": {"config": {}}}}

    server_state.initialize_state(config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.assert_not_called()
    with pytest.raises(server_state.ProviderConfigurationRequiredError):
        server_state.get_memory_instance()


def test_initialize_state_keeps_unrelated_startup_failures_fatal(monkeypatch):
    monkeypatch.setattr(
        server_state.Memory,
        "from_config",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        server_state.initialize_state(
            _hosted_config("configured-key"),
            credential_required_providers=HOSTED_PROVIDERS,
        )


def test_failed_reinitialize_preserves_existing_runtime(monkeypatch):
    ready_memory = MagicMock()
    from_config = MagicMock(return_value=ready_memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    initial_config = _hosted_config("configured-key")
    server_state.initialize_state(initial_config, credential_required_providers=HOSTED_PROVIDERS)

    from_config.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        server_state.initialize_state(
            _hosted_config("replacement-key"),
            credential_required_providers=HOSTED_PROVIDERS,
        )

    assert server_state.get_current_config() == initial_config
    assert server_state.get_memory_instance() is ready_memory


def test_config_update_builds_runtime_after_deferred_bootstrap(monkeypatch):
    ready_memory = MagicMock()
    from_config = MagicMock(return_value=ready_memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)

    server_state.initialize_state(_hosted_config(), credential_required_providers=HOSTED_PROVIDERS)
    updated = server_state.update_config(
        {
            "llm": {"config": {"api_key": "setup-key"}},
            "embedder": {"config": {"api_key": "setup-key"}},
        }
    )

    assert updated["llm"]["config"]["api_key"] == "setup-key"
    assert updated["embedder"]["config"]["api_key"] == "setup-key"
    assert server_state.get_memory_instance() is ready_memory
    from_config.assert_called_once_with(updated)


def test_partial_config_update_persists_and_stays_deferred(monkeypatch):
    from_config = MagicMock()
    save_overrides = MagicMock()
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    monkeypatch.setattr(server_state, "_save_overrides", save_overrides)
    initial_config = _hosted_config()

    server_state.initialize_state(initial_config, credential_required_providers=HOSTED_PROVIDERS)
    updated = server_state.update_config({"llm": {"config": {"api_key": "setup-key"}}})

    from_config.assert_not_called()
    assert updated["llm"]["config"]["api_key"] == "setup-key"
    assert updated["embedder"]["config"]["api_key"] is None
    save_overrides.assert_called_once()
    with pytest.raises(server_state.ProviderConfigurationRequiredError):
        server_state.get_memory_instance()


def test_failed_config_update_preserves_deferred_bootstrap_state(monkeypatch):
    from_config = MagicMock(side_effect=ValueError("invalid provider config"))
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    initial_config = _hosted_config()

    server_state.initialize_state(initial_config, credential_required_providers=HOSTED_PROVIDERS)
    with pytest.raises(ValueError, match="invalid provider config"):
        server_state.update_config(
            {
                "llm": {"provider": "custom", "config": {}},
                "embedder": {"provider": "custom", "config": {}},
            }
        )

    assert server_state.get_current_config() == initial_config
    with pytest.raises(
        server_state.ProviderConfigurationRequiredError, match="provider credentials have not been configured"
    ):
        server_state.get_memory_instance()


def test_incomplete_update_preserves_ready_runtime(monkeypatch):
    ready_memory = MagicMock()
    from_config = MagicMock(return_value=ready_memory)
    monkeypatch.setattr(server_state.Memory, "from_config", from_config)
    initial_config = _hosted_config("configured-key")

    server_state.initialize_state(initial_config, credential_required_providers=HOSTED_PROVIDERS)
    with pytest.raises(server_state.IncompleteProviderConfigurationError):
        server_state.update_config({"embedder": {"config": {"api_key": ""}}})

    assert server_state.get_current_config() == initial_config
    assert server_state.get_memory_instance() is ready_memory
    from_config.assert_called_once_with(initial_config)
