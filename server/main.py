# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import oauth_service
import telemetry
from auth import (
    ADMIN_API_KEY,
    AUTH_DISABLED,
    JWT_SECRET,
    require_admin,
    require_project_read,
    require_project_write,
    verify_auth,
)
from chat_import import (
    ChunkExecution,
    ImportOptions,
    ImportRuntimeHooks,
    _import_error_diagnostics,
    _safe_import_error_message,
    canonical_entity_scope,
    entity_scope_hash,
    import_jobs,
    is_supported_input,
    run_import_job,
    safe_upload_path,
    scoped_conversation_hash,
)
from db import SessionLocal
from errors import (
    UpstreamError,
    install_request_id_logging,
    new_request_id,
    request_id_var,
    upstream_error,
    upstream_error_handler,
)
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from import_quota import (
    ImportStorageQuotaExceeded,
    ImportStorageQuotaGuard,
    capture_import_storage_quota_snapshot,
)
from import_repository import (
    ImportActiveJobLimitExceeded,
    ImportLeaseLost,
    ImportRepository,
    ImportWorkspaceBudgetExceeded,
)
from models import RequestLog, User
from neo4j_graph import delete_memories as delete_graph_memories
from neo4j_graph import delete_memory as delete_graph_memory
from neo4j_graph import is_configured as graph_is_configured
from neo4j_graph import related_memories as graph_related_memories
from neo4j_graph import upsert_memories_batch
from neo4j_graph import upsert_memory as upsert_graph_memory
from project_scope import DEFAULT_PROJECT_ID, get_project_id
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from pydantic import model_validator
from rate_limit import limiter
from routers import api_keys as api_keys_router
from routers import auth as auth_router
from routers import entities as entities_router
from routers import exports as exports_router
from routers import graph as graph_router
from routers import memories as memories_router
from routers import oauth as oauth_router
from routers import playground as playground_router
from routers import requests as requests_router
from routers import settings as settings_router
from routers import usage as usage_router
from routers import webhooks as webhooks_router
from schemas import MessageResponse
from server_state import (
    IncompleteProviderConfigurationError,
    ProviderConfigurationRequiredError,
    get_current_config,
    get_memory_instance,
    initialize_state,
    merge_config,
    set_session_factory,
    update_config,
)
from settings_store import get_json
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select
from usage_service import (
    applicable_policies,
    classify_operation,
    enforce_storage_quotas,
    request_scope_context,
)
from webhook_dispatcher import queue_webhook_event
from workspace import (
    DEFAULT_ORG_ID,
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    find_project,
    project_settings,
)

from mem0.exceptions import ValidationError as MemoryValidationError
from mem0.memory.main import MemoryOperationContext
from mem0.utils.factory import EmbedderFactory, LlmFactory, RerankerFactory

install_request_id_logging()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(request_id)s] %(message)s")

MIN_KEY_LENGTH = 16
SENSITIVE_CONFIG_KEYS = {
    "admin_api_key",
    "api_key",
    "authorization",
    "jwt_secret",
    "password",
    "password_hash",
    "secret",
    "token",
}
SKIPPED_REQUEST_LOG_PATHS = {"/api/health", "/docs", "/redoc", "/openapi.json"}
SKIPPED_REQUEST_LOG_PREFIXES = ("/requests", "/oauth", "/.well-known/")

BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")
BUNDLED_RERANKER_PROVIDERS = ("llm_reranker",)


def _warn_if_unconfigured() -> None:
    """Pre-auth deployments upgrading into this build will 401 everywhere until
    an admin key or admin user exists. Surface the fix before the support tickets."""
    try:
        with SessionLocal() as session:
            if session.scalar(select(func.count(User.id))) > 0:
                return
    except Exception:
        return

    logging.warning(
        "\n%s\n"
        "  Auth is enabled by default and this server has no admin configured.\n"
        "  Protected endpoints will return 401 until you either:\n"
        "    1. Set ADMIN_API_KEY=<long-random-value>  (fastest, no client changes)\n"
        "    2. Register an admin at http://<host>:3000/setup\n"
        "    3. Set AUTH_DISABLED=true                 (local development only)\n"
        "  Docs: /docs\n"
        "%s",
        "=" * 72,
        "=" * 72,
    )


if not AUTH_DISABLED and not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET is required. Set it in .env (generate with `openssl rand -base64 48`) "
        "or set AUTH_DISABLED=true for local development only."
    )

if AUTH_DISABLED:
    logging.warning("AUTH_DISABLED is enabled. Protected endpoints are open for local development only.")
elif ADMIN_API_KEY and len(ADMIN_API_KEY) < MIN_KEY_LENGTH:
    logging.warning(
        "ADMIN_API_KEY is shorter than %d characters - consider using a longer key for production.",
        MIN_KEY_LENGTH,
    )
elif not ADMIN_API_KEY:
    _warn_if_unconfigured()

telemetry.log_status()

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "postgres")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_COLLECTION_NAME = os.environ.get("POSTGRES_COLLECTION_NAME", "memories")

OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip() or None
ANTHROPIC_API_KEY = (os.environ.get("ANTHROPIC_API_KEY") or "").strip() or None
GOOGLE_API_KEY = (os.environ.get("GOOGLE_API_KEY") or "").strip() or None
OPENROUTER_API_KEY = (os.environ.get("OPENROUTER_API_KEY") or "").strip() or None
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "").strip()
HISTORY_DB_PATH = os.environ.get("HISTORY_DB_PATH", "/app/history/history.db")


def _env_value(name: str, *legacy_names: str, default: str = "") -> str:
    for candidate in (name, *legacy_names):
        if candidate in os.environ:
            return os.environ[candidate]
    return default


DEFAULT_LLM_PROVIDER = _env_value("YIQIAO_LLM_PROVIDER", "MEM0_LLM_PROVIDER", default="openai")
DEFAULT_LLM_MODEL = _env_value(
    "YIQIAO_LLM_MODEL",
    "MEM0_LLM_MODEL",
    "MEM0_DEFAULT_LLM_MODEL",
    default="gpt-4.1-nano-2025-04-14",
)
DEFAULT_LLM_MAX_TOKENS = 4096
DEFAULT_EMBEDDER_PROVIDER = _env_value("YIQIAO_EMBEDDER_PROVIDER", "MEM0_EMBEDDER_PROVIDER", default="openai")
DEFAULT_EMBEDDER_MODEL = _env_value(
    "YIQIAO_EMBEDDER_MODEL",
    "MEM0_EMBEDDER_MODEL",
    "MEM0_DEFAULT_EMBEDDER_MODEL",
    default="text-embedding-3-small",
)
DEFAULT_RERANK_PROVIDER = _env_value("YIQIAO_RERANK_PROVIDER", "MEM0_RERANK_PROVIDER").strip()
DEFAULT_RERANK_LLM_PROVIDER = _env_value(
    "YIQIAO_RERANK_LLM_PROVIDER",
    "MEM0_RERANK_LLM_PROVIDER",
    default=DEFAULT_LLM_PROVIDER,
).strip()
DEFAULT_RERANK_MODEL = _env_value("YIQIAO_RERANK_MODEL", "MEM0_RERANK_MODEL").strip()
DEFAULT_RERANK_BASE_URL = _env_value("YIQIAO_RERANK_BASE_URL", "MEM0_RERANK_BASE_URL").strip()
DEFAULT_RERANK_API_KEY = (os.environ.get("RERANK_API_KEY") or os.environ.get("MEM0_RERANK_API_KEY") or "").strip()


def _optional_int_env(name: str, *legacy_names: str) -> Optional[int]:
    raw = _env_value(name, *legacy_names).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logging.warning("%s must be an integer; ignoring value %r.", name, raw)
        return None


def _optional_float_env(name: str, *legacy_names: str) -> Optional[float]:
    raw = _env_value(name, *legacy_names).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logging.warning("%s must be a number; ignoring value %r.", name, raw)
        return None


def _optional_csv_env(name: str, *legacy_names: str) -> list[str]:
    return [item.strip() for item in _env_value(name, *legacy_names).split(",") if item.strip()]


def _boolean_env(name: str, default: bool, *legacy_names: str) -> bool:
    raw = _env_value(name, *legacy_names).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    logging.warning("%s must be a boolean; using default %r.", name, default)
    return default


def _configured_embedding_dims() -> Optional[int]:
    dims = _optional_int_env(
        "YIQIAO_EMBEDDER_DIMS",
        "MEM0_EMBEDDER_DIMS",
        "MEM0_EMBEDDING_DIMS",
    )
    if dims is not None and dims <= 0:
        logging.warning("YIQIAO_EMBEDDER_DIMS must be positive; ignoring value %r.", dims)
        return None
    return dims


DEFAULT_EMBEDDER_DIMS = _configured_embedding_dims()
DEFAULT_EMBEDDER_ALLOWED_OPENAI_PARAMS = _optional_csv_env(
    "YIQIAO_EMBEDDER_ALLOWED_OPENAI_PARAMS",
    "MEM0_EMBEDDER_ALLOWED_OPENAI_PARAMS",
)
DEFAULT_MEMORY_INFER = _boolean_env("YIQIAO_DEFAULT_INFER", True, "MEM0_DEFAULT_INFER")
MEMORY_IMPORT_WORKERS = max(1, min(_optional_int_env("MEMORY_IMPORT_WORKERS") or 3, 4))
MEMORY_IMPORT_MAX_WORKERS = max(
    MEMORY_IMPORT_WORKERS,
    min(_optional_int_env("MEMORY_IMPORT_MAX_WORKERS") or 4, 4),
)
MEMORY_IMPORT_TARGET_TOKENS = max(
    4000,
    min(_optional_int_env("MEMORY_IMPORT_TARGET_TOKENS") or 5000, 6000),
)
MEMORY_IMPORT_MAX_TOKENS = max(
    MEMORY_IMPORT_TARGET_TOKENS,
    min(_optional_int_env("MEMORY_IMPORT_MAX_TOKENS") or 6000, 6000),
)
_memory_import_overlap_turns = _optional_int_env("MEMORY_IMPORT_OVERLAP_TURNS")
MEMORY_IMPORT_OVERLAP_TURNS = max(
    0,
    min(1 if _memory_import_overlap_turns is None else _memory_import_overlap_turns, 2),
)
MEMORY_IMPORT_MODEL_TIERING = _boolean_env("MEMORY_IMPORT_MODEL_TIERING_ENABLED", True)
MEMORY_IMPORT_FAST_MODEL = os.environ.get("MEMORY_IMPORT_FAST_MODEL", "gemini-2.5-flash").strip()
MEMORY_IMPORT_FALLBACK_MODEL = os.environ.get("MEMORY_IMPORT_FALLBACK_MODEL", "gemini-2.5-pro").strip()
MEMORY_IMPORT_LLM_PROVIDER = os.environ.get("MEMORY_IMPORT_LLM_PROVIDER", "").strip()
MEMORY_IMPORT_LLM_BASE_URL = os.environ.get("MEMORY_IMPORT_LLM_BASE_URL", "").strip()
MEMORY_IMPORT_LLM_API_KEY_ENV = os.environ.get("MEMORY_IMPORT_LLM_API_KEY_ENV", "").strip()
MEMORY_IMPORT_AUDIT_RATIO = max(
    0.05,
    min(_optional_float_env("MEMORY_IMPORT_AUDIT_RATIO") or 0.07, 0.10),
)
MEMORY_IMPORT_MIN_CONFIDENCE = max(
    0.0,
    min(_optional_float_env("MEMORY_IMPORT_MIN_CONFIDENCE") or 0.65, 1.0),
)
MEMORY_IMPORT_LLM_MAX_OUTPUT_TOKENS = max(
    4096,
    _optional_int_env("MEMORY_IMPORT_LLM_MAX_OUTPUT_TOKENS") or 8192,
)
MEMORY_IMPORT_LLM_TIMEOUT_SECONDS = max(
    30.0,
    _optional_float_env("MEMORY_IMPORT_LLM_TIMEOUT_SECONDS") or 120.0,
)
MEMORY_IMPORT_LEASE_SECONDS = max(
    30.0,
    _optional_float_env("MEMORY_IMPORT_LEASE_SECONDS") or 120.0,
)
MEMORY_IMPORT_LEASE_RENEW_SECONDS = max(
    1.0,
    min(
        _optional_float_env("MEMORY_IMPORT_LEASE_RENEW_SECONDS") or MEMORY_IMPORT_LEASE_SECONDS / 3,
        MEMORY_IMPORT_LEASE_SECONDS / 2,
    ),
)
MEMORY_IMPORT_RECOVERY_SCAN_SECONDS = max(
    1.0,
    _optional_float_env("MEMORY_IMPORT_RECOVERY_SCAN_SECONDS") or 15.0,
)
MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT = max(
    1,
    _optional_int_env("MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT") or 2,
)
MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES = max(
    1,
    _optional_int_env("MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES") or 2 * 1024 * 1024 * 1024,
)
MEMORY_IMPORT_STORAGE_ROOT = Path(
    os.environ.get(
        "MEMORY_IMPORT_STORAGE_ROOT",
        str(Path(HISTORY_DB_PATH).parent / "memory-imports"),
    )
)


def _validate_import_llm_route(provider: str, base_url: str, api_key_env: str) -> bool:
    values = (provider, base_url, api_key_env)
    if not any(values):
        return False
    if not all(values):
        raise RuntimeError(
            "MEMORY_IMPORT_LLM_PROVIDER, MEMORY_IMPORT_LLM_BASE_URL, and "
            "MEMORY_IMPORT_LLM_API_KEY_ENV must be configured together."
        )
    if provider != "openai":
        raise RuntimeError("The explicit memory-import LLM route currently requires provider 'openai'.")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env):
        raise RuntimeError("MEMORY_IMPORT_LLM_API_KEY_ENV must name a valid environment variable.")
    if not os.environ.get(api_key_env, "").strip():
        raise RuntimeError(f"The environment variable named by MEMORY_IMPORT_LLM_API_KEY_ENV ({api_key_env}) is empty.")
    return True


MEMORY_IMPORT_LLM_ROUTE_ENABLED = _validate_import_llm_route(
    MEMORY_IMPORT_LLM_PROVIDER,
    MEMORY_IMPORT_LLM_BASE_URL,
    MEMORY_IMPORT_LLM_API_KEY_ENV,
)


def _provider_config(provider: str, model: str, api_key: Optional[str] = None) -> dict[str, Any]:
    if api_key is not None:
        key = api_key
    else:
        key = {
            "openai": OPENAI_API_KEY,
            "anthropic": ANTHROPIC_API_KEY,
            "gemini": GOOGLE_API_KEY,
        }.get(provider)
    return {"api_key": key, "model": model}


