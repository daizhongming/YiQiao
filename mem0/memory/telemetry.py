# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import atexit
import logging
import math
import os
import platform
import random
import sys
import threading

from posthog import Posthog

import mem0
from mem0.memory.setup import get_or_create_user_id

_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})


def _parse_bool(raw):
    """Parse an opt-in flag. Unknown values fail closed."""
    if isinstance(raw, bool):
        return raw
    return isinstance(raw, str) and raw.strip().lower() in _TRUE_VALUES


def _telemetry_requested():
    """Read the YiQiao flag first, then the legacy compatibility alias."""
    raw = os.environ.get("YIQIAO_TELEMETRY")
    if raw is None:
        raw = os.environ.get("MEM0_TELEMETRY", "false")
    return _parse_bool(raw)


# MEM0_TELEMETRY remains an internal compatibility name used by the memory
# core. YiQiao telemetry is opt-in and cannot start without an operator key.
PROJECT_API_KEY = os.environ.get("YIQIAO_POSTHOG_API_KEY", "").strip()
HOST = os.environ.get("YIQIAO_POSTHOG_HOST", "https://us.i.posthog.com").strip()
MEM0_TELEMETRY = _telemetry_requested() and bool(PROJECT_API_KEY)
FEATURE_FLAGS_REQUEST_TIMEOUT_SECONDS = 0.5

logging.getLogger("posthog").setLevel(logging.CRITICAL + 1)
logging.getLogger("urllib3").setLevel(logging.CRITICAL + 1)
_logger = logging.getLogger(__name__)

_DEFAULT_SAMPLE_RATE = 0.1


def _parse_sample_rate(raw):
    """Parse a sampling rate without allowing invalid input to break startup."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _logger.debug("Telemetry sample rate %r is invalid; using %s", raw, _DEFAULT_SAMPLE_RATE)
        return _DEFAULT_SAMPLE_RATE
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        _logger.debug("Telemetry sample rate %r is out of range; using %s", raw, _DEFAULT_SAMPLE_RATE)
        return _DEFAULT_SAMPLE_RATE
    return value


_sample_rate_raw = os.environ.get(
    "YIQIAO_TELEMETRY_SAMPLE_RATE",
    os.environ.get("MEM0_TELEMETRY_SAMPLE_RATE", str(_DEFAULT_SAMPLE_RATE)),
)
MEM0_TELEMETRY_SAMPLE_RATE = _parse_sample_rate(_sample_rate_raw)

_LIFECYCLE_EVENTS = frozenset({"mem0.init", "mem0.reset", "mem0._create_procedural_memory", "mem0.notice_displayed"})
_SAFE_OPERATION_FIELDS = frozenset({"advanced_filters", "explain", "limit", "sync_type", "threshold", "version"})


def _sampling_before_send(msg):
    """Drop sampled hot-path events and annotate retained events."""
    if not isinstance(msg, dict):
        return None

    event_name = msg.get("event", "")
    is_lifecycle = event_name in _LIFECYCLE_EVENTS
    if not is_lifecycle and random.random() >= MEM0_TELEMETRY_SAMPLE_RATE:
        return None

    properties = msg.setdefault("properties", {})
    properties["sample_rate"] = 1.0 if is_lifecycle else MEM0_TELEMETRY_SAMPLE_RATE
    return msg


class AnonymousTelemetry:
    def __init__(self, vector_store=None, before_send=None):
        if not MEM0_TELEMETRY or not PROJECT_API_KEY:
            self.posthog = None
            self.user_id = None
            return

        self.posthog = Posthog(
            project_api_key=PROJECT_API_KEY,
            host=HOST,
            before_send=before_send,
            disable_geoip=True,
            feature_flags_request_timeout_seconds=FEATURE_FLAGS_REQUEST_TIMEOUT_SECONDS,
        )
        self.user_id = get_or_create_user_id(vector_store)

    def capture_event(self, event_name, properties=None, flags=None):
        if self.posthog is None or self.user_id is None:
            return

        properties = {
            "client_source": "python",
            "client_version": mem0.__version__,
            "python_version": platform.python_version(),
            "os": sys.platform,
            **(properties or {}),
        }
        try:
            capture_kwargs = {"distinct_id": self.user_id, "properties": properties}
            if flags is not None:
                capture_kwargs["flags"] = flags
            self.posthog.capture(event_name, **capture_kwargs)
        except Exception as exc:
            _logger.debug("Failed to capture telemetry event %r: %s", event_name, exc)

    def close(self):
        if self.posthog is not None:
            self.posthog.shutdown()
            self.posthog = None


_oss_telemetry_instance = None
_oss_telemetry_lock = threading.Lock()
_oss_telemetry_shutting_down = False


def _get_oss_telemetry():
    """Return the process-wide telemetry singleton, creating it lazily."""
    global _oss_telemetry_instance
    if _oss_telemetry_shutting_down:
        return None
    if _oss_telemetry_instance is not None:
        return _oss_telemetry_instance

    with _oss_telemetry_lock:
        if _oss_telemetry_shutting_down:
            return None
        if _oss_telemetry_instance is not None:
            return _oss_telemetry_instance
        _oss_telemetry_instance = AnonymousTelemetry(before_send=_sampling_before_send)
        atexit.register(_shutdown_oss_telemetry)
        return _oss_telemetry_instance


def _shutdown_oss_telemetry():
    global _oss_telemetry_instance, _oss_telemetry_shutting_down
    with _oss_telemetry_lock:
        _oss_telemetry_shutting_down = True
        if _oss_telemetry_instance is not None:
            _oss_telemetry_instance.close()
            _oss_telemetry_instance = None


def capture_event(event_name, memory_instance, additional_data=None):
    """Capture an anonymous self-hosted event without affecting core behavior."""
    if not MEM0_TELEMETRY:
        return

    try:
        telemetry = _get_oss_telemetry()
        if telemetry is None:
            return

        event_data = {
            "vector_size": memory_instance.embedding_model.config.embedding_dims,
            "history_store": "sqlite",
            "vector_store": (
                f"{memory_instance.vector_store.__class__.__module__}.{memory_instance.vector_store.__class__.__name__}"
            ),
            "llm": f"{memory_instance.llm.__class__.__module__}.{memory_instance.llm.__class__.__name__}",
            "embedding_model": (
                f"{memory_instance.embedding_model.__class__.__module__}."
                f"{memory_instance.embedding_model.__class__.__name__}"
            ),
            "function": (
                f"{memory_instance.__class__.__module__}.{memory_instance.__class__.__name__}."
                f"{memory_instance.api_version}"
            ),
        }
        if additional_data:
            event_data.update({key: value for key, value in additional_data.items() if key in _SAFE_OPERATION_FIELDS})

        telemetry.capture_event(event_name, event_data)
    except Exception as exc:
        _logger.debug("Failed to capture self-hosted telemetry event %r: %s", event_name, exc)
