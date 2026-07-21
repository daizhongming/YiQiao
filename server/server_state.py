# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import json
import logging
import threading
from copy import deepcopy
from typing import Any, Callable, Collection, Dict, Mapping

from yiqiao import Memory

_state_lock = threading.RLock()
_current_config: Dict[str, Any] = {}
_memory_instance: Memory | None = None
_waiting_for_provider_credentials = False
_credential_required_providers: frozenset[str] = frozenset()
_provider_credential_fallbacks: Dict[str, Any] = {}
_session_factory: Callable | None = None


class ProviderConfigurationRequiredError(RuntimeError):
    """Raised when memory operations are attempted before provider setup."""


class IncompleteProviderConfigurationError(ValueError):
    """Raised when a live runtime would be replaced by incomplete provider setup."""


def _has_api_key(config: Dict[str, Any], fallback: Dict[str, Any] | None = None) -> bool:
    if "api_key" in config:
        value = config["api_key"]
    else:
        value = (fallback or {}).get("api_key")
    return bool(value.strip()) if isinstance(value, str) else bool(value)


def _provider_credentials_missing(
    provider: Any,
    config: Any,
    credential_required_providers: Collection[str],
    fallback: Dict[str, Any] | None = None,
    credential_fallback: Any = None,
    allow_vertex_config: bool = True,
) -> bool:
    if provider not in credential_required_providers:
        return False
    provider_config = config if isinstance(config, dict) else {}
    if _has_api_key(provider_config, fallback):
        return False

    effective_config = dict(fallback or {})
    effective_config.update(provider_config)
    vertex_value = effective_config.get("vertexai")
    vertex_configured = vertex_value is not None
    vertex_enabled = str(vertex_value).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if provider == "gemini":
        if not allow_vertex_config and "vertexai" in provider_config:
            # The Gemini embedder does not consume the LLM-style ``vertexai``
            # flag.  Do not treat that flag as credentials, but still allow a
            # real Google API key fallback to configure the embedder.
            return not (isinstance(credential_fallback, str) and bool(credential_fallback.strip()))
        if vertex_enabled:
            return False
        if vertex_configured and isinstance(credential_fallback, bool):
            return True
    return not _has_api_key({"api_key": credential_fallback})


def _credential_fallback(
    credential_fallbacks: Mapping[str, Any] | None,
    scope: str,
    provider: Any,
) -> Any:
    if credential_fallbacks is None:
        return None
    scoped_key = f"{scope}:{provider}"
    if scoped_key in credential_fallbacks:
        return credential_fallbacks[scoped_key]
    return credential_fallbacks.get(provider)


def _missing_required_provider_credentials(
    config: Dict[str, Any],
    credential_required_providers: Collection[str],
    credential_fallbacks: Mapping[str, Any] | None = None,
) -> bool:
    for section_name in ("llm", "embedder"):
        section = config.get(section_name)
        if isinstance(section, dict) and _provider_credentials_missing(
            section.get("provider"),
            section.get("config"),
            credential_required_providers,
            allow_vertex_config=section_name == "llm",
            credential_fallback=_credential_fallback(credential_fallbacks, section_name, section.get("provider")),
        ):
            return True

    reranker = config.get("reranker")
    if not isinstance(reranker, dict) or reranker.get("provider") != "llm_reranker":
        return False
    reranker_config = reranker.get("config")
    if not isinstance(reranker_config, dict):
        reranker_config = {}
    nested_llm = reranker_config.get("llm")
    if isinstance(nested_llm, dict):
        nested_provider = nested_llm.get("provider") or reranker_config.get("provider") or "openai"
        return _provider_credentials_missing(
            nested_provider,
            nested_llm.get("config"),
            credential_required_providers,
            reranker_config,
            _credential_fallback(credential_fallbacks, "reranker", nested_provider),
        )
    return _provider_credentials_missing(
        reranker_config.get("provider") or "openai",
        reranker_config,
        credential_required_providers,
        credential_fallback=_credential_fallback(
            credential_fallbacks, "reranker", reranker_config.get("provider") or "openai"
        ),
    )


def set_session_factory(factory: Callable) -> None:
    global _session_factory
    _session_factory = factory