def _llm_config(
    provider: str,
    model: str,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    selected_api_key = api_key if api_key is not None else (LLM_API_KEY or None)
    config = _provider_config(provider, model, selected_api_key)
    if provider == "openai":
        selected_base_url = base_url or _env_value("YIQIAO_LLM_BASE_URL", "MEM0_LLM_BASE_URL")
        if selected_base_url:
            config["openai_base_url"] = selected_base_url
        max_tokens = _optional_int_env("YIQIAO_LLM_MAX_TOKENS", "MEM0_LLM_MAX_TOKENS")
        if max_tokens is not None and max_tokens <= 0:
            logging.warning("YIQIAO_LLM_MAX_TOKENS must be positive; using %d.", DEFAULT_LLM_MAX_TOKENS)
            max_tokens = None
        config["max_tokens"] = max_tokens if max_tokens is not None else DEFAULT_LLM_MAX_TOKENS
        config["request_timeout"] = (
            _optional_float_env("YIQIAO_LLM_TIMEOUT_SECONDS", "MEM0_LLM_TIMEOUT_SECONDS") or 30.0
        )
        max_retries = _optional_int_env("YIQIAO_LLM_MAX_RETRIES", "MEM0_LLM_MAX_RETRIES")
        config["max_retries"] = max_retries if max_retries is not None else 0
    return config


def _embedder_config(provider: str, model: str) -> dict[str, Any]:
    config = _provider_config(provider, model, EMBEDDING_API_KEY or None)
    if provider == "openai":
        base_url = _env_value("YIQIAO_EMBEDDER_BASE_URL", "MEM0_EMBEDDER_BASE_URL")
        if base_url:
            config["openai_base_url"] = base_url
        if DEFAULT_EMBEDDER_ALLOWED_OPENAI_PARAMS:
            config["allowed_openai_params"] = DEFAULT_EMBEDDER_ALLOWED_OPENAI_PARAMS
    if DEFAULT_EMBEDDER_DIMS is not None:
        config["embedding_dims"] = DEFAULT_EMBEDDER_DIMS
    return config


def _reranker_config() -> Optional[dict[str, Any]]:
    if not DEFAULT_RERANK_MODEL and not DEFAULT_RERANK_PROVIDER:
        return None

    provider = DEFAULT_RERANK_PROVIDER or "llm_reranker"
    top_k = _optional_int_env("YIQIAO_RERANK_TOP_K", "MEM0_RERANK_TOP_K")

    if provider == "llm_reranker":
        llm_provider = DEFAULT_RERANK_LLM_PROVIDER or DEFAULT_LLM_PROVIDER
        llm_config = _llm_config(
            llm_provider,
            DEFAULT_RERANK_MODEL or DEFAULT_LLM_MODEL,
            DEFAULT_RERANK_API_KEY,
            DEFAULT_RERANK_BASE_URL,
        )
        if DEFAULT_RERANK_API_KEY:
            llm_config["api_key"] = DEFAULT_RERANK_API_KEY
        if llm_provider == "openai" and DEFAULT_RERANK_BASE_URL:
            llm_config["openai_base_url"] = DEFAULT_RERANK_BASE_URL

        temperature = _optional_float_env("YIQIAO_RERANK_TEMPERATURE", "MEM0_RERANK_TEMPERATURE")
        max_tokens = _optional_int_env("YIQIAO_RERANK_MAX_TOKENS", "MEM0_RERANK_MAX_TOKENS")
        if temperature is None:
            temperature = 0.0
        if max_tokens is None:
            max_tokens = 20

        llm_config["temperature"] = temperature
        llm_config["max_tokens"] = max_tokens

        config: dict[str, Any] = {
            "model": DEFAULT_RERANK_MODEL or DEFAULT_LLM_MODEL,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "llm": {"provider": llm_provider, "config": llm_config},
        }
        if DEFAULT_RERANK_API_KEY:
            config["api_key"] = DEFAULT_RERANK_API_KEY
        if top_k is not None:
            config["top_k"] = top_k
        return {"provider": provider, "config": config}

    config = {}
    if DEFAULT_RERANK_MODEL:
        config["model"] = DEFAULT_RERANK_MODEL
    if DEFAULT_RERANK_API_KEY:
        config["api_key"] = DEFAULT_RERANK_API_KEY
    if top_k is not None:
        config["top_k"] = top_k
    return {"provider": provider, "config": config}


DEFAULT_CONFIG = {
    "version": "v1.1",
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "host": POSTGRES_HOST,
            "port": int(POSTGRES_PORT),
            "dbname": POSTGRES_DB,
            "user": POSTGRES_USER,
            "password": POSTGRES_PASSWORD,
            "collection_name": POSTGRES_COLLECTION_NAME,
            "embedding_model_dims": DEFAULT_EMBEDDER_DIMS or 1024,
        },
    },
    "llm": {
        "provider": DEFAULT_LLM_PROVIDER,
        "config": {**_llm_config(DEFAULT_LLM_PROVIDER, DEFAULT_LLM_MODEL), "temperature": 0.2},
    },
    "embedder": {
        "provider": DEFAULT_EMBEDDER_PROVIDER,
        "config": _embedder_config(DEFAULT_EMBEDDER_PROVIDER, DEFAULT_EMBEDDER_MODEL),
    },
    "history_db_path": HISTORY_DB_PATH,
}

reranker_config = _reranker_config()
if reranker_config is not None:
    DEFAULT_CONFIG["reranker"] = reranker_config


set_session_factory(SessionLocal)
import_repository = ImportRepository(SessionLocal)
import_jobs.configure_repository(import_repository)
initialize_state(
    DEFAULT_CONFIG,
    credential_required_providers=set(BUNDLED_LLM_PROVIDERS) | set(BUNDLED_EMBEDDER_PROVIDERS),
    provider_credential_fallbacks={
        "llm:openai": OPENAI_API_KEY or OPENROUTER_API_KEY,
        "embedder:openai": OPENAI_API_KEY,
        "llm:anthropic": ANTHROPIC_API_KEY,
        "llm:gemini": GOOGLE_API_KEY
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes", "on"},
        "embedder:gemini": GOOGLE_API_KEY
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes", "on"},
        "reranker:openai": OPENAI_API_KEY or OPENROUTER_API_KEY,
        "reranker:anthropic": ANTHROPIC_API_KEY,
        "reranker:gemini": GOOGLE_API_KEY
        or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() in {"1", "true", "yes", "on"},
    },
)

_import_threads: dict[str, threading.Thread] = {}
_import_pending_leases: dict[str, str] = {}
_import_threads_lock = threading.RLock()
_import_progress_lock = threading.RLock()
_import_llm_cache: dict[tuple[str, str, int], Any] = {}
_import_llm_cache_lock = threading.RLock()
_import_legacy_hashes: dict[str, dict[tuple[str, str], str]] = {}
_import_legacy_hashes_lock = threading.RLock()


app = FastAPI(
    title="YiQiao 记忆服务 API",
    description=(
        "面向 AI 助手与智能体的记忆管理和检索 API。\n\n"
        "## 身份认证\n"
        "支持 Bearer JWT、通过 `X-API-Key` 请求头传递的项目 API 密钥，"
        "以及管理员环境变量 `ADMIN_API_KEY`。`AUTH_DISABLED=true` 仅用于本地开发。\n\n"
        "---\n\n"
        "REST API for managing and searching memories for AI assistants and agents. "
        "Authentication supports Bearer JWT, project API keys via `X-API-Key`, "
        "and the `ADMIN_API_KEY` environment variable."
    ),
    version="0.2.0",
    redirect_slashes=False,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(UpstreamError, upstream_error_handler)


def _provider_configuration_required_handler(_request: Request, _exc: ProviderConfigurationRequiredError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Model provider credentials are not configured. Complete provider setup before using memory operations."
        },
    )


app.add_exception_handler(ProviderConfigurationRequiredError, _provider_configuration_required_handler)
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
DASHBOARD_URLS = [url.strip() for url in os.environ.get("DASHBOARD_URLS", DASHBOARD_URL).split(",") if url.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=DASHBOARD_URLS,
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(oauth_router.router)
app.include_router(api_keys_router.router)
app.include_router(entities_router.router)
app.include_router(exports_router.router)
app.include_router(graph_router.router)
app.include_router(memories_router.router)
app.include_router(playground_router.router)
app.include_router(requests_router.router)
app.include_router(settings_router.router)
app.include_router(settings_router.cloud_router)
app.include_router(usage_router.router)
app.include_router(webhooks_router.router)
app.include_router(webhooks_router.compat_router)


class Message(BaseModel):
    role: str = Field(..., description="Role of the message (user or assistant).")
    content: str = Field(..., description="Message content.")


class MemoryCreate(BaseModel):
    messages: List[Message] = Field(..., description="List of messages to store.")
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    app_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    categories: Optional[List[str]] = Field(
        None, description="Explicit category labels. Usually assigned automatically from project categories."
    )
    timestamp: Optional[Any] = Field(
        None,
        description="Unix timestamp or ISO timestamp for historical imports. Stored as metadata.created_at.",
    )
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format.")
    infer: Optional[bool] = Field(
        None,
        description="Whether to extract facts from messages. Defaults to YIQIAO_DEFAULT_INFER (true by default).",
    )
    memory_type: Optional[str] = Field(None, description="Type of memory to store (e.g. 'core').")
    prompt: Optional[str] = Field(None, description="Custom prompt to use for fact extraction.")


class MemoryImportEntity(BaseModel):
    type: Literal["user", "agent", "app", "run"]
    id: str = Field(min_length=1, max_length=255)

    @model_validator(mode="after")
    def normalize_id(self):
        self.id = self.id.strip()
        if not self.id:
            raise ValueError("Entity IDs cannot be empty.")
        return self


class MemoryImportUploadOptions(BaseModel):
    entities: list[MemoryImportEntity] = Field(min_length=1, max_length=4)
    source_app: str = Field(default="auto", min_length=1, max_length=64)
    infer: bool = True
    redact_secrets: bool = True
    skip_duplicates: bool = True
    # Legacy limits remain accepted; token limits below drive the current chunker.
    chunk_messages: int = Field(default=20, ge=1, le=100)
    chunk_chars: int = Field(default=12000, ge=1000, le=100000)
    chunk_target_tokens: int = Field(default=MEMORY_IMPORT_TARGET_TOKENS, ge=4000, le=6000)
    chunk_max_tokens: int = Field(default=MEMORY_IMPORT_MAX_TOKENS, ge=4000, le=6000)
    chunk_overlap_turns: int = Field(default=MEMORY_IMPORT_OVERLAP_TURNS, ge=0, le=2)
    workers: int = Field(default=MEMORY_IMPORT_WORKERS, ge=1, le=4)
    model_tiering_enabled: bool = MEMORY_IMPORT_MODEL_TIERING
    fast_model: str = Field(default=MEMORY_IMPORT_FAST_MODEL, min_length=1, max_length=255)
    fallback_model: str = Field(default=MEMORY_IMPORT_FALLBACK_MODEL, min_length=1, max_length=255)
    audit_ratio: float = Field(default=MEMORY_IMPORT_AUDIT_RATIO, ge=0.05, le=0.10)

    @model_validator(mode="after")
    def normalize_options(self):
        self.source_app = self.source_app.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]+", self.source_app):
            raise ValueError("source_app may contain only letters, numbers, dots, underscores, and hyphens.")
        if not self.infer:
            raise ValueError("Chat memory imports require infer=true.")
        entity_types = [entity.type for entity in self.entities]
        if len(entity_types) != len(set(entity_types)):
            raise ValueError("Each entity type can be configured only once.")
        if self.chunk_target_tokens > self.chunk_max_tokens:
            raise ValueError("chunk_target_tokens cannot exceed chunk_max_tokens.")
        self.fast_model = self.fast_model.strip()
        self.fallback_model = self.fallback_model.strip()
        if not self.fast_model or not self.fallback_model:
            raise ValueError("Import model names cannot be empty.")
        if (
            MEMORY_IMPORT_LLM_ROUTE_ENABLED
            and self.model_tiering_enabled
            and (self.fast_model != MEMORY_IMPORT_FAST_MODEL or self.fallback_model != MEMORY_IMPORT_FALLBACK_MODEL)
        ):
            raise ValueError("Tiered imports must use the server-configured fast and fallback models.")
        return self

    def to_import_options(self) -> ImportOptions:
        return ImportOptions(
            entities={f"{entity.type}_id": entity.id for entity in self.entities},
            source_app=self.source_app,
            infer=self.infer,
            redact_secrets=self.redact_secrets,
            skip_duplicates=self.skip_duplicates,
            chunk_messages=self.chunk_messages,
            chunk_chars=self.chunk_chars,
            chunk_target_tokens=self.chunk_target_tokens,
            chunk_max_tokens=self.chunk_max_tokens,
            chunk_overlap_turns=self.chunk_overlap_turns,
            workers=min(self.workers, MEMORY_IMPORT_MAX_WORKERS),
            max_workers=MEMORY_IMPORT_MAX_WORKERS,
            model_tiering_enabled=self.model_tiering_enabled,
            fast_model=self.fast_model,
            fallback_model=self.fallback_model,
            audit_ratio=self.audit_ratio,
            min_confidence=MEMORY_IMPORT_MIN_CONFIDENCE,
        )


class MemoryUpdate(BaseModel):
    text: Optional[str] = Field(None, description="New content to update the memory with.")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata to update.")
    categories: Optional[List[str]] = Field(None, description="Category labels to update.")
    expiration_date: Optional[str] = Field(None, description="Expiration date in YYYY-MM-DD format, or null to clear.")


class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query.")
    user_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    run_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    agent_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    app_id: Optional[str] = Field(None, description="Deprecated: pass inside `filters` instead.", deprecated=True)
    filters: Optional[Dict[str, Any]] = None
    top_k: Optional[int] = Field(None, description="Maximum number of results to return.")
    threshold: Optional[float] = Field(None, description="Minimum similarity score for results.")
    explain: Optional[bool] = Field(None, description="Include score details for each search result.")
    rerank: Optional[bool] = Field(None, description="Whether to rerank results.")
    show_expired: Optional[bool] = Field(None, description="Include expired memories.")


class GenerateInstructionsRequest(BaseModel):
    use_case: str = Field(..., description="Description of what the user will use YiQiao for.")
    memory_depth: Optional[str] = None
    include: Optional[str] = None
    exclude: Optional[str] = None
    multilingual: Optional[bool] = None


class GenerateCategoriesRequest(GenerateInstructionsRequest):
    custom_instructions: Optional[str] = None


SETTINGS_GENERATION_MAX_TOKENS = 8192
SETTINGS_GENERATION_TIMEOUT_SECONDS = 60.0


class ModelConfigurationTestRequest(BaseModel):
    kind: Literal["llm", "embedder", "reranker"]
    provider: str
    config: Dict[str, Any] = Field(default_factory=dict)
    route: Literal["runtime", "memory_import"] = "runtime"


def _client_error(exc: Exception) -> HTTPException:
    """Map core validation / not-found errors to 4xx so clients can tell a bad
    request from an upstream outage. 'not found' is a 404, everything else a 400."""
    detail = str(exc)
    status_code = 404 if isinstance(exc, ValueError) and "not found" in detail.lower() else 400
    return HTTPException(status_code=status_code, detail=detail)


def _redact_config(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: _redact_config(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item_value, key) for item_value in value]
    if key is not None and key.lower() in SENSITIVE_CONFIG_KEYS:
        return "[redacted]" if value else value
    return value


def _validate_bundled_providers(config: Dict[str, Any]) -> None:
    llm = config.get("llm")
    if isinstance(llm, dict) and (provider := llm.get("provider")) and provider not in BUNDLED_LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"LLM provider '{provider}' is not bundled in this image. "
                f"Bundled providers: {', '.join(BUNDLED_LLM_PROVIDERS)}. "
                "To use another provider, install its Python package, rebuild the container, "
                "and extend BUNDLED_LLM_PROVIDERS in server/main.py."
            ),
        )

    embedder = config.get("embedder")
    if (
        isinstance(embedder, dict)
        and (provider := embedder.get("provider"))
        and provider not in BUNDLED_EMBEDDER_PROVIDERS
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Embedder provider '{provider}' is not bundled in this image. "
                f"Bundled providers: {', '.join(BUNDLED_EMBEDDER_PROVIDERS)}. "
                "To use another provider, install its Python package, rebuild the container, "
                "and extend BUNDLED_EMBEDDER_PROVIDERS in server/main.py."
            ),
        )

    reranker = config.get("reranker")
    if isinstance(reranker, dict):
        provider = reranker.get("provider")
        if provider and provider not in BUNDLED_RERANKER_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Reranker provider '{provider}' is not bundled in this image. "
                    f"Bundled providers: {', '.join(BUNDLED_RERANKER_PROVIDERS)}."
                ),
            )
        reranker_config = reranker.get("config") or {}
        reranker_llm = reranker_config.get("llm") if isinstance(reranker_config, dict) else None
        if isinstance(reranker_llm, dict):
            llm_provider = reranker_llm.get("provider")
            if llm_provider and llm_provider not in BUNDLED_LLM_PROVIDERS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Reranker LLM provider '{llm_provider}' is not bundled in this image. "
                        f"Bundled providers: {', '.join(BUNDLED_LLM_PROVIDERS)}."
                    ),
                )


def _redact_error_detail(exc: Exception, config: Dict[str, Any]) -> str:
    detail = str(exc) or exc.__class__.__name__

    def redact(value: Any, key: str | None = None) -> None:
        nonlocal detail
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                redact(item_value, item_key)
        elif isinstance(value, list):
            for item in value:
                redact(item, key)
        elif key and key.lower() in SENSITIVE_CONFIG_KEYS and value:
            detail = detail.replace(str(value), "[redacted]")

    redact(config)
    return detail[:1000]


def _model_test_section(req: ModelConfigurationTestRequest) -> Dict[str, Any]:
    incoming = {"provider": req.provider, "config": req.config}
    current = get_current_config().get(req.kind)
    if isinstance(current, dict):
        return merge_config(current, incoming)
    return incoming


def _workspace_settings() -> dict[str, Any]:
    with SessionLocal() as session:
        return get_json(session, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)


def _count_memories_for_project(project_id: str) -> int:
    result = get_memory_instance().vector_store.list(filters={"project_id": project_id}, top_k=1_000_000)
    rows = result[0] if result and isinstance(result, list) and isinstance(result[0], list) else result or []
    return len(rows)


def _organization_project_ids(organization_id: str) -> list[str]:
    return [
        str(project.get("id"))
        for project in _workspace_settings().get("projects", [])
        if project.get("id") and project.get("organization_id") == organization_id
    ]


def _capture_import_storage_quota_snapshot(request: Request, user: User | None) -> dict[str, Any]:
    with SessionLocal() as session:
        return capture_import_storage_quota_snapshot(request, user, session)


