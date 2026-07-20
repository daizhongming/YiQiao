# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Optional anonymous telemetry.

Disabled by default. Set YIQIAO_TELEMETRY=true and YIQIAO_POSTHOG_API_KEY to
opt in. The local dashboard nudge only writes to the server log.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import mem0

PROJECT_API_KEY = os.environ.get("YIQIAO_POSTHOG_API_KEY", "")
HOST = os.environ.get("YIQIAO_POSTHOG_HOST", "https://us.i.posthog.com")

_TELEMETRY_FLAG = os.environ.get("YIQIAO_TELEMETRY", os.environ.get("MEM0_TELEMETRY", "false"))
ENABLED = _TELEMETRY_FLAG.lower() not in {"0", "false", "no", "off"}
STATE_PATH = Path(
    os.environ.get(
        "YIQIAO_TELEMETRY_STATE_PATH",
        os.environ.get("MEM0_TELEMETRY_STATE_PATH", "/app/history/telemetry.json"),
    )
)

_lock = Lock()
_client: Any = None
_dashboard_nudge_logged = False


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state))
    except OSError:
        logging.exception("telemetry: failed to persist state")


def _install_id(state: dict[str, Any]) -> str:
    install_id = state.get("install_id")
    if not install_id:
        install_id = str(uuid.uuid4())
        state["install_id"] = install_id
        _save_state(state)
    return install_id


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not PROJECT_API_KEY:
        logging.warning("telemetry: YIQIAO_POSTHOG_API_KEY is not configured; disabling")
        return None
    try:
        from posthog import Posthog
    except ImportError:
        logging.warning("telemetry: posthog package not installed; disabling")
        return None
    _client = Posthog(project_api_key=PROJECT_API_KEY, host=HOST, disable_geoip=True)
    return _client


def log_status() -> None:
    if ENABLED:
        logging.info("telemetry: anonymous telemetry is enabled. Set YIQIAO_TELEMETRY=false to disable.")


def _capture_once(event: str, state_key: str) -> None:
    if not ENABLED:
        return

    with _lock:
        state = _load_state()
        if state.get(state_key):
            return

        client = _get_client()
        if client is None:
            return

        try:
            client.capture(
                distinct_id=_install_id(state),
                event=event,
                properties={"server_version": mem0.__version__},
            )
            state[state_key] = datetime.now(timezone.utc).isoformat()
            _save_state(state)
        except Exception:
            logging.exception("telemetry: failed to send %s event", event)


def capture_admin_registered(email: str) -> None:
    # Keep the argument for call-site compatibility, but never transmit it.
    del email
    _capture_once("admin_registered", "admin_registered_sent_at")


def capture_onboarding_completed(email: str, use_case: str) -> None:
    # Both values can contain identifying or free-form operator data.
    del email, use_case
    _capture_once("onboarding_completed", "onboarding_sent_at")


def log_dashboard_nudge_once(dashboard_url: str) -> None:
    """Log a hint pointing the operator to the dashboard after the first stored memory."""
    global _dashboard_nudge_logged
    if _dashboard_nudge_logged:
        return
    _dashboard_nudge_logged = True
    logging.info(
        "First memory stored. Open the dashboard at %s to view and manage your memories.",
        dashboard_url,
    )