def _load_overrides() -> Dict[str, Any]:
    try:
        if _session_factory is None:
            return {}
        from models import Settings

        session = _session_factory()
        try:
            row = session.get(Settings, "config_overrides")
            if row is None:
                return {}
            return json.loads(row.value)
        finally:
            session.close()
    except Exception:
        return {}


def _save_overrides(overrides: Dict[str, Any]) -> None:
    try:
        if _session_factory is None:
            return
        from models import Settings
        from sqlalchemy.dialects.postgresql import insert

        session = _session_factory()
        try:
            serialized = json.dumps(overrides)
            stmt = (
                insert(Settings)
                .values(key="config_overrides", value=serialized)
                .on_conflict_do_update(
                    index_elements=[Settings.key],
                    set_={"value": serialized},
                )
            )
            session.execute(stmt)
            session.commit()
        finally:
            session.close()
    except Exception:
        logging.warning("Failed to persist config overrides to database", exc_info=True)


def _merge_config(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    if base.get("provider") and updates.get("provider") and base["provider"] != updates["provider"]:
        return deepcopy(updates)

    merged = deepcopy(base)

    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            current_provider = merged[key].get("provider")
            updated_provider = value.get("provider")
            if current_provider and updated_provider and current_provider != updated_provider:
                merged[key] = deepcopy(value)
            else:
                merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = deepcopy(value)

    return merged


def merge_config(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial runtime configuration without exposing mutable state."""
    return _merge_config(base, updates)


def initialize_state(
    default_config: Dict[str, Any],
    *,
    credential_required_providers: Collection[str] = (),
    provider_credential_fallbacks: Mapping[str, Any] | None = None,
) -> None:
    global _credential_required_providers, _current_config, _memory_instance
    global _provider_credential_fallbacks, _waiting_for_provider_credentials
    with _state_lock:
        next_required_providers = frozenset(credential_required_providers)
        next_credential_fallbacks = dict(provider_credential_fallbacks or {})
        next_config = deepcopy(default_config)
        overrides = _load_overrides()
        if overrides:
            next_config = _merge_config(next_config, overrides)
        if _missing_required_provider_credentials(
            next_config,
            next_required_providers,
            next_credential_fallbacks,
        ):
            _credential_required_providers = next_required_providers
            _provider_credential_fallbacks = next_credential_fallbacks
            _current_config = next_config
            _memory_instance = None
            _waiting_for_provider_credentials = True
            logging.warning(
                "Model provider credentials are not configured. YiQiao is starting in setup mode; "
                "complete provider setup in the dashboard before using memory operations."
            )
            return
        next_memory_instance = Memory.from_config(next_config)
        _credential_required_providers = next_required_providers
        _provider_credential_fallbacks = next_credential_fallbacks
        _current_config = next_config
        _memory_instance = next_memory_instance
        _waiting_for_provider_credentials = False


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    global _current_config, _memory_instance, _waiting_for_provider_credentials
    with _state_lock:
        next_config = _merge_config(_current_config, updates)
        if _missing_required_provider_credentials(
            next_config,
            _credential_required_providers,
            _provider_credential_fallbacks,
        ):
            if _memory_instance is not None:
                raise IncompleteProviderConfigurationError(
                    "Configuration update is incomplete; provide credentials for all configured model providers."
                )
            _current_config = next_config
            _memory_instance = None
            _waiting_for_provider_credentials = True
            overrides = _load_overrides()
            overrides = _merge_config(overrides, updates)
            _save_overrides(overrides)
            logging.warning(
                "Model provider credentials are not configured. YiQiao remains in setup mode; "
                "complete provider setup before using memory operations."
            )
            return deepcopy(_current_config)
        next_memory_instance = Memory.from_config(next_config)
        _current_config = next_config
        _memory_instance = next_memory_instance
        _waiting_for_provider_credentials = False
        overrides = _load_overrides()
        overrides = _merge_config(overrides, updates)
        _save_overrides(overrides)
        return deepcopy(_current_config)


def get_current_config() -> Dict[str, Any]:
    with _state_lock:
        return deepcopy(_current_config)


def get_memory_instance() -> Memory:
    with _state_lock:
        if _memory_instance is None:
            if _waiting_for_provider_credentials:
                raise ProviderConfigurationRequiredError(
                    "YiQiao model provider credentials have not been configured. "
                    "Complete provider setup before using memory operations."
                )
            raise RuntimeError("YiQiao runtime has not been initialized.")
        return _memory_instance