def _enforce_memory_storage_quota(
    request: Request,
    user: User | None,
    *,
    enforce_hard: bool = True,
) -> None:
    if getattr(request.state, "auth_type", "none") in {"admin_api_key", "disabled"}:
        return
    with SessionLocal() as session:
        context = request_scope_context(request, user, session)
        policies = applicable_policies(session, context, {"stored_memories"})
        if not enforce_hard:
            policies = [policy for policy in policies if policy.mode != "hard"]
        if not policies:
            return
        project_counts: dict[str, int] = {}
        for policy in policies:
            if policy.scope_type == "project":
                if policy.scope_id not in project_counts:
                    project_counts[policy.scope_id] = _count_memories_for_project(policy.scope_id)
            elif policy.scope_type == "organization":
                org_project_ids = [
                    str(project.get("id"))
                    for project in context["workspace"].get("projects", [])
                    if project.get("id") and project.get("organization_id") == policy.scope_id
                ]
                project_counts[policy.scope_id] = sum(_count_memories_for_project(item) for item in org_project_ids)
        counts = {(policy.scope_type, policy.scope_id): project_counts.get(policy.scope_id, 0) for policy in policies}
        enforce_storage_quotas(request, user, session, counts, enforce_hard=enforce_hard)


def _build_extraction_prompt(settings: dict[str, Any]) -> str | None:
    extraction = settings.get("extraction") or {}
    categories = settings.get("categories") or []
    lines: list[str] = []
    if extraction.get("custom_instructions"):
        lines.append(str(extraction["custom_instructions"]).strip())
    if extraction.get("use_case"):
        lines.append(f"Use case: {extraction['use_case']}.")
    depth = extraction.get("memory_depth") or "Essential Insights"
    depth_rules = {
        "Essential Insights": "Extract only stable facts, preferences, goals, constraints, and durable context.",
        "Balanced Context": "Extract stable facts plus useful recurring context and project-specific details.",
        "Comprehensive Knowledge": "Extract rich durable context, preferences, goals, constraints, and notable project details.",
    }
    lines.append(depth_rules.get(depth, depth_rules["Essential Insights"]))
    if extraction.get("multilingual"):
        lines.append("Store each memory in the same language and script as the input message.")
    if extraction.get("include"):
        lines.append(f"Prioritize these elements: {extraction['include']}")
    if extraction.get("exclude"):
        lines.append(f"Do not store these elements: {extraction['exclude']}")
    if categories:
        category_text = "; ".join(
            f"{item.get('name')}: {item.get('description')}".strip(": ")
            for item in categories
            if isinstance(item, dict) and item.get("name")
        )
        if category_text:
            lines.append(f"When appropriate, categorize memories using these categories: {category_text}.")
            lines.append(
                "For each extracted memory, include a categories array containing only matching category names from the list above. "
                "Use [] when no category is appropriate."
            )
    return "\n".join(line for line in lines if line).strip() or None


def _category_match_key(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").replace("-", " ").lower().split())


def _category_slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:64] or fallback


def _normalize_category_definitions(value: Any) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else [value] if value else []
    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("key") or item.get("label") or "").strip()
            if not name and len(item) == 1:
                only_key, only_value = next(iter(item.items()))
                name = str(only_key or "").strip()
                description = str(only_value or "").strip()
            else:
                description = str(item.get("description") or "").strip()
        else:
            text = str(item or "").strip()
            name, _, description = text.partition(":")
            name = name.strip()
            description = description.strip()
        if not name:
            name = f"category_{index + 1}"
        name = _category_slug(name, f"category_{index + 1}")
        key = _category_match_key(name)
        if key in seen:
            continue
        seen.add(key)
        categories.append({"name": name, "description": description})
    return categories


def _parse_generated_instructions(response: Any) -> tuple[str, str]:
    text = str(response or "").strip()
    match = re.search(r"INSTRUCTIONS:\s*(.*?)\s*TEST_MESSAGE:\s*(.+)", text, flags=re.I | re.S)
    if not match:
        raise HTTPException(
            status_code=502,
            detail="The LLM returned an incomplete instruction response. Please retry.",
        )
    instructions = match.group(1).strip()
    test_message = match.group(2).strip()
    if not instructions or not test_message:
        raise HTTPException(
            status_code=502,
            detail="The LLM returned an incomplete instruction response. Please retry.",
        )
    return instructions, test_message


def _parse_generated_categories(response: Any) -> list[dict[str, str]]:
    if isinstance(response, (dict, list)):
        parsed = response
    else:
        text = str(response or "").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                raise HTTPException(
                    status_code=502,
                    detail="The LLM returned incomplete category JSON. Please retry.",
                )
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=502,
                    detail="The LLM returned incomplete category JSON. Please retry.",
                ) from exc
    categories = _normalize_category_definitions(parsed.get("categories") if isinstance(parsed, dict) else parsed)
    if not categories:
        raise HTTPException(
            status_code=502,
            detail="The LLM did not return any usable categories. Please retry.",
        )
    return categories


def _category_names(settings: dict[str, Any]) -> list[str]:
    return [
        str(item.get("name")).strip()
        for item in settings.get("categories") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]


def _normalize_memory_categories(value: Any, allowed: list[str] | None = None) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    raw_items = value if isinstance(value, list) else [value]
    allowed_map = None
    if allowed:
        allowed_map = {_category_match_key(item): item for item in allowed if item}
    categories: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        if allowed_map is not None:
            text = allowed_map.get(_category_match_key(text), "")
        if not text or text in seen:
            continue
        seen.add(text)
        categories.append(text)
    return categories


def _request_categories(memory_categories: Any, metadata: dict[str, Any], allowed: list[str]) -> list[str]:
    return _normalize_memory_categories(
        memory_categories or metadata.get("categories") or metadata.get("category"),
        allowed or None,
    )


