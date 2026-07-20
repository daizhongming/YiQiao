import hashlib
import hmac
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from urllib import error, request

from db import SessionLocal
from models import Webhook
from sqlalchemy import select

TIMEOUT_SECONDS = 5


def _loads_events(value: str) -> list[str]:
    try:
        events = json.loads(value)
    except json.JSONDecodeError:
        return []
    return events if isinstance(events, list) else []


def _delivery_key(value: str | None = None) -> str:
    key = str(value or uuid.uuid4()).strip()
    if not key or len(key) > 255 or any(character in key for character in "\r\n"):
        raise ValueError("Webhook delivery key must be 1-255 characters without line breaks.")
    return key


def queue_webhook_event(
    event_type: str,
    data: dict,
    project_id: str | None = None,
    *,
    delivery_key: str | None = None,
) -> str:
    resolved_key = _delivery_key(delivery_key)
    threading.Thread(
        target=_deliver_webhooks,
        args=(event_type, data, project_id, resolved_key),
        daemon=True,
    ).start()
    return resolved_key


def send_webhook(
    hook: Webhook,
    event_type: str,
    data: dict,
    delivery_key: str | None = None,
) -> str:
    resolved_key = _delivery_key(delivery_key)
    body = json.dumps(
        {
            "id": resolved_key,
            "type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        },
        separators=(",", ":"),
        default=str,
    ).encode()
    signature = hmac.new(hook.signing_secret.encode(), body, hashlib.sha256).hexdigest()
    req = request.Request(
        hook.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "YiQiao-Webhooks/1.0",
            "X-YiQiao-Event": event_type,
            "X-YiQiao-Delivery-Id": resolved_key,
            "X-YiQiao-Signature": f"sha256={signature}",
        },
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
            status = f"{res.status} {res.reason}"
    except error.HTTPError as exc:
        status = f"{exc.code} {exc.reason}"
    except Exception as exc:
        status = f"error: {type(exc).__name__}"
        logging.info("Webhook delivery failed for %s", hook.url, exc_info=exc)
    hook.last_delivery_status = status
    hook.last_delivery_at = datetime.now(timezone.utc)
    return status


def _deliver_webhooks(
    event_type: str,
    data: dict,
    project_id: str | None = None,
    delivery_key: str | None = None,
) -> None:
    try:
        resolved_key = _delivery_key(delivery_key)
        with SessionLocal() as db:
            stmt = select(Webhook).where(Webhook.enabled.is_(True))
            if project_id:
                stmt = stmt.where(Webhook.project_id == project_id)
            hooks = db.execute(stmt).scalars().all()
            for hook in hooks:
                if event_type not in _loads_events(hook.events):
                    continue
                send_webhook(hook, event_type, data, resolved_key)
            db.commit()
    except Exception:
        logging.info("Webhook dispatch skipped", exc_info=True)
