import json
import secrets
import uuid
from datetime import datetime
from urllib.parse import urlparse

from auth import enforce_api_key_scope, require_project_write, verify_auth
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Request
from models import User, Webhook
from project_scope import get_project_id
from pydantic import BaseModel
from schemas import MessageResponse
from settings_store import get_json
from sqlalchemy import select
from sqlalchemy.orm import Session
from webhook_dispatcher import send_webhook
from workspace import (
    DEFAULT_WORKSPACE_SETTINGS,
    WORKSPACE_KEY,
    member_role,
    role_allows_write,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
compat_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

EVENTS = {"memory.added", "memory.updated", "memory.deleted", "memory.categorized"}


class WebhookCreate(BaseModel):
    name: str = "Webhook"
    url: str
    events: list[str]
    enabled: bool = True


class WebhookUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None


class WebhookResponse(BaseModel):
    id: uuid.UUID
    name: str
    project_id: str
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    last_delivery_status: str | None = None
    last_delivery_at: datetime | None = None
    signing_secret: str | None = None


def _validate_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Webhook URL must be http or https.")
    return value


def _validate_events(events: list[str]) -> list[str]:
    clean = sorted({*events, "memory.added"})
    invalid = [event for event in clean if event not in EVENTS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported webhook event: {invalid[0]}")
    return clean


def _to_response(hook: Webhook, signing_secret: str | None = None) -> WebhookResponse:
    return WebhookResponse(
        id=hook.id,
        name=hook.name,
        project_id=hook.project_id,
        url=hook.url,
        events=json.loads(hook.events),
        enabled=hook.enabled,
        created_at=hook.created_at,
        last_delivery_status=hook.last_delivery_status,
        last_delivery_at=hook.last_delivery_at,
        signing_secret=signing_secret,
    )


def _ensure_project_write(request: Request, project_id: str, user: User | None, db: Session) -> None:
    current_project_id = get_project_id(request)
    auth_type = getattr(request.state, "auth_type", "none")
    if auth_type in {"admin_api_key", "disabled"}:
        request.state.project_id = project_id
        return
    if auth_type == "api_key":
        enforce_api_key_scope(request, write=True)
        if current_project_id == project_id:
            request.state.project_id = project_id
            return
        raise HTTPException(status_code=403, detail="Project access denied.")
    if auth_type == "bearer" and user is not None and user.role == "admin":
        request.state.project_id = project_id
        return
    settings = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    role = member_role(settings, user.email if user else None, project_id)
    if not role_allows_write(role):
        raise HTTPException(status_code=403, detail="Project access denied.")
    request.state.project_id = project_id


@router.get("", response_model=list[WebhookResponse])
def list_webhooks(request: Request, _user: User | None = Depends(require_project_write), db: Session = Depends(get_db)):
    hooks = (
        db.execute(
            select(Webhook).where(Webhook.project_id == get_project_id(request)).order_by(Webhook.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_response(hook) for hook in hooks]


@router.post("", response_model=WebhookResponse, status_code=201)
def create_webhook(
    body: WebhookCreate,
    request: Request,
    user: User | None = Depends(require_project_write),
    db: Session = Depends(get_db),
):
    secret = f"whsec_{secrets.token_urlsafe(32)}"
    hook = Webhook(
        name=body.name.strip() or "Webhook",
        project_id=get_project_id(request),
        url=_validate_url(body.url),
        events=json.dumps(_validate_events(body.events)),
        signing_secret=secret,
        enabled=body.enabled,
        created_by=None if user is None or user.id.int == 0 else user.id,
    )
    db.add(hook)
    db.commit()
    db.refresh(hook)
    return _to_response(hook, signing_secret=secret)


@router.patch("/{hook_id}", response_model=WebhookResponse)
def update_webhook(
    hook_id: uuid.UUID,
    body: WebhookUpdate,
    request: Request,
    _user: User | None = Depends(require_project_write),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.project_id != get_project_id(request):
        raise HTTPException(status_code=404, detail="Webhook not found.")
    if body.name is not None:
        hook.name = body.name.strip() or "Webhook"
    if body.url is not None:
        hook.url = _validate_url(body.url)
    if body.events is not None:
        hook.events = json.dumps(_validate_events(body.events))
    if body.enabled is not None:
        hook.enabled = body.enabled
    db.commit()
    db.refresh(hook)
    return _to_response(hook)


@router.post("/{hook_id}/test", response_model=WebhookResponse)
def test_webhook(
    hook_id: uuid.UUID,
    request: Request,
    _user: User | None = Depends(require_project_write),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.project_id != get_project_id(request):
        raise HTTPException(status_code=404, detail="Webhook not found.")
    event_type = (_to_response(hook).events or ["memory.added"])[0]
    send_webhook(
        hook,
        event_type,
        {
            "test": True,
            "project_id": hook.project_id,
            "memory_id": str(uuid.uuid4()),
        },
    )
    db.commit()
    db.refresh(hook)
    return _to_response(hook)


@router.delete("/{hook_id}", response_model=MessageResponse)
def delete_webhook(
    hook_id: uuid.UUID,
    request: Request,
    _user: User | None = Depends(require_project_write),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, hook_id)
    if hook is None or hook.project_id != get_project_id(request):
        raise HTTPException(status_code=404, detail="Webhook not found.")
    db.delete(hook)
    db.commit()
    return MessageResponse(message="Webhook deleted.")


@compat_router.get("/projects/{project_id}/", response_model=list[WebhookResponse])
def platform_list_webhooks(
    project_id: str,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    _ensure_project_write(request, project_id, user, db)
    return list_webhooks(request, user, db)


@compat_router.post("/projects/{project_id}/", response_model=WebhookResponse, status_code=201)
def platform_create_webhook(
    project_id: str,
    body: dict,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    _ensure_project_write(request, project_id, user, db)
    payload = WebhookCreate(
        name=body.get("name") or "Webhook",
        url=body.get("url"),
        events=body.get("events") or body.get("event_types") or [],
        enabled=body.get("enabled", True),
    )
    return create_webhook(payload, request, user, db)


@compat_router.put("/{hook_id}/", response_model=WebhookResponse)
def platform_update_webhook(
    hook_id: uuid.UUID,
    body: dict,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, hook_id)
    auth_type = getattr(request.state, "auth_type", "none")
    if hook is None or (auth_type == "api_key" and hook.project_id != get_project_id(request)):
        raise HTTPException(status_code=404, detail="Webhook not found.")
    _ensure_project_write(request, hook.project_id, user, db)
    payload = WebhookUpdate(
        name=body.get("name"),
        url=body.get("url"),
        events=body.get("events") or body.get("event_types"),
        enabled=body.get("enabled"),
    )
    return update_webhook(hook_id, payload, request, user, db)


@compat_router.delete("/{hook_id}/", response_model=MessageResponse)
def platform_delete_webhook(
    hook_id: uuid.UUID,
    request: Request,
    user: User | None = Depends(verify_auth),
    db: Session = Depends(get_db),
):
    hook = db.get(Webhook, hook_id)
    auth_type = getattr(request.state, "auth_type", "none")
    if hook is None or (auth_type == "api_key" and hook.project_id != get_project_id(request)):
        raise HTTPException(status_code=404, detail="Webhook not found.")
    _ensure_project_write(request, hook.project_id, user, db)
    return delete_webhook(hook_id, request, user, db)