def _memory_metadata_for_graph(memory: dict[str, Any] | None, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = dict(fallback or {})
    metadata.pop("_allowed_categories", None)
    if isinstance(memory, dict):
        metadata.update(memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {})
        metadata.pop("_allowed_categories", None)
        categories = _normalize_memory_categories(
            memory.get("categories") or metadata.get("categories") or metadata.get("category")
        )
        if categories:
            metadata["categories"] = categories
        if memory.get("project_id"):
            metadata["project_id"] = memory.get("project_id")
    return metadata


def _coerce_memory_timestamp(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return _coerce_memory_timestamp(float(text))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return None


def _add_graph_results(
    response: dict[str, Any],
    query: str,
    project_id: str,
    filters: dict[str, Any],
    top_k: int,
    include_details: bool = False,
) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        return response
    related = graph_related_memories(query, project_id, filters, limit=max(top_k, 10))
    if not related:
        return response

    results = list(response["results"])
    by_id = {str(item.get("id")): item for item in results if isinstance(item, dict) and item.get("id")}
    for item in related:
        memory_id = str(item.get("id") or "")
        if not memory_id:
            continue
        boost = float(item.get("boost") or 0.0)
        if memory_id in by_id:
            result = by_id[memory_id]
            result["score"] = min(1.0, float(result.get("score") or 0.0) + boost)
            if include_details:
                details = result.setdefault("score_details", {})
                if isinstance(details, dict):
                    details["neo4j_entity_boost"] = boost
                    details["matched_entities"] = item.get("matched_entities", [])
            continue
        try:
            memory = get_memory_instance().get(memory_id)
        except Exception:
            continue
        if not memory:
            continue
        if _memory_project_id(memory) != project_id:
            continue
        appended = {
            "id": memory_id,
            "memory": memory.get("memory") or memory.get("data") or item.get("text"),
            "score": min(1.0, boost),
            "metadata": memory.get("metadata") or {},
        }
        if include_details:
            appended["score_details"] = {
                "neo4j_entity_boost": boost,
                "matched_entities": item.get("matched_entities", []),
            }
        results.append(appended)

    response["results"] = sorted(results, key=lambda item: float(item.get("score") or 0.0), reverse=True)[:top_k]
    return response


def _should_log_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return False
    if getattr(request.state, "suppress_request_log", False):
        return False
    path = request.url.path
    if path in SKIPPED_REQUEST_LOG_PATHS:
        return False
    return not path.startswith(SKIPPED_REQUEST_LOG_PREFIXES)


REQUEST_ENTITY_FIELDS = ("user_id", "agent_id", "app_id", "run_id")


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(json.dumps(value, default=str))


def _request_log_entities(payload: Any) -> dict[str, str]:
    entities: dict[str, str] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in REQUEST_ENTITY_FIELDS and item is not None and key not in entities:
                    entities[key] = str(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return entities


def _request_log_result_count(response: Any) -> int | None:
    if isinstance(response, dict):
        results = response.get("results")
        if isinstance(results, list):
            return len(results)
        if isinstance(response.get("total"), int):
            return int(response["total"])
    if isinstance(response, list):
        return len(response)
    return None


def _set_request_log_context(
    request: Request,
    event_type: str,
    payload: Any = None,
    response: Any = None,
) -> None:
    request.state.request_log_event_type = event_type.upper()
    if payload is not None:
        safe_payload = _json_safe(payload)
        request.state.request_log_payload = safe_payload
        request.state.request_log_entities = _request_log_entities(safe_payload)
    if response is not None:
        safe_response = _json_safe(response)
        request.state.request_log_response = safe_response
        request.state.request_log_result_count = _request_log_result_count(safe_response)


def _persist_request_log(
    method: str,
    path: str,
    status_code: int,
    latency_ms: float,
    auth_type: str,
    project_id: str,
    organization_id: str | None,
    api_key_id: str | None,
    actor_user_id: str | None,
    operation: str,
    event_type: str,
    entities: dict[str, str],
    request_payload: Any,
    response_payload: Any,
    result_count: int | None,
) -> None:
    session = SessionLocal()

    try:
        session.add(
            RequestLog(
                method=method,
                path=path,
                status_code=status_code,
                latency_ms=latency_ms,
                auth_type=auth_type,
                project_id=project_id,
                organization_id=organization_id,
                api_key_id=uuid.UUID(api_key_id) if api_key_id else None,
                actor_user_id=uuid.UUID(actor_user_id) if actor_user_id else None,
                operation=operation,
                event_type=event_type,
                user_id=entities.get("user_id"),
                agent_id=entities.get("agent_id"),
                app_id=entities.get("app_id"),
                run_id=entities.get("run_id"),
                request_payload=request_payload,
                response_payload=response_payload,
                result_count=result_count,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        logging.exception("Failed to persist request log")
    finally:
        session.close()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    oauth_protocol_request = request.url.path.startswith("/oauth") or request.url.path.startswith("/.well-known/")
    if oauth_protocol_request:
        request.state.suppress_request_log = True
    request.state.auth_type = getattr(request.state, "auth_type", "none")
    request.state.api_key_id = None
    request.state.actor_user_id = None
    request.state.actor_email = None
    request.state.organization_id = None
    request.state.request_log_payload = dict(request.query_params) or None
    request.state.request_log_entities = _request_log_entities(request.state.request_log_payload)
    rid = new_request_id()
    token = request_id_var.set(rid)
    start = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = rid
        oauth_authenticated = getattr(request.state, "auth_type", "none") == "oauth"
        if oauth_protocol_request or oauth_authenticated:
            response.headers["Cache-Control"] = "no-store, no-cache"
            response.headers["Pragma"] = "no-cache"
        if 200 <= status_code < 300 and oauth_authenticated:
            grant_id = getattr(request.state, "oauth_grant_id", None)
            access_token_hash = getattr(request.state, "oauth_access_token_hash", None)
            context = getattr(request.state, "oauth_audit_context", None)
            if grant_id and access_token_hash and context:
                try:
                    with SessionLocal() as oauth_db:
                        oauth_service.record_resource_success(
                            oauth_db,
                            grant_id=uuid.UUID(grant_id),
                            access_token_hash=access_token_hash,
                            context=context,
                        )
                except Exception:
                    logging.exception("Failed to persist OAuth resource-use timestamps")
        warnings = getattr(request.state, "quota_warnings", [])
        if warnings:
            response.headers["X-Quota-Warning"] = ", ".join(warnings)
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        request_id_var.reset(token)
        if _should_log_request(request):
            operation = classify_operation(request.method, request.url.path)
            event_type = getattr(request.state, "request_log_event_type", None) or {
                "memory_write": "ADD",
                "memory_search": "SEARCH",
                "memory_read": "GET_ALL",
            }.get(operation, request.method.upper())
            asyncio.get_running_loop().run_in_executor(
                None,
                _persist_request_log,
                request.method,
                request.url.path,
                status_code,
                round((time.perf_counter() - start) * 1000, 2),
                getattr(request.state, "auth_type", "none"),
                get_project_id(request),
                getattr(request.state, "organization_id", None),
                getattr(request.state, "api_key_id", None),
                getattr(request.state, "actor_user_id", None),
                operation,
                event_type,
                getattr(request.state, "request_log_entities", {}),
                getattr(request.state, "request_log_payload", None),
                getattr(request.state, "request_log_response", None),
                getattr(request.state, "request_log_result_count", None),
            )


@app.get("/api/health", include_in_schema=False)
def health_check():
    return {"status": "ok"}


@app.get("/configure", summary="Get current YiQiao configuration")
def get_config(_auth=Depends(verify_auth)):
    return _redact_config(get_current_config())


@app.get("/configure/providers", summary="List bundled model providers")
def list_bundled_providers(_auth=Depends(verify_auth)):
    return {
        "llm": list(BUNDLED_LLM_PROVIDERS),
        "embedder": list(BUNDLED_EMBEDDER_PROVIDERS),
        "reranker": list(BUNDLED_RERANKER_PROVIDERS),
    }


@app.post("/configure/test", summary="Test a model configuration")
def test_model_configuration(req: ModelConfigurationTestRequest, _auth=Depends(require_admin)):
    section = _model_test_section(req)
    _validate_bundled_providers({req.kind: section})
    started = time.perf_counter()

    try:
        provider = str(section.get("provider") or "")
        config = dict(section.get("config") or {})
        details: Dict[str, Any]

        if req.kind == "llm":
            model = str(config.get("model") or "").strip()
            if req.route == "memory_import":
                if not model:
                    raise ValueError("A model is required for the memory-import route test.")
                if not MEMORY_IMPORT_LLM_ROUTE_ENABLED:
                    raise ValueError("The explicit memory-import LLM route is not configured.")
                llm = _import_llm(model, use_import_route=True)
                route_provider = MEMORY_IMPORT_LLM_PROVIDER
                route_base_url = str(getattr(llm.config, "openai_base_url", "") or "").rstrip("/")
            else:
                llm = LlmFactory.create(provider, config)
                route_provider = provider
                route_base_url = str(config.get("openai_base_url") or "").rstrip("/")
            configured_model = getattr(getattr(llm, "config", None), "model", None)
            route_model = configured_model.strip() if isinstance(configured_model, str) else model
            response = llm.generate_response(
                [{"role": "user", "content": "Reply with exactly: YiQiao OK"}],
            )
            details = {
                "preview": str(response).strip()[:200],
                "route": req.route,
                "route_configured": req.route == "runtime" or MEMORY_IMPORT_LLM_ROUTE_ENABLED,
                "route_effective": True,
                "route_model": route_model,
                "route_provider": route_provider,
                "route_base_url_sha256": hashlib.sha256(route_base_url.encode("utf-8")).hexdigest()
                if route_base_url
                else None,
            }
        elif req.kind == "embedder":
            embedder = EmbedderFactory.create(provider, config, None)
            embedding = embedder.embed("YiQiao embedding connection test", memory_action="search")
            dimensions = len(embedding)
            expected_dims = config.get("embedding_dims")
            if expected_dims is not None and dimensions != int(expected_dims):
                raise ValueError(
                    f"Embedding dimension mismatch: configured {expected_dims}, provider returned {dimensions}."
                )
            details = {"dimensions": dimensions}
        else:
            reranker = RerankerFactory.create(provider, config)
            reranked = reranker.rerank(
                "哪条记忆与 Java 后端求职最相关？",
                [
                    {"id": "job", "memory": "用户正在准备 Java 后端工程师岗位的简历。"},
                    {"id": "other", "memory": "用户周末喜欢看电影。"},
                ],
                top_k=2,
            )
            details = {
                "results": [
                    {
                        "id": item.get("id"),
                        "score": item.get("rerank_score"),
                    }
                    for item in reranked
                ]
            }
    except HTTPException:
        raise
    except Exception as exc:
        detail = _redact_error_detail(exc, section)
        if req.kind == "llm" and req.route == "memory_import":
            route_values = (
                os.environ.get(MEMORY_IMPORT_LLM_API_KEY_ENV, "").strip(),
                MEMORY_IMPORT_LLM_BASE_URL.rstrip("/"),
            )
            for value in filter(None, route_values):
                detail = detail.replace(value, "[redacted]")
        raise HTTPException(
            status_code=400,
            detail=f"{req.kind} test failed: {detail}",
        )

    return {
        "status": "ok",
        "kind": req.kind,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        **details,
    }


@app.post("/configure", summary="Configure YiQiao")
def set_config(config: Dict[str, Any], _auth=Depends(require_admin)):
    """Set memory configuration. Requires admin role."""
    _validate_bundled_providers(config)
    try:
        update_config(config)
    except IncompleteProviderConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Configuration set successfully"}


@app.post("/generate-instructions", summary="Generate custom instructions from a use case")
def generate_instructions(req: GenerateInstructionsRequest, _auth=Depends(require_project_write)):
    """Generate custom instructions and a contextual test message tailored to a use case."""
    try:
        llm = get_memory_instance().llm
        prompt = (
            "You are configuring a memory system. Given the use case below, produce two things:\n"
            "1. INSTRUCTIONS: A short paragraph of custom instructions telling the memory extraction system "
            "what kinds of facts, preferences, and context to prioritize. Be specific to the use case.\n"
            "2. TEST_MESSAGE: A single realistic sentence a user in this use case would say, suitable for "
            "testing that the memory system works.\n\n"
            "Respond in exactly this format (no markdown, no extra text):\n"
            "INSTRUCTIONS: <your instructions>\n"
            f"TEST_MESSAGE: <your test message>\n\n"
            f"Use case: {req.use_case}\n"
            f"Memory depth: {req.memory_depth or 'Essential Insights'}\n"
            f"Multilingual: {bool(req.multilingual)}\n"
            f"Include: {req.include or ''}\n"
            f"Exclude: {req.exclude or ''}"
        )
        response = llm.generate_response(
            [{"role": "user", "content": prompt}],
            max_tokens=SETTINGS_GENERATION_MAX_TOKENS,
            temperature=0,
            timeout=SETTINGS_GENERATION_TIMEOUT_SECONDS,
        )
        instructions, test_message = _parse_generated_instructions(response)
        return {"custom_instructions": instructions, "test_message": test_message}
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


@app.post("/generate-categories", summary="Generate project categories from extraction settings")
def generate_categories(req: GenerateCategoriesRequest, _auth=Depends(require_project_write)):
    """Generate editable project-level category suggestions from extraction settings."""
    try:
        llm = get_memory_instance().llm
        prompt = (
            "You are configuring project-level memory categories for a memory system. "
            "Return 8 to 14 durable categories that organize future extracted memories for this project.\n"
            "Each category must have a stable lowercase snake_case name and a short description that helps an LLM classify memories.\n"
            "Do not include source, confidence, branch, session, or runtime metadata fields as categories.\n\n"
            "Return ONLY valid JSON in this shape:\n"
            '{"categories":[{"name":"category_name","description":"When to use this category"}]}\n\n'
            f"Use case: {req.use_case or 'General assistant memory'}\n"
            f"Memory depth: {req.memory_depth or 'Essential Insights'}\n"
            f"Multilingual: {bool(req.multilingual)}\n"
            f"Include: {req.include or ''}\n"
            f"Exclude: {req.exclude or ''}\n"
            f"Custom instructions: {req.custom_instructions or ''}"
        )
        response = llm.generate_response(
            [{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=SETTINGS_GENERATION_MAX_TOKENS,
            temperature=0,
            timeout=SETTINGS_GENERATION_TIMEOUT_SECONDS,
        )
        return {"categories": _parse_generated_categories(response)}
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


def _store_memory(
    memory_create: MemoryCreate,
    project_id: str,
    *,
    operation_context: MemoryOperationContext | None = None,
    pre_vector_write: Callable[[int], None] | None = None,
    sync_graph: bool = True,
) -> dict[str, Any]:
    settings = project_settings(_workspace_settings(), project_id)
    params = {
        k: v
        for k, v in memory_create.model_dump().items()
        if v is not None and k not in {"messages", "timestamp", "categories"}
    }
    if memory_create.infer is None:
        params["infer"] = DEFAULT_MEMORY_INFER
    metadata = dict(memory_create.metadata or {})
    allowed_categories = _category_names(settings)
    explicit_categories = _request_categories(memory_create.categories, metadata, allowed_categories)
    metadata.pop("category", None)
    metadata.pop("categories", None)
    if explicit_categories:
        metadata["categories"] = explicit_categories
    if allowed_categories:
        metadata["_allowed_categories"] = allowed_categories
    metadata["project_id"] = project_id
    created_at = _coerce_memory_timestamp(memory_create.timestamp)
    if created_at:
        metadata.setdefault("created_at", created_at)
        metadata.setdefault("source_created_at", created_at)
    params["metadata"] = metadata
    if not params.get("prompt"):
        prompt = _build_extraction_prompt(settings)
        if prompt:
            params["prompt"] = prompt
    retention = settings.get("retention") or {}
    if "expiration_date" not in params and retention.get("memory_decay", True) and retention.get("expiration_date"):
        params["expiration_date"] = retention["expiration_date"]
    if operation_context is not None:
        params["operation_context"] = operation_context
    if pre_vector_write is not None:
        params["pre_vector_write"] = pre_vector_write
    response = get_memory_instance().add(
        messages=[message.model_dump() for message in memory_create.messages],
        **params,
    )
    if response.get("results"):
        telemetry.log_dashboard_nudge_once(DASHBOARD_URL)
        if sync_graph:
            for item in response.get("results", []):
                graph_metadata = _memory_metadata_for_graph(item, params["metadata"])
                upsert_graph_memory(
                    str(item.get("id") or item.get("memory_id") or ""),
                    item.get("memory") or item.get("text") or item.get("data"),
                    {key: getattr(memory_create, key, None) for key in ("user_id", "agent_id", "app_id", "run_id")},
                    graph_metadata,
                )
        import_key = str(params["metadata"].get("import_key") or "") if operation_context is not None else ""
        _queue_memory_added_webhook(
            response,
            project_id,
            import_key=import_key or None,
            execution_guard=operation_context.execution_guard if operation_context is not None else None,
        )
    return response


_IMPORT_WEBHOOK_RESULT_KEYS = (
    "id",
    "memory_id",
    "memory",
    "text",
    "data",
    "event",
    "categories",
    "source_message_indices",
    "confidence",
)


def _queue_memory_added_webhook(
    response: dict[str, Any],
    project_id: str,
    *,
    import_key: str | None = None,
    execution_guard: Callable[[], None] | None = None,
) -> str:
    results = response.get("results") or []
    if not results:
        return ""
    event_data = response
    delivery_key = None
    if import_key:
        canonical_results = [
            {key: item[key] for key in _IMPORT_WEBHOOK_RESULT_KEYS if item.get(key) is not None} for item in results
        ]
        canonical_results.sort(key=lambda item: str(item.get("id") or item.get("memory_id") or ""))
        event_data = {"results": canonical_results}
        memory_ids = [str(item.get("id") or item.get("memory_id") or "") for item in canonical_results]
        material = json.dumps(
            {"project_id": project_id, "import_key": import_key, "memory_ids": memory_ids},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        delivery_key = f"memory-import-{hashlib.sha256(material.encode()).hexdigest()}"
    if execution_guard is not None:
        execution_guard()
    return queue_webhook_event(
        "memory.added",
        event_data,
        project_id,
        delivery_key=delivery_key,
    )


def _import_graph_items(
    response: dict[str, Any],
    memory_create: MemoryCreate,
    project_id: str,
) -> list[dict[str, Any]]:
    base_metadata = dict(memory_create.metadata or {})
    base_metadata["project_id"] = project_id
    entities = {key: getattr(memory_create, key, None) for key in ("user_id", "agent_id", "app_id", "run_id")}
    rows = []
    for item in response.get("results") or []:
        memory_id = str(item.get("id") or item.get("memory_id") or "")
        if not memory_id:
            continue
        rows.append(
            {
                "memory_id": memory_id,
                "text": item.get("memory") or item.get("text") or item.get("data"),
                "entities": entities,
                "metadata": _memory_metadata_for_graph(item, base_metadata),
            }
        )
    return rows


@app.post("/memories", summary="Create memories")
def add_memory(request: Request, memory_create: MemoryCreate, _auth=Depends(require_project_write)):
    """Store new memories."""
    _set_request_log_context(request, "ADD", memory_create.model_dump(mode="json"))
    if not any([memory_create.user_id, memory_create.agent_id, memory_create.app_id, memory_create.run_id]):
        raise HTTPException(
            status_code=400,
            detail="At least one identifier (user_id, agent_id, app_id, run_id) is required.",
        )

    project_id = get_project_id(request)
    _enforce_memory_storage_quota(request, _auth, enforce_hard=False)
    storage_quota_snapshot = _capture_import_storage_quota_snapshot(request, _auth)
    quota_guard = ImportStorageQuotaGuard(
        storage_quota_snapshot,
        SessionLocal,
        _count_memories_for_project,
        _organization_project_ids,
    )
    try:
        response = _store_memory(memory_create, project_id, pre_vector_write=quota_guard)
        _set_request_log_context(request, "ADD", memory_create.model_dump(mode="json"), response)
        return JSONResponse(content=response)
    except ImportStorageQuotaExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "storage_quota_exceeded",
                "metric": "stored_memories",
                "limit": exc.limit_value,
                "used": exc.used,
                "selected_new": exc.selected_new,
                "projected": exc.projected,
                "scope_type": exc.scope_type,
                "scope_id": exc.scope_id,
            },
        ) from exc
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except (ValueError, MemoryValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()
    finally:
        quota_guard.release()


MAX_IMPORT_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_IMPORT_FILE_BYTES = 256 * 1024 * 1024
IMPORT_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _save_import_uploads(
    files: list[UploadFile],
    upload_root: Path,
    *,
    reserve_bytes: Callable[[int], None] | None = None,
) -> tuple[list[Path], list[str], int]:
    saved: list[Path] = []
    names: list[str] = []
    ignored = 0
    total_bytes = 0
    upload_root.mkdir(parents=True, exist_ok=True)
    for index, upload in enumerate(files):
        original_name = (upload.filename or f"upload-{index}").replace("\\", "/")
        if not is_supported_input(original_name):
            ignored += 1
            await upload.close()
            continue
        relative = safe_upload_path(original_name, f"upload-{index}{Path(original_name).suffix.lower()}")
        target = upload_root / relative
        if target.exists():
            target = target.with_name(f"{index}-{target.name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        file_bytes = 0
        try:
            with target.open("wb") as destination:
                while chunk := await upload.read(IMPORT_UPLOAD_CHUNK_BYTES):
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > MAX_IMPORT_FILE_BYTES:
                        raise HTTPException(
                            status_code=413, detail=f"File is larger than 256 MB: {relative.as_posix()}"
                        )
                    if total_bytes > MAX_IMPORT_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="The combined upload is larger than 512 MB.")
                    if reserve_bytes is not None:
                        reserve_bytes(len(chunk))
                    destination.write(chunk)
        finally:
            await upload.close()
        saved.append(target)
        names.append(target.relative_to(upload_root).as_posix())
    return saved, names, ignored


def _vector_rows(result: Any) -> list[Any]:
    rows = result[0] if isinstance(result, tuple) else result
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        rows = rows[0]
    return rows if isinstance(rows, list) else []


def _batch_existing_import_keys(project_id: str, keys: set[str]) -> set[str]:
    if not keys:
        return set()
    vector_store = get_memory_instance().vector_store
    batch_method = getattr(vector_store, "existing_payload_values", None)
    if callable(batch_method):
        result = batch_method("import_key", keys, filters={"project_id": project_id})
        if isinstance(result, (set, list, tuple)):
            return {str(value) for value in result if value in keys}

    # Non-PGVector backends use one project-scoped list as a compatibility fallback.
    listed = vector_store.list(filters={"project_id": project_id}, top_k=max(1000, len(keys) * 20))
    return {
        str(row.payload.get("import_key"))
        for row in _vector_rows(listed)
        if getattr(row, "payload", None) and row.payload.get("import_key") in keys
    }


def _existing_import_memory_hashes(
    project_id: str,
    entities: dict[str, str],
) -> dict[tuple[str, str], str]:
    vector_store = get_memory_instance().vector_store
    expected_scope = canonical_entity_scope(entities)
    filters = {"project_id": project_id, **expected_scope}
    listed = vector_store.list(filters=filters, top_k=100000)
    hashes = {}
    for row in _vector_rows(listed):
        payload = getattr(row, "payload", None) or {}
        if canonical_entity_scope(payload) != expected_scope:
            continue
        conversation_id = payload.get("conversation_id")
        memory_hash = payload.get("hash")
        if conversation_id and memory_hash:
            hashes[(str(conversation_id), str(memory_hash))] = str(row.id)
    return hashes


def _import_rows_for_key(project_id: str, import_key: str) -> list[Any]:
    return _vector_rows(
        get_memory_instance().vector_store.list(
            filters={"project_id": project_id, "import_key": import_key},
            top_k=1000,
        )
    )


def _import_llm(model: str, *, use_import_route: bool = False):
    runtime_llm = deepcopy(get_current_config().get("llm") or {})
    runtime_config = deepcopy(runtime_llm.get("config") or {})
    if use_import_route and MEMORY_IMPORT_LLM_ROUTE_ENABLED:
        provider = MEMORY_IMPORT_LLM_PROVIDER
        api_key = os.environ.get(MEMORY_IMPORT_LLM_API_KEY_ENV, "").strip()
        config = _llm_config(
            provider,
            model,
            api_key=api_key,
            base_url=MEMORY_IMPORT_LLM_BASE_URL,
        )
        for key in ("temperature", "top_p"):
            if key in runtime_config:
                config[key] = runtime_config[key]
    else:
        provider = str(runtime_llm.get("provider") or DEFAULT_LLM_PROVIDER)
        config = runtime_config
    config["model"] = model
    config["max_tokens"] = MEMORY_IMPORT_LLM_MAX_OUTPUT_TOKENS
    if provider == "openai":
        config["request_timeout"] = MEMORY_IMPORT_LLM_TIMEOUT_SECONDS
        config["max_retries"] = 0
    fingerprint = hash(json.dumps(config, sort_keys=True, default=str))
    cache_key = (provider, model, fingerprint)
    with _import_llm_cache_lock:
        instance = _import_llm_cache.get(cache_key)
        if instance is None:
            instance = LlmFactory.create(provider, deepcopy(config))
            _import_llm_cache[cache_key] = instance
        return instance


def _ensure_import_chunk(execution: ChunkExecution):
    indices = execution.chunk.source_indices
    core_indices = execution.chunk.core_source_indices
    job = import_jobs.get(execution.job_id)
    if job is None:
        raise RuntimeError("Memory import job disappeared while preparing a chunk.")
    return import_repository.upsert_chunk(
        lease_owner=execution.lease_owner,
        job_id=execution.job_id,
        project_id=job.project_id,
        import_key=execution.import_key,
        conversation_id=execution.conversation.id,
        status="pending",
        attempt=max(0, execution.attempt - 1),
        chunk_index=execution.chunk_index,
        chunk_count=execution.chunk_count,
        source_message_start=min(indices) if indices else None,
        source_message_end=max(indices) if indices else None,
        source_message_indices=indices,
        core_source_message_indices=core_indices,
        source_turn_start=min(core_indices) if core_indices else None,
        source_turn_end=max(core_indices) if core_indices else None,
        token_count=execution.chunk.token_count,
        input_tokens=execution.chunk.token_count,
        parent_import_key=execution.chunk.parent_import_key,
        split_depth=execution.chunk.split_depth,
        overlap_turns=execution.chunk.overlap_turns,
        source_path=execution.conversation.source_path,
        conversation_title=execution.conversation.title,
    )


def _recompute_import_progress(job_id: str, lease_owner: str | None = None) -> None:
    with _import_progress_lock:
        chunks = import_repository.list_chunks(job_id)
        split_parents = [chunk for chunk in chunks if chunk.status == "split"]
        leaves = [chunk for chunk in chunks if chunk.status != "split"]
        imported = [chunk for chunk in leaves if chunk.status == "succeeded"]
        skipped = [chunk for chunk in leaves if chunk.status == "skipped"]
        failed = [chunk for chunk in leaves if chunk.status == "failed"]
        import_jobs.update(
            job_id,
            lease_owner=lease_owner,
            processed_chunks=len(imported) + len(skipped) + len(failed),
            imported_chunks=len(imported),
            skipped_chunks=len(skipped),
            failed_chunks=len(failed),
            split_chunks=len(split_parents),
            memories_created=sum(len(chunk.memory_ids or []) for chunk in imported),
        )


def _assert_import_lease(job_id: str, lease_owner: str | None) -> None:
    if lease_owner is not None:
        import_repository.assert_job_lease(job_id, lease_owner)


def _import_execution_guard(
    job_id: str,
    lease_owner: str | None,
) -> Callable[[], None] | None:
    if lease_owner is None:
        return None

    def guard() -> None:
        import_repository.assert_job_lease(job_id, lease_owner)

    return guard


def _claim_import_chunk(execution: ChunkExecution, _payload: dict[str, Any]) -> str:
    chunk = _ensure_import_chunk(execution)
    if chunk.status == "succeeded":
        return "resume_succeeded"

    claimed, manifest = import_repository.claim_manifest(
        chunk.project_id,
        execution.import_key,
        execution.job_id,
        chunk.id,
        lease_owner=execution.lease_owner,
    )
    if claimed:
        execution.reconcile_existing = int(manifest.attempts or 0) > 1
        return "claimed"
    if manifest.status == "split":
        return "split"
    if manifest.status in {"completed", "imported", "succeeded"}:
        if manifest.job_id == execution.job_id:
            if chunk.status != "succeeded":
                import_repository.update_chunk(
                    execution.job_id,
                    execution.import_key,
                    lease_owner=execution.lease_owner,
                    status="succeeded",
                    memory_ids=list(manifest.memory_ids or []),
                    finished_at=datetime.now(timezone.utc),
                    error_type=None,
                    error_message=None,
                )
                _recompute_import_progress(execution.job_id, execution.lease_owner)
            return "resume_succeeded"
        return "succeeded"

    if manifest.status == "claimed" and manifest.job_id == execution.job_id and manifest.chunk_id == chunk.id:
        existing_rows = _import_rows_for_key(chunk.project_id, execution.import_key)
        if existing_rows:
            execution.reconcile_existing = True
            return "claimed"
        import_repository.mark_manifest(
            chunk.project_id,
            execution.import_key,
            "released",
            job_id=execution.job_id,
            lease_owner=execution.lease_owner,
        )
        import_repository.release_memory_hashes(
            execution.job_id,
            chunk.id,
            lease_owner=execution.lease_owner,
        )
        claimed, reclaimed_manifest = import_repository.claim_manifest(
            chunk.project_id,
            execution.import_key,
            execution.job_id,
            chunk.id,
            lease_owner=execution.lease_owner,
        )
        execution.reconcile_existing = bool(claimed and int(reclaimed_manifest.attempts or 0) > 1)
        return "claimed" if claimed else "busy"
    return "busy"


def _update_import_chunk(execution: ChunkExecution, state: str, details: dict[str, Any]) -> None:
    chunk = import_repository.get_chunk(execution.job_id, execution.import_key) or _ensure_import_chunk(execution)
    now = datetime.now(timezone.utc)
    values: dict[str, Any] = {"status": state, "attempt": execution.attempt}
    if state == "processing":
        values.update(started_at=chunk.started_at or now, error_type=None, error_message=None)
    elif state == "retrying":
        values.update(
            retry_count=int(chunk.retry_count or 0) + 1,
            error_type=details.get("error_type"),
            error_message=details.get("error"),
        )
        import_jobs.add_error(
            execution.job_id,
            execution.conversation.source_path,
            str(details.get("error") or "Import chunk retry scheduled."),
            error_type=details.get("error_type"),
            error_code=details.get("error_code"),
            error_details=details.get("error_details"),
            attempt=execution.attempt,
            retryable=bool(details.get("retryable", True)),
            import_key=execution.import_key,
            lease_owner=execution.lease_owner,
        )
    elif state == "succeeded":
        values.update(
            finished_at=now,
            duration_seconds=details.get("duration_seconds"),
            timings=details.get("timings") or {},
            memory_ids=details.get("memory_ids") or [],
            model_used=details.get("model_used"),
            fallback_used=bool(details.get("fallback_reason")),
            fallback_reason=details.get("fallback_reason"),
            audit_result=details.get("audit_result"),
            audit_metadata=details.get("audit_metadata") or {},
            claimed_memory_hashes=details.get("claimed_memory_hashes") or [],
            error_type=None,
            error_message=None,
        )
    elif state == "skipped":
        skip_reason = details.get("reason", "duplicate")
        values.update(
            finished_at=now,
            audit_metadata={"skip_reason": skip_reason},
            error_type=None,
            error_message=None,
            next_retry_at=None,
        )
        if skip_reason == "superseded_split":
            values.update(memory_ids=[], claimed_memory_hashes=[])
            import_repository.release_memory_hashes(
                execution.job_id,
                chunk.id,
                lease_owner=execution.lease_owner,
            )
        manifests = import_repository.load_manifests(chunk.project_id, [execution.import_key])
        manifest = manifests.get(execution.import_key)
        if manifest is None:
            claimed, _manifest = import_repository.claim_manifest(
                chunk.project_id,
                execution.import_key,
                execution.job_id,
                chunk.id,
                lease_owner=execution.lease_owner,
            )
            if claimed:
                import_repository.mark_manifest(
                    chunk.project_id,
                    execution.import_key,
                    "succeeded",
                    memory_ids=[],
                    job_id=execution.job_id,
                    lease_owner=execution.lease_owner,
                )
        elif skip_reason == "superseded_split" and manifest.status not in {
            "completed",
            "imported",
            "succeeded",
        }:
            owns_manifest = manifest.job_id == execution.job_id
            if not owns_manifest:
                owns_manifest, _manifest = import_repository.claim_manifest(
                    chunk.project_id,
                    execution.import_key,
                    execution.job_id,
                    chunk.id,
                    lease_owner=execution.lease_owner,
                )
            if owns_manifest:
                import_repository.mark_manifest(
                    chunk.project_id,
                    execution.import_key,
                    "succeeded",
                    memory_ids=[],
                    job_id=execution.job_id,
                    lease_owner=execution.lease_owner,
                    last_error=None,
                )
    elif state == "split":
        values.update(
            finished_at=now,
            error_type="adaptive_split",
            error_message=details.get("error"),
            audit_metadata={"children": details.get("children", 2)},
        )
        import_repository.mark_manifest(
            chunk.project_id,
            execution.import_key,
            "split",
            job_id=execution.job_id,
            lease_owner=execution.lease_owner,
        )
        import_repository.release_memory_hashes(
            execution.job_id,
            chunk.id,
            lease_owner=execution.lease_owner,
        )
    elif state == "cancelled":
        values.update(finished_at=None, error_type=None, error_message=None)
        import_repository.mark_manifest(
            chunk.project_id,
            execution.import_key,
            "released",
            job_id=execution.job_id,
            lease_owner=execution.lease_owner,
        )
        import_repository.release_memory_hashes(
            execution.job_id,
            chunk.id,
            lease_owner=execution.lease_owner,
        )
    elif state == "busy":
        values.update(
            status="failed",
            finished_at=now,
            error_type=details.get("error_type") or "import_claim_busy",
            error_message=details.get("error"),
        )
    elif state == "failed":
        values.update(
            finished_at=now,
            duration_seconds=details.get("duration_seconds"),
            timings=details.get("timings") or {},
            error_type=details.get("error_type") or "extraction_error",
            error_message=details.get("error"),
        )
        import_repository.mark_manifest(
            chunk.project_id,
            execution.import_key,
            "failed",
            last_error=details.get("error"),
            job_id=execution.job_id,
            lease_owner=execution.lease_owner,
        )
    import_repository.update_chunk(
        execution.job_id,
        execution.import_key,
        lease_owner=execution.lease_owner,
        **values,
    )
    if state in {"succeeded", "skipped", "busy", "failed"}:
        _recompute_import_progress(execution.job_id, execution.lease_owner)


def _reconcile_import_vector_rows(
    execution: ChunkExecution,
    chunk: Any,
    job: Any,
    memory_create: MemoryCreate,
) -> dict[str, Any] | None:
    execution_guard = _import_execution_guard(execution.job_id, execution.lease_owner)
    if execution_guard is not None:
        execution_guard()
    rows = _import_rows_for_key(job.project_id, execution.import_key)
    if not rows:
        return None
    conversation_scope = scoped_conversation_hash(execution.conversation.id, job.entities)

    results: list[dict[str, Any]] = []
    ids_by_hash: dict[str, str] = {}
    persisted_records: list[tuple[str, str, None, dict[str, Any]]] = []
    for row in rows:
        payload = dict(getattr(row, "payload", None) or {})
        graph_metadata = {key: value for key, value in payload.items() if key not in {"data", "text_lemmatized"}}
        memory_id = str(row.id)
        item = {
            "id": memory_id,
            "memory": payload.get("data"),
            "event": "ADD",
            "metadata": graph_metadata,
        }
        for key in ("categories", "source_message_indices", "confidence"):
            if key in payload:
                item[key] = payload[key]
        results.append(item)
        memory_text = payload.get("data")
        if not isinstance(memory_text, str) or not memory_text.strip():
            raise RuntimeError("Persisted import vector row is missing memory text.")
        persisted_records.append((memory_id, memory_text, None, payload))
        memory_hash = payload.get("hash")
        if memory_hash:
            ids_by_hash[str(memory_hash)] = memory_id

    memory_instance = get_memory_instance()
    complete_side_effects = getattr(memory_instance, "_complete_import_side_effects", None)
    if not callable(complete_side_effects):
        raise RuntimeError("Memory implementation cannot reconcile import side effects.")
    import_metadata = dict(memory_create.metadata or {})
    import_metadata["project_id"] = job.project_id
    source_messages = import_metadata.get("source_messages")
    if not isinstance(source_messages, list) or not source_messages:
        source_messages = [
            {
                **message.model_dump(),
                "source_index": index,
            }
            for index, message in enumerate(memory_create.messages)
        ]
    import_filters = {
        key: value
        for key in ("user_id", "agent_id", "app_id", "run_id")
        if (value := getattr(memory_create, key, None))
    }
    import_filters["project_id"] = job.project_id
    complete_side_effects(
        persisted_records,
        source_messages,
        import_filters,
        import_metadata,
        operation_context=MemoryOperationContext(execution_guard=execution_guard),
    )

    import_repository.mark_memory_hashes_succeeded(
        job.project_id,
        conversation_scope,
        ids_by_hash,
        job_id=execution.job_id,
        chunk_id=chunk.id,
        memory_ids=ids_by_hash,
        lease_owner=execution.lease_owner,
    )
    response = {"results": results}
    graph_items = _import_graph_items(response, memory_create, job.project_id)
    if graph_items:
        import_repository.add_graph_items(
            execution.job_id,
            chunk.id,
            graph_items,
            lease_owner=execution.lease_owner,
        )
    memory_ids = [str(row.id) for row in rows]
    _queue_memory_added_webhook(
        response,
        job.project_id,
        import_key=execution.import_key,
        execution_guard=execution_guard,
    )
    import_repository.mark_manifest(
        job.project_id,
        execution.import_key,
        "succeeded",
        memory_ids=memory_ids,
        job_id=execution.job_id,
        lease_owner=execution.lease_owner,
    )
    return {
        "response": response,
        "memory_ids": memory_ids,
        "model_used": chunk.model_used,
        "fallback_reason": chunk.fallback_reason,
        "audit_result": chunk.audit_result,
        "audit_metadata": chunk.audit_metadata or {},
        "claimed_memory_hashes": list(ids_by_hash),
    }


def _store_import_chunk(
    payload: dict[str, Any],
    execution: ChunkExecution,
    options: ImportOptions,
) -> dict[str, Any]:
    chunk = import_repository.get_chunk(execution.job_id, execution.import_key) or _ensure_import_chunk(execution)
    job = import_jobs.get(execution.job_id)
    if job is None:
        raise RuntimeError("Memory import job disappeared before storage.")
    conversation_scope = scoped_conversation_hash(execution.conversation.id, job.entities)
    scope_hash = entity_scope_hash(job.entities)

    memory_create = MemoryCreate.model_validate(payload)
    if execution.reconcile_existing or execution.attempt > 1:
        reconciled = _reconcile_import_vector_rows(execution, chunk, job, memory_create)
        if reconciled is not None:
            return reconciled

    tiering = options.model_tiering_enabled
    runtime_model = str((get_current_config().get("llm") or {}).get("config", {}).get("model") or DEFAULT_LLM_MODEL)

    def phase_callback(phase: str, seconds: float) -> None:
        execution.phase_callback(phase, seconds)

    def claim_memory_hash(memory_hash: str) -> bool:
        with _import_legacy_hashes_lock:
            legacy_memory_id = _import_legacy_hashes.get(execution.job_id, {}).get(
                (execution.conversation.id, memory_hash)
            )
        if legacy_memory_id is not None:
            claimed = import_repository.claim_memory_hash(
                job.project_id,
                conversation_scope,
                memory_hash,
                execution.job_id,
                chunk.id,
                lease_owner=execution.lease_owner,
            )
            if claimed:
                import_repository.mark_memory_hashes_succeeded(
                    job.project_id,
                    conversation_scope,
                    [memory_hash],
                    job_id=execution.job_id,
                    chunk_id=chunk.id,
                    memory_ids={memory_hash: legacy_memory_id},
                    lease_owner=execution.lease_owner,
                )
            return False
        return import_repository.claim_memory_hash(
            job.project_id,
            conversation_scope,
            memory_hash,
            execution.job_id,
            chunk.id,
            lease_owner=execution.lease_owner,
        )

    if tiering:
        primary_llm = _import_llm(options.fast_model, use_import_route=True)
        fallback_llm = _import_llm(options.fallback_model, use_import_route=True)
    else:
        primary_llm = _import_llm(runtime_model)
        fallback_llm = None

    quota_guard = ImportStorageQuotaGuard(
        job.storage_quota_snapshot,
        SessionLocal,
        _count_memories_for_project,
        _organization_project_ids,
    )

    context = MemoryOperationContext(
        primary_llm=primary_llm,
        fallback_llm=fallback_llm,
        primary_model_label=options.fast_model if tiering else runtime_model,
        fallback_model_label=options.fallback_model,
        force_fallback=bool(tiering and execution.force_fallback_reason),
        force_fallback_reason=execution.force_fallback_reason,
        require_source_message_indices=True,
        source_messages=list(payload["metadata"].get("source_messages") or []),
        core_source_message_indices=list(payload["metadata"].get("core_source_message_indices") or []) or None,
        min_confidence=options.min_confidence,
        audit=bool(tiering and execution.audit),
        audit_use_more_complete=True,
        obvious_fact_empty_fallback=bool(tiering and execution.obvious_facts),
        strict_vector_write=True,
        phase_callback=phase_callback,
        memory_hash_claim=claim_memory_hash,
        execution_guard=_import_execution_guard(execution.job_id, execution.lease_owner),
        pre_vector_write=quota_guard,
        id_factory=lambda memory_hash: str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"yiqiao:{job.project_id}:{scope_hash}:{execution.conversation.id}:{memory_hash}",
            )
        ),
    )
    try:
        response = _store_memory(
            memory_create,
            job.project_id,
            operation_context=context,
            sync_graph=False,
        )
    except Exception:
        import_repository.release_memory_hashes(
            execution.job_id,
            chunk.id,
            context.claimed_memory_hashes,
            lease_owner=execution.lease_owner,
        )
        raise
    finally:
        quota_guard.release()

    memory_ids = [
        str(item.get("id") or item.get("memory_id"))
        for item in response.get("results") or []
        if item.get("id") or item.get("memory_id")
    ]
    ids_by_hash = dict(zip(context.claimed_memory_hashes, memory_ids, strict=False))
    import_repository.mark_memory_hashes_succeeded(
        job.project_id,
        conversation_scope,
        context.claimed_memory_hashes,
        job_id=execution.job_id,
        chunk_id=chunk.id,
        memory_ids=ids_by_hash,
        lease_owner=execution.lease_owner,
    )
    graph_items = _import_graph_items(response, memory_create, job.project_id)
    if graph_items:
        import_repository.add_graph_items(
            execution.job_id,
            chunk.id,
            graph_items,
            lease_owner=execution.lease_owner,
        )
    import_repository.mark_manifest(
        job.project_id,
        execution.import_key,
        "succeeded",
        memory_ids=memory_ids,
        job_id=execution.job_id,
        lease_owner=execution.lease_owner,
    )
    return {
        "response": response,
        "memory_ids": memory_ids,
        "model_used": context.model_used,
        "fallback_reason": context.fallback_reason,
        "pressure_fallback": context.provider_pressure_recovered,
        "audit_result": context.audit_result,
        "audit_metadata": context.audit_metadata,
        "claimed_memory_hashes": context.claimed_memory_hashes,
    }


def _sync_import_graph(
    job_id: str,
    *,
    include_failed: bool = False,
    lease_owner: str | None = None,
) -> str:
    statuses = ["pending", "failed"] if include_failed else ["pending"]
    items = import_repository.list_graph_items(job_id, status=statuses)
    if not items:
        return "completed"
    if not graph_is_configured():
        return "disabled"
    item_ids = [item.id for item in items]
    try:
        _assert_import_lease(job_id, lease_owner)
        upsert_memories_batch([dict(item.payload or {}) for item in items])
    except Exception as exc:
        import_repository.mark_graph_items(
            item_ids,
            "failed",
            str(exc),
            increment_attempts=False,
            job_id=job_id,
            lease_owner=lease_owner,
        )
        all_items = import_repository.list_graph_items(job_id, status=None)
        import_jobs.update(
            job_id,
            lease_owner=lease_owner,
            graph_pending_items=sum(item.status == "pending" for item in all_items),
            graph_synced_items=sum(item.status == "synced" for item in all_items),
            graph_failed_items=sum(item.status == "failed" for item in all_items),
        )
        raise
    import_repository.mark_graph_items(
        item_ids,
        "synced",
        increment_attempts=False,
        job_id=job_id,
        lease_owner=lease_owner,
    )
    all_items = import_repository.list_graph_items(job_id, status=None)
    import_jobs.update(
        job_id,
        lease_owner=lease_owner,
        graph_pending_items=sum(item.status == "pending" for item in all_items),
        graph_synced_items=sum(item.status == "synced" for item in all_items),
        graph_failed_items=sum(item.status == "failed" for item in all_items),
    )
    return "completed"


def _import_hooks(job_id: str, lease_owner: str | None = None) -> ImportRuntimeHooks:
    job = import_jobs.get(job_id)
    if job is None:
        raise RuntimeError("Memory import job not found.")

    def load_existing(project_id: str, keys: set[str]) -> set[str]:
        manifests = import_repository.load_manifests(project_id, keys)
        same_job = {key for key, manifest in manifests.items() if manifest.job_id == job_id}
        completed_elsewhere = {
            key
            for key, manifest in manifests.items()
            if manifest.job_id != job_id and manifest.status in {"completed", "imported", "succeeded"}
        }
        vector_keys = _batch_existing_import_keys(project_id, keys)
        return (completed_elsewhere | vector_keys) - same_job

    def load_chunk_statuses(current_job_id: str) -> dict[str, str]:
        _recompute_import_progress(current_job_id, lease_owner)
        return import_repository.load_persisted_chunk_statuses(
            job.project_id,
            current_job_id,
        )

    def sync_graph(current_job_id: str) -> str:
        if lease_owner is None:
            return _sync_import_graph(current_job_id, include_failed=True)
        return _sync_import_graph(
            current_job_id,
            include_failed=True,
            lease_owner=lease_owner,
        )

    return ImportRuntimeHooks(
        load_existing_keys=load_existing,
        load_chunk_statuses=load_chunk_statuses,
        claim_chunk=_claim_import_chunk,
        update_chunk=_update_import_chunk,
        sync_graph=sync_graph,
        finalize_workspace=lambda current_job_id, source_retry_required: _prepare_terminal_import_workspace(
            current_job_id,
            source_retry_required,
            lease_owner=lease_owner,
        ),
    )


def _options_from_import_job(job) -> ImportOptions:
    return ImportOptions.from_persisted_snapshot(job.options_snapshot)


def _import_job_paths(job) -> tuple[Path, Path, list[Path]]:
    if not job.workspace:
        raise RuntimeError("Memory import workspace is missing.")
    workspace = Path(job.workspace)
    upload_root = workspace / "uploads"
    resolved_root = upload_root.resolve()
    input_paths = []
    for name in job.input_files:
        candidate = (upload_root / Path(str(name).replace("/", os.sep))).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise RuntimeError("Memory import contains an invalid persisted upload path.") from exc
        if not candidate.is_file():
            raise FileNotFoundError(f"Persisted import upload is missing: {name}")
        input_paths.append(candidate)
    return workspace, upload_root, input_paths


def _validated_import_workspace(job) -> Path:
    if not job.workspace:
        raise RuntimeError("Memory import workspace is missing.")
    storage_root = MEMORY_IMPORT_STORAGE_ROOT.resolve()
    workspace = Path(job.workspace).resolve()
    try:
        relative = workspace.relative_to(storage_root)
    except ValueError as exc:
        raise RuntimeError("Memory import workspace is outside the configured storage root.") from exc
    if len(relative.parts) != 1 or relative.name != job.id:
        raise RuntimeError("Memory import workspace does not match its job ID.")
    return workspace


def _delete_import_workspace(job) -> None:
    workspace = _validated_import_workspace(job)
    if workspace.exists():
        shutil.rmtree(workspace)


def _record_import_exception(
    job_id: str,
    source: str,
    exc: BaseException,
    *,
    error_type: str,
    retryable: bool,
    operation_phase: str,
    lease_owner: str | None = None,
) -> str:
    error_code, error_details = _import_error_diagnostics(exc)
    error_details.setdefault("operation_phase", operation_phase)
    error_details.setdefault("failure_point", operation_phase)
    safe_message = _safe_import_error_message(exc)
    import_jobs.add_error(
        job_id,
        source,
        safe_message,
        error_type=error_type,
        error_code=error_code,
        error_details=error_details,
        retryable=retryable,
        lease_owner=lease_owner,
    )
    return safe_message


def _record_workspace_cleanup_failure(job, exc: Exception, *, lease_owner: str | None = None) -> None:
    logging.warning("Memory import %s workspace cleanup failed", job.id, exc_info=True)
    try:
        _record_import_exception(
            job.id,
            "workspace_cleanup",
            exc,
            error_type="workspace_cleanup_error",
            retryable=False,
            operation_phase="workspace_cleanup",
            lease_owner=lease_owner,
        )
    except ImportLeaseLost:
        pass


def _cleanup_terminal_import_workspace(job_id: str, *, lease_owner: str | None = None) -> bool:
    job = import_jobs.get(job_id, refresh=True)
    if (
        job is None
        or not job.workspace
        or job.status not in {"completed", "completed_with_errors"}
        or job.source_retry_required
    ):
        return False
    try:
        _assert_import_lease(job.id, lease_owner)
        _delete_import_workspace(job)
    except ImportLeaseLost:
        raise
    except Exception as exc:
        _record_workspace_cleanup_failure(job, exc, lease_owner=lease_owner)
        return False
    updated = import_jobs.update(
        job.id,
        workspace=None,
        workspace_bytes=0,
        lease_owner=lease_owner,
    )
    if updated is None:
        logging.warning("Memory import %s workspace was deleted but its reservation was not cleared", job.id)
        return False
    return True


def _prepare_terminal_import_workspace(
    job_id: str,
    source_retry_required: bool,
    *,
    lease_owner: str | None = None,
) -> bool:
    job = import_jobs.get(job_id, refresh=True)
    if job is None or not job.workspace:
        return True
    if source_retry_required:
        return _prune_import_extraction(job, lease_owner=lease_owner)
    try:
        _assert_import_lease(job.id, lease_owner)
        _delete_import_workspace(job)
    except ImportLeaseLost:
        raise
    except Exception as exc:
        _record_workspace_cleanup_failure(job, exc, lease_owner=lease_owner)
        return False
    updated = import_jobs.update(
        job.id,
        workspace=None,
        workspace_bytes=0,
        lease_owner=lease_owner,
    )
    if updated is None:
        logging.warning("Memory import %s workspace was deleted but its reservation was not cleared", job.id)
        return False
    return True


def _prune_import_extraction(job, *, lease_owner: str | None = None) -> bool:
    try:
        _assert_import_lease(job.id, lease_owner)
        workspace = _validated_import_workspace(job)
        extraction_root = workspace / "extracted"
        if extraction_root.exists():
            shutil.rmtree(extraction_root)
        return True
    except ImportLeaseLost:
        raise
    except Exception as exc:
        _record_workspace_cleanup_failure(job, exc, lease_owner=lease_owner)
        return False


def _finalize_terminal_import_workspace(job_id: str, *, lease_owner: str | None = None) -> None:
    job = import_jobs.get(job_id, refresh=True)
    if job is None or job.status not in {"cancelled", "completed", "completed_with_errors", "failed"}:
        return
    if not job.source_retry_required:
        _cleanup_terminal_import_workspace(job.id, lease_owner=lease_owner)
    elif job.workspace:
        _prune_import_extraction(job, lease_owner=lease_owner)


def _directory_bytes(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _reconcile_import_workspace_accounting() -> None:
    try:
        records = import_repository.list_jobs_with_workspaces()
    except Exception:
        logging.warning("Memory import workspace accounting scan failed", exc_info=True)
        return
    for record in records:
        job = import_jobs.get(record.id, refresh=True)
        if job is None or not job.workspace:
            continue
        if job.status in {"completed", "completed_with_errors"} and not job.source_retry_required:
            _cleanup_terminal_import_workspace(job.id)
            continue
        try:
            workspace = _validated_import_workspace(job)
            retained_bytes = _directory_bytes(workspace / "uploads")
            import_repository.reconcile_workspace_bytes(job.id, retained_bytes)
        except Exception:
            logging.warning("Memory import %s workspace accounting failed", job.id, exc_info=True)


def _expire_stale_import_upload(job_id: str) -> None:
    lease_owner = str(uuid.uuid4())
    if not import_repository.acquire_job_lease(
        job_id,
        lease_owner,
        lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
    ):
        return
    try:
        job = import_jobs.get(job_id, refresh=True)
        if job is None or job.status != "uploading":
            return
        try:
            _delete_import_workspace(job)
        except Exception as exc:
            _record_workspace_cleanup_failure(job, exc, lease_owner=lease_owner)
            import_jobs.update(
                job.id,
                status="failed",
                phase="failed",
                source_retry_required=False,
                active_workers=0,
                lease_owner=lease_owner,
            )
            return
        import_jobs.add_error(
            job.id,
            "upload",
            "The upload was interrupted before the import was queued.",
            error_type="upload_interrupted",
            retryable=False,
            lease_owner=lease_owner,
        )
        import_jobs.update(
            job.id,
            status="failed",
            phase="failed",
            workspace=None,
            workspace_bytes=0,
            source_retry_required=False,
            active_workers=0,
            lease_owner=lease_owner,
        )
    except ImportLeaseLost:
        return
    finally:
        import_repository.release_job_lease(job_id, lease_owner)


def _abort_import_upload(job_id: str, lease_owner: str) -> None:
    job = import_jobs.get(job_id, refresh=True)
    if job is None or job.status != "uploading":
        return
    try:
        _delete_import_workspace(job)
    except Exception as exc:
        _record_workspace_cleanup_failure(job, exc, lease_owner=lease_owner)
        try:
            import_jobs.update(
                job.id,
                status="failed",
                phase="failed",
                source_retry_required=False,
                active_workers=0,
                lease_owner=lease_owner,
            )
        except ImportLeaseLost:
            pass
        finally:
            import_repository.release_job_lease(job.id, lease_owner)
        return
    try:
        if import_repository.delete_uploading_job(job.id, lease_owner):
            import_jobs.forget(job.id)
        else:
            logging.warning("Memory import %s upload reservation could not be released", job.id)
    finally:
        import_repository.release_job_lease(job.id, lease_owner)


def _renew_memory_import_lease(
    job_id: str,
    lease_owner: str,
    stop: threading.Event,
    lease_lost: threading.Event,
) -> None:
    while not stop.wait(MEMORY_IMPORT_LEASE_RENEW_SECONDS):
        try:
            if not import_repository.renew_job_lease(
                job_id,
                lease_owner,
                lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
            ):
                logging.warning("Memory import %s lost its execution lease", job_id)
                lease_lost.set()
                return
            record = import_repository.get_job(job_id)
            if record is not None and record.cancel_requested:
                import_jobs.request_cancel(job_id, record.project_id)
        except Exception:
            logging.warning("Memory import %s lease renewal failed", job_id, exc_info=True)
            lease_lost.set()
            return


def _run_memory_import(job_id: str, lease_owner: str) -> None:
    lease_stop = threading.Event()
    lease_lost = threading.Event()
    lease_thread = threading.Thread(
        target=_renew_memory_import_lease,
        args=(job_id, lease_owner, lease_stop, lease_lost),
        name=f"memory-import-lease-{job_id[:8]}",
        daemon=True,
    )
    lease_thread.start()
    try:
        try:
            job = import_jobs.get(job_id)
            if job is None:
                return
            options = _options_from_import_job(job)
            workspace, upload_root, input_paths = _import_job_paths(job)
            dedup_started = time.perf_counter()
            legacy_hashes = _existing_import_memory_hashes(job.project_id, options.entities)
            with _import_legacy_hashes_lock:
                _import_legacy_hashes[job.id] = legacy_hashes
            import_jobs.record_phase(
                job.id,
                "deduplication",
                time.perf_counter() - dedup_started,
                lease_owner=lease_owner,
            )
            run_import_job(
                job.id,
                input_paths,
                workspace,
                workspace / "extracted",
                options,
                lambda _payload: {},
                display_root=upload_root,
                store_payload_with_context=lambda payload, execution: _store_import_chunk(
                    payload,
                    execution,
                    options,
                ),
                hooks=_import_hooks(job.id, lease_owner),
                retain_workspace=True,
                external_cancelled=lease_lost.is_set,
                lease_owner=lease_owner,
            )
        except ImportLeaseLost:
            lease_lost.set()
        except Exception as exc:
            if not lease_lost.is_set():
                try:
                    _record_import_exception(
                        job_id,
                        "import_runner",
                        exc,
                        error_type="runner_error",
                        retryable=True,
                        operation_phase="import_runner",
                        lease_owner=lease_owner,
                    )
                    import_jobs.update(
                        job_id,
                        status="failed",
                        phase="failed",
                        source_retry_required=True,
                        active_workers=0,
                        lease_owner=lease_owner,
                    )
                except ImportLeaseLost:
                    lease_lost.set()
    finally:
        if not lease_lost.is_set():
            try:
                _finalize_terminal_import_workspace(job_id, lease_owner=lease_owner)
            except ImportLeaseLost:
                lease_lost.set()
            except Exception:
                logging.warning("Memory import %s terminal workspace finalization failed", job_id, exc_info=True)
        lease_stop.set()
        lease_thread.join(timeout=MEMORY_IMPORT_LEASE_RENEW_SECONDS + 1)
        try:
            import_repository.release_job_lease(job_id, lease_owner)
        except Exception:
            logging.warning("Memory import %s lease release failed", job_id, exc_info=True)
        with _import_legacy_hashes_lock:
            _import_legacy_hashes.pop(job_id, None)
        with _import_threads_lock:
            if _import_threads.get(job_id) is threading.current_thread():
                _import_threads.pop(job_id, None)
            pending_owner = _import_pending_leases.pop(job_id, None)

        fresh_job = import_jobs.get(job_id, refresh=True)
        if pending_owner is not None:
            if fresh_job is not None and not fresh_job.cancel_requested and fresh_job.status == "queued":
                _start_memory_import_thread(job_id, pending_owner)
            else:
                import_repository.release_job_lease(job_id, pending_owner)
        elif lease_lost.is_set():
            _submit_memory_import(job_id, reset_for_recovery=True)
        elif fresh_job is not None and fresh_job.status == "queued" and not fresh_job.cancel_requested:
            _submit_memory_import(job_id)


def _start_memory_import_thread(job_id: str, lease_owner: str) -> bool:
    with _import_threads_lock:
        existing = _import_threads.get(job_id)
        if existing is not None and existing.is_alive():
            _import_pending_leases[job_id] = lease_owner
            return True
        thread = threading.Thread(
            target=_run_memory_import,
            args=(job_id, lease_owner),
            name=f"memory-import-{job_id[:8]}",
            daemon=True,
        )
        _import_threads[job_id] = thread
        try:
            thread.start()
        except Exception:
            _import_threads.pop(job_id, None)
            import_repository.release_job_lease(job_id, lease_owner)
            raise
        return True


def _submit_memory_import(job_id: str, *, reset_for_recovery: bool = False) -> bool:
    with _import_threads_lock:
        existing = _import_threads.get(job_id)
        if existing is not None and existing.is_alive():
            return False
        lease_owner = str(uuid.uuid4())
        try:
            acquired = import_repository.acquire_job_lease(
                job_id,
                lease_owner,
                lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
            )
        except Exception:
            logging.warning("Memory import %s lease acquisition failed", job_id, exc_info=True)
            return False
        if not acquired:
            return False
        try:
            if reset_for_recovery:
                import_jobs.get(job_id, refresh=True)
                import_jobs.update(
                    job_id,
                    status="queued",
                    phase="queued",
                    active_workers=0,
                    discovered_files=0,
                    parsed_files=0,
                    skipped_files=0,
                    total_conversations=0,
                    total_chunks=0,
                    total_tokens=0,
                    current_file=None,
                    current_conversation=None,
                    lease_owner=lease_owner,
                )
            return _start_memory_import_thread(job_id, lease_owner)
        except ImportLeaseLost:
            import_repository.release_job_lease(job_id, lease_owner)
            return False
        except Exception:
            import_repository.release_job_lease(job_id, lease_owner)
            raise


def _start_graph_retry_thread(job_id: str, lease_owner: str) -> bool:
    thread = threading.Thread(
        target=_run_graph_retry,
        args=(job_id, lease_owner),
        name=f"memory-import-graph-{job_id[:8]}",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        import_repository.release_job_lease(job_id, lease_owner)
        raise
    return True


def _submit_graph_retry_recovery(job_id: str) -> bool:
    lease_owner = str(uuid.uuid4())
    try:
        acquired = import_repository.acquire_job_lease(
            job_id,
            lease_owner,
            lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
        )
    except Exception:
        logging.warning("Memory import %s graph-retry lease acquisition failed", job_id, exc_info=True)
        return False
    if not acquired:
        return False
    try:
        return _start_graph_retry_thread(job_id, lease_owner)
    except Exception:
        logging.warning("Memory import %s graph-retry recovery start failed", job_id, exc_info=True)
        return False


def _resume_memory_imports() -> None:
    try:
        records = import_repository.list_recoverable_jobs()
    except Exception:
        logging.warning("Memory import recovery scan failed", exc_info=True)
        return
    for record in records:
        if record.status == "uploading":
            _expire_stale_import_upload(record.id)
            continue
        job = import_jobs.get(record.id, refresh=True)
        if job is None:
            continue
        if job.workspace and job.workspace_bytes == 0:
            try:
                workspace = _validated_import_workspace(job)
                import_repository.reconcile_workspace_bytes(
                    job.id,
                    _directory_bytes(workspace / "uploads"),
                )
                job = import_jobs.get(job.id, refresh=True) or job
            except Exception:
                logging.warning("Memory import %s workspace accounting failed", job.id, exc_info=True)
        if job.status == "cancelling" or job.cancel_requested:
            import_jobs.update(
                job.id,
                status="cancelled",
                phase="cancelled",
                source_retry_required=True,
                active_workers=0,
            )
            _finalize_terminal_import_workspace(job.id)
            continue
        if job.status == "syncing_graph":
            _submit_graph_retry_recovery(job.id)
            continue
        try:
            _import_job_paths(job)
        except Exception as exc:
            _record_import_exception(
                job.id,
                "recovery",
                exc,
                error_type="recovery_error",
                retryable=False,
                operation_phase="recovery",
            )
            import_jobs.update(job.id, status="failed", phase="failed", active_workers=0)
            continue
        _submit_memory_import(job.id, reset_for_recovery=True)


def _memory_import_recovery_loop(
    stop: threading.Event,
    scan_seconds: float | None = None,
) -> None:
    interval = max(0.01, float(scan_seconds or MEMORY_IMPORT_RECOVERY_SCAN_SECONDS))
    while not stop.wait(interval):
        _resume_memory_imports()


@app.on_event("startup")
def resume_memory_imports_on_startup() -> None:
    _reconcile_import_workspace_accounting()
    _resume_memory_imports()
    existing = getattr(app.state, "memory_import_recovery_thread", None)
    if existing is not None and existing.is_alive():
        return
    stop = threading.Event()
    thread = threading.Thread(
        target=_memory_import_recovery_loop,
        args=(stop,),
        name="memory-import-recovery",
        daemon=True,
    )
    app.state.memory_import_recovery_stop = stop
    app.state.memory_import_recovery_thread = thread
    thread.start()


@app.on_event("shutdown")
def stop_memory_import_recovery_on_shutdown() -> None:
    stop = getattr(app.state, "memory_import_recovery_stop", None)
    thread = getattr(app.state, "memory_import_recovery_thread", None)
    if stop is not None:
        stop.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)


@app.post("/memory-imports", status_code=202, summary="Queue a chat history import")
async def create_memory_import(
    request: Request,
    files: list[UploadFile] = File(...),
    options: str = Form(...),
    _auth=Depends(require_project_write),
):
    if not files:
        raise HTTPException(status_code=400, detail="Select at least one file to import.")
    try:
        upload_options = MemoryImportUploadOptions.model_validate(json.loads(options))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Import options must be valid JSON.") from exc
    except PydanticValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc

    project_id = get_project_id(request)
    _enforce_memory_storage_quota(request, _auth)
    storage_quota_snapshot = _capture_import_storage_quota_snapshot(request, _auth)
    job_id = str(uuid.uuid4())
    upload_lease_owner = str(uuid.uuid4())
    workspace = MEMORY_IMPORT_STORAGE_ROOT / job_id
    upload_root = workspace / "uploads"
    upload_job_created = False
    try:
        import_options = upload_options.to_import_options()
        job = import_jobs.create(
            project_id,
            [],
            import_options,
            job_id=job_id,
            workspace=str(workspace),
            status="uploading",
            lease_owner=upload_lease_owner,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=MEMORY_IMPORT_LEASE_SECONDS),
            storage_quota_snapshot=storage_quota_snapshot,
            max_active_jobs=MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT,
            max_retained_workspace_bytes=MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES,
        )
        upload_job_created = True

        def reserve_bytes(size: int) -> None:
            import_repository.reserve_workspace_bytes(
                job.id,
                upload_lease_owner,
                size,
                max_retained_bytes=MEMORY_IMPORT_MAX_RETAINED_WORKSPACE_BYTES,
                lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
            )

        _saved_files, input_names, ignored = await _save_import_uploads(
            files,
            upload_root,
            reserve_bytes=reserve_bytes,
        )
        if not input_names:
            raise HTTPException(
                status_code=400,
                detail="No supported files were selected. Use Markdown, text, JSON, JSONL, ZIP, or TAR files.",
            )
        job = import_jobs.update(
            job.id,
            input_files=input_names,
            total_input_files=len(input_names),
            skipped_files=ignored,
            status="queued",
            phase="queued",
            lease_owner=upload_lease_owner,
        )
        if job is None:
            raise ImportLeaseLost(job_id, upload_lease_owner)
        if not import_repository.release_job_lease(job.id, upload_lease_owner):
            raise ImportLeaseLost(job.id, upload_lease_owner)
        upload_job_created = False
        _submit_memory_import(job.id)
        return import_jobs.serialize(job)
    except ImportActiveJobLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "memory_import_active_job_limit",
                "project_id": exc.project_id,
                "limit": exc.limit,
                "active_jobs": exc.active_jobs,
            },
        ) from exc
    except ImportWorkspaceBudgetExceeded as exc:
        if upload_job_created:
            _abort_import_upload(job_id, upload_lease_owner)
        raise HTTPException(
            status_code=507,
            detail={
                "code": "memory_import_workspace_budget",
                "limit_bytes": exc.limit_bytes,
                "used_bytes": exc.used_bytes,
                "requested_bytes": exc.requested_bytes,
            },
        ) from exc
    except ImportLeaseLost as exc:
        if upload_job_created:
            _abort_import_upload(job_id, upload_lease_owner)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "memory_import_upload_lease_lost",
                "message": "The import upload reservation expired; submit the upload again.",
            },
        ) from exc
    except Exception:
        if upload_job_created:
            _abort_import_upload(job_id, upload_lease_owner)
        raise


@app.get("/memory-imports", summary="List recent chat history imports")
def list_memory_imports(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    _auth=Depends(require_project_read),
):
    jobs = import_jobs.list(get_project_id(request), limit)
    return {"results": [import_jobs.serialize(job) for job in jobs], "total": len(jobs)}


@app.get("/memory-imports/{job_id}", summary="Get chat history import progress")
def get_memory_import(
    request: Request,
    job_id: str,
    _auth=Depends(require_project_read),
):
    job = import_jobs.get(job_id, get_project_id(request), refresh=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    return import_jobs.serialize(job)


@app.post("/memory-imports/{job_id}/cancel", summary="Cancel a chat history import")
def cancel_memory_import(
    request: Request,
    job_id: str,
    _auth=Depends(require_project_write),
):
    job = import_jobs.request_cancel(job_id, get_project_id(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    return import_jobs.serialize(job)


@app.post("/memory-imports/{job_id}/discard", summary="Discard retained chat import source files")
def discard_memory_import_workspace(
    request: Request,
    job_id: str,
    _auth=Depends(require_project_write),
):
    project_id = get_project_id(request)
    job = import_jobs.get(job_id, project_id, refresh=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    if job.status not in {"cancelled", "completed", "completed_with_errors", "failed"}:
        raise HTTPException(status_code=409, detail="Only a terminal memory import can discard retained files.")
    if not job.workspace:
        if not job.source_retry_required and job.workspace_bytes == 0:
            return import_jobs.serialize(job)
        raise HTTPException(status_code=409, detail="The retained memory import workspace is unavailable.")

    lease_owner = str(uuid.uuid4())
    claimed = import_repository.acquire_job_workspace_discard_lease(
        job_id,
        project_id,
        lease_owner,
        lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
    )
    if claimed is None:
        current = import_jobs.get(job_id, project_id, refresh=True)
        if current is not None and not current.workspace and not current.source_retry_required:
            return import_jobs.serialize(current)
        raise HTTPException(
            status_code=409,
            detail="Memory import files are already being discarded or the import was retried.",
        )

    claimed_job = import_jobs.get(job_id, project_id, refresh=True)
    if claimed_job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    try:
        _delete_import_workspace(claimed_job)
    except Exception as exc:
        _record_workspace_cleanup_failure(claimed_job, exc, lease_owner=lease_owner)
        try:
            import_repository.abort_job_workspace_discard(job_id, lease_owner)
        except ImportLeaseLost:
            pass
        import_jobs.get(job_id, project_id, refresh=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "memory_import_workspace_discard_failed",
                "message": "The retained import files could not be deleted.",
            },
        ) from exc

    try:
        import_repository.complete_job_workspace_discard(job_id, lease_owner)
    except ImportLeaseLost as exc:
        current = import_jobs.get(job_id, project_id, refresh=True)
        if current is not None and not current.workspace and not current.source_retry_required:
            return import_jobs.serialize(current)
        raise HTTPException(status_code=409, detail="Memory import discard ownership was lost.") from exc

    discarded = import_jobs.get(job_id, project_id, refresh=True)
    if discarded is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    return import_jobs.serialize(discarded)


@app.get("/memory-imports/{job_id}/errors", summary="List memory import errors")
def list_memory_import_errors(
    request: Request,
    job_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _auth=Depends(require_project_read),
):
    job = import_jobs.get(job_id, get_project_id(request), refresh=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    rows = import_repository.list_errors(job_id, limit=limit, offset=offset)
    return {
        "results": [
            {
                "id": row.id,
                "source": row.source,
                "message": row.message,
                "phase": row.phase,
                "type": row.error_type,
                "code": row.error_code,
                "attempt": row.attempt,
                "retryable": row.retryable,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
        "total": job.error_count,
    }


@app.post("/memory-imports/{job_id}/retry", summary="Retry failed memory import chunks")
def retry_memory_import(
    request: Request,
    job_id: str,
    _auth=Depends(require_project_write),
):
    project_id = get_project_id(request)
    job = import_jobs.get(job_id, project_id, refresh=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    if job.status not in {"cancelled", "completed_with_errors", "failed"}:
        raise HTTPException(status_code=409, detail="Memory import is not ready for retry.")
    failed_chunks = import_repository.list_chunks(job_id, statuses="failed")
    if not job.source_retry_required:
        if job.graph_status == "failed":
            raise HTTPException(
                status_code=409,
                detail="Only graph synchronization failed; use the graph-retry endpoint.",
            )
        raise HTTPException(status_code=409, detail="No source chunks require retry.")
    try:
        _import_job_paths(job)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    lease_owner = str(uuid.uuid4())
    try:
        retry_record = import_repository.acquire_job_retry_lease(
            job_id,
            project_id,
            lease_owner,
            lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
            max_active_jobs=MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT,
        )
    except ImportActiveJobLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "memory_import_active_job_limit",
                "project_id": exc.project_id,
                "limit": exc.limit,
                "active_jobs": exc.active_jobs,
            },
        ) from exc
    if retry_record is None:
        current = import_jobs.get(job_id, project_id, refresh=True)
        if current is None:
            raise HTTPException(status_code=404, detail="Memory import not found.")
        raise HTTPException(status_code=409, detail="Memory import retry is already queued or running.")

    try:
        import_jobs.get(job_id, project_id, refresh=True)
        for chunk in failed_chunks:
            import_repository.update_chunk(
                job_id,
                chunk.import_key,
                lease_owner=lease_owner,
                status="pending",
                error_type=None,
                error_message=None,
                finished_at=None,
            )
        _recompute_import_progress(job_id, lease_owner)
        job = import_jobs.get(job_id, project_id, refresh=True)
        _start_memory_import_thread(job_id, lease_owner)
    except ImportLeaseLost as exc:
        import_repository.release_job_lease(job_id, lease_owner)
        import_jobs.get(job_id, project_id, refresh=True)
        raise HTTPException(status_code=409, detail="Memory import retry lease was lost.") from exc
    except Exception:
        try:
            import_jobs.update(
                job_id,
                status="failed",
                phase="failed",
                active_workers=0,
                lease_owner=lease_owner,
            )
        except ImportLeaseLost:
            pass
        finally:
            import_repository.release_job_lease(job_id, lease_owner)
            import_jobs.get(job_id, project_id, refresh=True)
        raise
    return import_jobs.serialize(job)


def _run_graph_retry(job_id: str, lease_owner: str) -> None:
    lease_stop = threading.Event()
    lease_lost = threading.Event()
    graph_phase_recorded = False
    lease_thread = threading.Thread(
        target=_renew_memory_import_lease,
        args=(job_id, lease_owner, lease_stop, lease_lost),
        name=f"memory-import-graph-lease-{job_id[:8]}",
        daemon=True,
    )
    graph_started = time.perf_counter()

    def record_graph_phase() -> None:
        nonlocal graph_phase_recorded
        if graph_phase_recorded:
            return
        if lease_lost.is_set():
            raise ImportLeaseLost(job_id, lease_owner)
        import_jobs.record_phase(
            job_id,
            "neo4j",
            time.perf_counter() - graph_started,
            lease_owner=lease_owner,
        )
        graph_phase_recorded = True

    lease_thread.start()
    try:
        import_jobs.increment(job_id, graph_attempts=1, lease_owner=lease_owner)
        result = _sync_import_graph(
            job_id,
            include_failed=True,
            lease_owner=lease_owner,
        )
        if lease_lost.is_set():
            raise ImportLeaseLost(job_id, lease_owner)
        import_repository.assert_job_lease(job_id, lease_owner)
        job = import_jobs.get(job_id, refresh=True)
        status = "completed_with_errors" if job and job.failed_chunks else "completed"
        _prepare_terminal_import_workspace(
            job_id,
            bool(job and job.source_retry_required),
            lease_owner=lease_owner,
        )
        record_graph_phase()
        import_jobs.update(
            job_id,
            status=status,
            phase="completed",
            graph_status=result,
            graph_error=None,
            lease_owner=lease_owner,
        )
    except ImportLeaseLost:
        lease_lost.set()
    except Exception as exc:
        if not lease_lost.is_set():
            try:
                import_repository.assert_job_lease(job_id, lease_owner)
                job = import_jobs.get(job_id, refresh=True)
                _prepare_terminal_import_workspace(
                    job_id,
                    bool(job and job.source_retry_required),
                    lease_owner=lease_owner,
                )
                safe_message = _record_import_exception(
                    job_id,
                    "graph_sync",
                    exc,
                    error_type="graph_sync_error",
                    retryable=True,
                    operation_phase="graph_sync",
                    lease_owner=lease_owner,
                )
                record_graph_phase()
                import_jobs.update(
                    job_id,
                    status="completed_with_errors",
                    phase="completed",
                    graph_status="failed",
                    graph_error=safe_message,
                    lease_owner=lease_owner,
                )
            except ImportLeaseLost:
                lease_lost.set()
            except Exception:
                logging.warning("Memory import %s graph retry finalization failed", job_id, exc_info=True)
    finally:
        if not lease_lost.is_set() and not graph_phase_recorded:
            try:
                record_graph_phase()
            except ImportLeaseLost:
                lease_lost.set()
            except Exception:
                logging.warning("Memory import %s graph-retry metrics failed", job_id, exc_info=True)
        lease_stop.set()
        lease_thread.join(timeout=MEMORY_IMPORT_LEASE_RENEW_SECONDS + 1)
        try:
            import_repository.release_job_lease(job_id, lease_owner)
        except Exception:
            logging.warning("Memory import %s graph-retry lease release failed", job_id, exc_info=True)


@app.post("/memory-imports/{job_id}/graph-retry", summary="Retry memory import graph sync")
def retry_memory_import_graph(
    request: Request,
    job_id: str,
    _auth=Depends(require_project_write),
):
    job = import_jobs.get(job_id, get_project_id(request), refresh=True)
    if job is None:
        raise HTTPException(status_code=404, detail="Memory import not found.")
    if job.status not in {"completed", "completed_with_errors"}:
        raise HTTPException(status_code=409, detail="Memory import graph sync is not ready for retry.")
    if job.graph_status not in {"failed", "pending", "disabled"}:
        raise HTTPException(status_code=409, detail="Graph sync is not ready for retry.")
    lease_owner = str(uuid.uuid4())
    try:
        activated = import_repository.activate_graph_retry(
            job_id,
            job.project_id,
            lease_owner,
            lease_seconds=MEMORY_IMPORT_LEASE_SECONDS,
            max_active_jobs=MEMORY_IMPORT_MAX_ACTIVE_JOBS_PER_PROJECT,
        )
    except ImportActiveJobLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "memory_import_active_job_limit",
                "project_id": exc.project_id,
                "limit": exc.limit,
                "active_jobs": exc.active_jobs,
            },
        ) from exc
    if activated is None:
        raise HTTPException(status_code=409, detail="Graph sync is not ready for retry.")
    import_jobs.get(job_id, job.project_id, refresh=True)
    _start_graph_retry_thread(job_id, lease_owner)
    return import_jobs.serialize(import_jobs.get(job_id))


ALL_MEMORIES_LIMIT = 1000
_RESERVED_PAYLOAD_KEYS = {
    "data",
    "user_id",
    "agent_id",
    "app_id",
    "run_id",
    "hash",
    "project_id",
    "created_at",
    "updated_at",
    "expiration_date",
    "categories",
    "category",
}


def _serialize_memory(row: Any) -> Dict[str, Any]:
    payload = getattr(row, "payload", None) or {}
    return {
        "id": getattr(row, "id", None),
        "memory": payload.get("data"),
        "project_id": payload.get("project_id") or DEFAULT_PROJECT_ID,
        "user_id": payload.get("user_id"),
        "agent_id": payload.get("agent_id"),
        "app_id": payload.get("app_id"),
        "run_id": payload.get("run_id"),
        "hash": payload.get("hash"),
        "expiration_date": payload.get("expiration_date"),
        "categories": _normalize_memory_categories(payload.get("categories") or payload.get("category")),
        "metadata": {k: v for k, v in payload.items() if k not in _RESERVED_PAYLOAD_KEYS},
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
    }


def _payload_project_id(payload: Dict[str, Any]) -> str:
    return str(payload.get("project_id") or DEFAULT_PROJECT_ID)


def _list_all_memories(project_id: str, limit: int = ALL_MEMORIES_LIMIT) -> Dict[str, Any]:
    results = get_memory_instance().vector_store.list(top_k=limit)
    rows = results[0] if results and isinstance(results, list) and isinstance(results[0], list) else results or []
    return {
        "results": [
            _serialize_memory(row)
            for row in rows
            if _payload_project_id(getattr(row, "payload", None) or {}) == project_id
        ]
    }


def _memory_project_id(memory: Any) -> str:
    if isinstance(memory, dict):
        metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
        return str(memory.get("project_id") or metadata.get("project_id") or DEFAULT_PROJECT_ID)
    return DEFAULT_PROJECT_ID


def _ensure_memory_project(memory_id: str, project_id: str) -> None:
    try:
        uuid.UUID(memory_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Memory not found.")
    try:
        memory = get_memory_instance().get(memory_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Memory not found.")
    if not memory or _memory_project_id(memory) != project_id:
        raise HTTPException(status_code=404, detail="Memory not found.")


@app.get("/memories", summary="Get memories")
def get_all_memories(
    request: Request,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    top_k: Optional[int] = Query(None, ge=0, le=ALL_MEMORIES_LIMIT),
    show_expired: bool = Query(False),
    _auth=Depends(require_project_read),
):
    """Retrieve stored memories scoped to the current project."""
    project_id = get_project_id(request)
    log_payload = {
        key: value
        for key, value in {
            "user_id": user_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "app_id": app_id,
            "top_k": top_k if isinstance(top_k, int) else None,
            "show_expired": show_expired,
        }.items()
        if value is not None
    }
    _set_request_log_context(request, "GET_ALL", log_payload)
    try:
        if not any([user_id, run_id, agent_id, app_id]):
            # require_project_read has already authorized access; the raw vector listing is filtered by project_id.
            response = _list_all_memories(project_id, limit=top_k if top_k is not None else ALL_MEMORIES_LIMIT)
            _set_request_log_context(request, "GET_ALL", log_payload, response)
            return response
        filters = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id, "app_id": app_id}.items() if v
        }
        filters["project_id"] = project_id
        params = {"filters": filters}
        if top_k is not None:
            params["top_k"] = top_k
        params["show_expired"] = show_expired
        response = get_memory_instance().get_all(**params)
        _set_request_log_context(request, "GET_ALL", log_payload, response)
        return response
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


@app.get("/memories/{memory_id}", summary="Get a memory")
def get_memory(request: Request, memory_id: str, _auth=Depends(require_project_read)):
    """Retrieve a specific memory by ID."""
    try:
        response = get_memory_instance().get(memory_id)
        if _memory_project_id(response) != get_project_id(request):
            raise HTTPException(status_code=404, detail="Memory not found.")
        return response
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


@app.post("/search", summary="Search memories")
def search_memories(request: Request, search_req: SearchRequest, _auth=Depends(require_project_read)):
    """Search for memories based on a query."""
    _set_request_log_context(request, "SEARCH", search_req.model_dump(mode="json", exclude_none=True))
    try:
        filters = search_req.filters or {}
        # ponytail: flat project filter; add nested filter merging when the memory core exposes a shared parser.
        filters["project_id"] = get_project_id(request)
        deprecated_keys = []
        deprecated_values = search_req.model_dump(
            include={"user_id", "agent_id", "app_id", "run_id"},
            exclude_none=True,
        )
        for entity_key, entity_val in deprecated_values.items():
            if entity_val:
                filters[entity_key] = entity_val
                deprecated_keys.append(entity_key)
        if deprecated_keys:
            logging.warning(
                "Top-level %s in /search is deprecated. Use filters={%s} instead.",
                ", ".join(deprecated_keys),
                ", ".join(f'"{k}": "..."' for k in deprecated_keys),
            )
        params = {}
        project_id = get_project_id(request)
        top_k = search_req.top_k if search_req.top_k is not None else 10
        params["top_k"] = top_k
        if search_req.threshold is not None:
            params["threshold"] = search_req.threshold
        if search_req.explain is not None:
            params["explain"] = search_req.explain
        if search_req.rerank is not None:
            params["rerank"] = search_req.rerank
        if search_req.show_expired is not None:
            params["show_expired"] = search_req.show_expired
        response = get_memory_instance().search(query=search_req.query, filters=filters, **params)
        response = _add_graph_results(response, search_req.query, project_id, filters, top_k, bool(search_req.explain))
        _set_request_log_context(request, "SEARCH", search_req.model_dump(mode="json", exclude_none=True), response)
        return response
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


@app.put("/memories/{memory_id}", summary="Update a memory")
def update_memory(request: Request, memory_id: str, updated_memory: MemoryUpdate, _auth=Depends(require_project_write)):
    """Update an existing memory."""
    try:
        fields_set = updated_memory.model_fields_set
        project_id = get_project_id(request)
        _ensure_memory_project(memory_id, project_id)
        params = {"memory_id": memory_id}
        if "text" in fields_set:
            params["data"] = updated_memory.text
        metadata_patch: dict[str, Any] | None = None
        if "metadata" in fields_set:
            metadata_patch = dict(updated_memory.metadata or {})
        if "categories" in fields_set:
            settings = project_settings(_workspace_settings(), project_id)
            categories = _normalize_memory_categories(updated_memory.categories, _category_names(settings) or None)
            metadata_patch = metadata_patch or {}
            metadata_patch["categories"] = categories
            metadata_patch.pop("category", None)
        elif metadata_patch is not None and ("category" in metadata_patch or "categories" in metadata_patch):
            settings = project_settings(_workspace_settings(), project_id)
            categories = _request_categories(None, metadata_patch, _category_names(settings))
            metadata_patch.pop("category", None)
            metadata_patch.pop("categories", None)
            if categories:
                metadata_patch["categories"] = categories
        if metadata_patch is not None:
            metadata_patch["project_id"] = project_id
            params["metadata"] = metadata_patch
        if "expiration_date" in fields_set:
            params["expiration_date"] = updated_memory.expiration_date
        response = get_memory_instance().update(**params)
        if "text" in fields_set or "metadata" in fields_set or "categories" in fields_set:
            metadata = params.get("metadata")
            memory_text = updated_memory.text
            if memory_text is None:
                try:
                    current = get_memory_instance().get(memory_id)
                    memory_text = current.get("memory") or current.get("data")
                    metadata = _memory_metadata_for_graph(current, metadata)
                except Exception:
                    pass
            upsert_graph_memory(memory_id, memory_text, {}, metadata or {"project_id": project_id})
        if ("categories" in fields_set or "metadata" in fields_set) and params.get("metadata"):
            metadata = params["metadata"]
            if "categories" in metadata:
                queue_webhook_event(
                    "memory.categorized",
                    {"memory_id": memory_id, "metadata": metadata},
                    project_id,
                )
        queue_webhook_event("memory.updated", {"memory_id": memory_id, "result": response}, project_id)
        return response
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except (ValueError, MemoryValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@app.get("/memories/{memory_id}/history", summary="Get memory history")
def memory_history(request: Request, memory_id: str, _auth=Depends(require_project_read)):
    """Retrieve memory history."""
    try:
        _ensure_memory_project(memory_id, get_project_id(request))
        return get_memory_instance().history(memory_id=memory_id)
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except (ValueError, MemoryValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@app.delete("/memories/{memory_id}", summary="Delete a memory", response_model=MessageResponse)
def delete_memory(request: Request, memory_id: str, _auth=Depends(require_project_write)):
    """Delete a specific memory by ID."""
    try:
        _ensure_memory_project(memory_id, get_project_id(request))
        get_memory_instance().delete(memory_id=memory_id)
        delete_graph_memory(memory_id)
        memories_router.delete_memory_feedback(get_project_id(request), memory_id)
        queue_webhook_event("memory.deleted", {"memory_id": memory_id}, get_project_id(request))
        return MessageResponse(message="Memory deleted successfully")
    except HTTPException:
        raise
    except ProviderConfigurationRequiredError:
        raise
    except (ValueError, MemoryValidationError) as e:
        raise _client_error(e)
    except Exception:
        raise upstream_error()


@app.delete("/memories", summary="Delete all memories", response_model=MessageResponse)
def delete_all_memories(
    request: Request,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    _auth=Depends(require_project_write),
):
    """Delete all memories for a given identifier. Requires admin role."""
    if not any([user_id, run_id, agent_id, app_id]):
        raise HTTPException(status_code=400, detail="At least one identifier is required.")
    try:
        params = {
            k: v for k, v in {"user_id": user_id, "run_id": run_id, "agent_id": agent_id, "app_id": app_id}.items() if v
        }
        params["project_id"] = get_project_id(request)
        get_memory_instance().delete_all(**params)
        delete_graph_memories(get_project_id(request), params)
        return MessageResponse(message="All relevant memories deleted")
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


@app.post("/reset", summary="Reset all memories")
def reset_memory(_auth=Depends(require_admin)):
    """Completely reset stored memories. Requires admin role."""
    try:
        get_memory_instance().reset()
        return {"message": "All memories reset"}
    except ProviderConfigurationRequiredError:
        raise
    except Exception:
        raise upstream_error()


def _merge_entity_filters(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    filters = data.get("filters")
    if isinstance(filters, dict):
        for key in ("user_id", "agent_id", "app_id", "run_id"):
            if key not in data and filters.get(key):
                data[key] = filters[key]
    return data


def _memory_create_from_platform_payload(payload: dict[str, Any]) -> MemoryCreate:
    data = _merge_entity_filters(payload)
    if "messages" not in data and data.get("message"):
        data["messages"] = [data["message"]]
    return MemoryCreate.model_validate(data)


def _memory_update_from_platform_payload(payload: dict[str, Any]) -> MemoryUpdate:
    data = dict(payload)
    if "text" not in data and "data" in data:
        data["text"] = data["data"]
    return MemoryUpdate.model_validate(data)


@app.get("/v1/ping/", summary="Platform-compatible API key validation")
def platform_ping(request: Request, user: User | None = Depends(verify_auth)):
    project_id = get_project_id(request)
    project = find_project(_workspace_settings(), project_id) or {}
    return {
        "status": "ok",
        "org_id": project.get("organization_id") or DEFAULT_ORG_ID,
        "project_id": project_id,
        "user_email": user.email if user else None,
    }


@app.post("/v3/memories/add/", summary="Platform-compatible create memories")
def platform_add_memory(request: Request, payload: dict[str, Any], _auth=Depends(require_project_write)):
    return add_memory(request, _memory_create_from_platform_payload(payload), _auth)


@app.post("/v3/memories/", summary="Platform-compatible get memories")
def platform_get_memories(request: Request, payload: dict[str, Any], _auth=Depends(require_project_read)):
    data = _merge_entity_filters(payload)
    filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    return get_all_memories(
        request,
        user_id=data.get("user_id") or filters.get("user_id"),
        run_id=data.get("run_id") or filters.get("run_id"),
        agent_id=data.get("agent_id") or filters.get("agent_id"),
        app_id=data.get("app_id") or filters.get("app_id"),
        top_k=data.get("top_k") or data.get("page_size"),
        show_expired=bool(data.get("show_expired", False)),
        _auth=_auth,
    )


@app.post("/v3/memories/search/", summary="Platform-compatible search memories")
def platform_search_memories(request: Request, payload: dict[str, Any], _auth=Depends(require_project_read)):
    return search_memories(request, SearchRequest.model_validate(payload), _auth)


@app.get("/v1/memories/{memory_id}/", summary="Platform-compatible get memory")
def platform_get_memory(request: Request, memory_id: str, _auth=Depends(require_project_read)):
    return get_memory(request, memory_id, _auth)


@app.put("/v1/memories/{memory_id}/", summary="Platform-compatible update memory")
def platform_update_memory(
    request: Request,
    memory_id: str,
    payload: dict[str, Any],
    _auth=Depends(require_project_write),
):
    return update_memory(request, memory_id, _memory_update_from_platform_payload(payload), _auth)


@app.delete("/v1/memories/{memory_id}/", summary="Platform-compatible delete memory")
def platform_delete_memory(request: Request, memory_id: str, _auth=Depends(require_project_write)):
    return delete_memory(request, memory_id, _auth)


@app.delete("/v1/memories/", summary="Platform-compatible delete memories")
def platform_delete_memories(
    request: Request,
    user_id: Optional[str] = None,
    run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    app_id: Optional[str] = None,
    _auth=Depends(require_project_write),
):
    return delete_all_memories(request, user_id, run_id, agent_id, app_id, _auth)


@app.get("/v1/memories/{memory_id}/history/", summary="Platform-compatible memory history")
def platform_memory_history(request: Request, memory_id: str, _auth=Depends(require_project_read)):
    return memory_history(request, memory_id, _auth)


@app.get("/v1/entities/", summary="Platform-compatible list entities")
def platform_list_entities(request: Request, _auth=Depends(require_project_read)):
    return entities_router.list_entities(request, _auth)


@app.delete("/v2/entities/{entity_type}/{entity_id}/", summary="Platform-compatible delete entity")
def platform_delete_entity(request: Request, entity_type: str, entity_id: str, _auth=Depends(require_project_write)):
    if entity_type not in {"user", "agent", "app", "run"}:
        raise HTTPException(status_code=400, detail="entity_type must be user, agent, app, or run.")
    return entities_router.delete_entity(request, entity_type, entity_id, _auth)


@app.post("/v1/exports/", summary="Platform-compatible create memory export")
def platform_create_memory_export(
    request: Request,
    payload: dict[str, Any],
    _auth=Depends(require_project_write),
    db=Depends(lambda: SessionLocal()),
):
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    for key in ("user_id", "agent_id", "app_id", "run_id"):
        if payload.get(key):
            filters[key] = payload[key]
    body = exports_router.ExportCreate(
        filters=filters,
        date_range=payload.get("date_range"),
        pydantic_schema=payload.get("schema") or payload.get("pydantic_schema"),
    )
    try:
        return exports_router.create_export(body, request, _auth, db)
    finally:
        db.close()


@app.post("/v1/exports/get/", summary="Platform-compatible get memory export")
def platform_get_memory_export(
    request: Request,
    payload: dict[str, Any],
    _auth=Depends(require_project_read),
    db=Depends(lambda: SessionLocal()),
):
    try:
        export_id = payload.get("export_id") or payload.get("id")
        if export_id:
            return exports_router.get_export(str(export_id), request, _auth, db)
        jobs = exports_router.list_exports(request, _auth, db)
        if not jobs:
            raise HTTPException(status_code=404, detail="Memory export not found.")
        return exports_router.get_export(jobs[0]["id"], request, _auth, db)
    finally:
        db.close()


@app.post("/v1/feedback/", summary="Platform-compatible memory feedback")
def platform_feedback(payload: dict[str, Any], _auth=Depends(require_project_write)):
    if not payload.get("memory_id"):
        raise HTTPException(status_code=400, detail="memory_id is required.")
    return {"message": "Feedback recorded"}


@app.get("/", summary="Redirect to the OpenAPI documentation", include_in_schema=False)
def home():
    """Redirect to the OpenAPI documentation."""
    return RedirectResponse(url="/docs")
