# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Public OAuth and authenticated connected-application routes."""

from __future__ import annotations

import uuid
from typing import Any

import oauth_service
from auth import require_dashboard_user
from connector_protocol import AUDIENCE, PROTOCOL_VERSION, SUPPORTED_SCOPES
from db import get_db
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from models import User
from project_scope import get_project_id, normalize_project_id
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(tags=["oauth"])

DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_MAX_FORM_BYTES = 16 * 1024


class DeviceLookupRequest(BaseModel):
    user_code: str = Field(min_length=1, max_length=32)


class DeviceApprovalRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    approved_scopes: list[str] | None = Field(default=None, max_length=len(SUPPORTED_SCOPES))


class GrantApplicationRevocationRequest(BaseModel):
    client_id: str = Field(min_length=3, max_length=128)


class ApplicationRegistrationRequest(BaseModel):
    client_id: str = Field(min_length=3, max_length=128)
    display_name: str = Field(min_length=1, max_length=255)
    client_type: str = "public"
    allowed_audiences: list[str] = Field(default_factory=lambda: [AUDIENCE], min_length=1, max_length=1)
    allowed_scopes: list[str] = Field(
        default_factory=lambda: list(SUPPORTED_SCOPES),
        min_length=1,
        max_length=len(SUPPORTED_SCOPES),
    )
    operator_metadata: dict[str, Any] = Field(default_factory=dict)


def _oauth_error_response(exc: oauth_service.OAuthProtocolError) -> JSONResponse:
    headers = {"Cache-Control": "no-store, no-cache", "Pragma": "no-cache", **exc.headers}
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.error,
            "error_description": exc.description,
            "protocol_version": PROTOCOL_VERSION,
        },
        headers=headers,
    )


def _configuration_error_response() -> JSONResponse:
    return _oauth_error_response(
        oauth_service.OAuthProtocolError(
            "temporarily_unavailable",
            "OAuth service configuration is incomplete.",
            status_code=503,
        )
    )


async def _strict_form(
    request: Request,
    *,
    allowed: set[str],
    required: set[str],
) -> dict[str, str]:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        raise oauth_service.OAuthProtocolError(
            "invalid_request",
            "Content-Type must be application/x-www-form-urlencoded.",
            status_code=415,
        )
    body = await request.body()
    if len(body) > _MAX_FORM_BYTES:
        raise oauth_service.OAuthProtocolError("invalid_request", "The form payload is too large.", status_code=413)
    form = await request.form()
    pairs = list(form.multi_items())
    keys = [str(key) for key, _value in pairs]
    if any(key not in allowed for key in keys) or len(keys) != len(set(keys)):
        raise oauth_service.OAuthProtocolError("invalid_request", "The form contains unsupported or repeated fields.")
    values = {str(key): str(value) for key, value in pairs}
    if any(not values.get(key, "").strip() for key in required):
        raise oauth_service.OAuthProtocolError("invalid_request", "The form is missing a required field.")
    return values


def _management_context(request: Request) -> oauth_service.AuditContext:
    try:
        return oauth_service.audit_context(request)
    except oauth_service.OAuthProtocolError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.description) from exc


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata(request: Request):
    try:
        return oauth_service.authorization_server_metadata(oauth_service.issuer_for_request(request))
    except RuntimeError:
        return _configuration_error_response()


@router.get("/.well-known/service-capabilities")
def service_capabilities(request: Request):
    try:
        return oauth_service.service_capabilities(oauth_service.issuer_for_request(request))
    except RuntimeError:
        return _configuration_error_response()


@router.get("/oauth/health")
def oauth_health():
    return {"status": "ok", "service_id": "yiqiao", "protocol_version": PROTOCOL_VERSION}


@router.post("/oauth/device_authorization")
async def device_authorization(request: Request, db: Session = Depends(get_db)):
    try:
        context = oauth_service.audit_context(request)
        oauth_service.enforce_rate_limit(db, "device_authorization", context)
        form = await _strict_form(
            request,
            allowed={"client_id", "scope", "audience", "code_challenge", "code_challenge_method"},
            required={"client_id", "scope", "audience", "code_challenge", "code_challenge_method"},
        )
        client_id = oauth_service.rate_limit_public_client(
            db,
            "device_authorization",
            context,
            form["client_id"],
        )
        issuer = oauth_service.issuer_for_request(request)
        return oauth_service.create_device_authorization(
            db,
            client_id=client_id,
            scope=form["scope"],
            audience=form["audience"],
            code_challenge=form["code_challenge"],
            code_challenge_method=form["code_challenge_method"],
            issuer=issuer,
            context=context,
            rate_limit_client=False,
        )
    except oauth_service.OAuthProtocolError as exc:
        db.rollback()
        return _oauth_error_response(exc)
    except RuntimeError:
        db.rollback()
        return _configuration_error_response()


@router.post("/oauth/token")
async def token(request: Request, db: Session = Depends(get_db)):
    try:
        context = oauth_service.audit_context(request)
        oauth_service.enforce_rate_limit(db, "token", context)
        form = await _strict_form(
            request,
            allowed={"grant_type", "device_code", "client_id", "code_verifier", "refresh_token"},
            required={"grant_type", "client_id"},
        )
        client_id = oauth_service.rate_limit_public_client(db, "token", context, form["client_id"])
        if form["grant_type"] == DEVICE_GRANT_TYPE:
            if not form.get("device_code") or not form.get("code_verifier") or form.get("refresh_token"):
                raise oauth_service.OAuthProtocolError("invalid_request", "The Device Flow form is incomplete.")
            return oauth_service.exchange_device_code(
                db,
                device_code=form["device_code"],
                client_id=client_id,
                code_verifier=form["code_verifier"],
                context=context,
                rate_limit_client=False,
            )
        if form["grant_type"] == "refresh_token":
            if not form.get("refresh_token") or form.get("device_code") or form.get("code_verifier"):
                raise oauth_service.OAuthProtocolError("invalid_request", "The refresh form is incomplete.")
            return oauth_service.refresh_access_token(
                db,
                refresh_token=form["refresh_token"],
                client_id=client_id,
                context=context,
                rate_limit_client=False,
            )
        raise oauth_service.OAuthProtocolError("unsupported_grant_type", "The requested grant type is unsupported.")
    except oauth_service.OAuthProtocolError as exc:
        db.rollback()
        return _oauth_error_response(exc)


@router.post("/oauth/revoke")
async def revoke_token(request: Request, db: Session = Depends(get_db)):
    try:
        context = oauth_service.audit_context(request)
        oauth_service.enforce_rate_limit(db, "revocation", context)
        form = await _strict_form(
            request,
            allowed={"token", "token_type_hint", "client_id"},
            required={"token", "client_id"},
        )
        client_id = oauth_service.rate_limit_public_client(db, "revocation", context, form["client_id"])
        token_type_hint = form.get("token_type_hint")
        if token_type_hint not in {None, "access_token", "refresh_token"}:
            token_type_hint = None
        oauth_service.revoke_token(
            db,
            token=form["token"],
            token_type_hint=token_type_hint,
            client_id=client_id,
            context=context,
            rate_limit_client=False,
        )
        return Response(status_code=200)
    except oauth_service.OAuthProtocolError as exc:
        db.rollback()
        return _oauth_error_response(exc)


@router.post("/oauth/device-requests/lookup")
def lookup_device_request(
    request: Request,
    body: DeviceLookupRequest,
    _user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    try:
        return oauth_service.lookup_device_request(
            db,
            user_code=body.user_code,
            context=_management_context(request),
        )
    except oauth_service.OAuthProtocolError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.description,
            headers=exc.headers,
        ) from exc


@router.post("/oauth/device-requests/{request_id}/approve")
def approve_device_request(
    request_id: uuid.UUID,
    request: Request,
    body: DeviceApprovalRequest,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    project_id = normalize_project_id(body.project_id)
    if project_id != body.project_id.strip():
        raise HTTPException(status_code=400, detail="Project ID is invalid.")
    return oauth_service.approve_device_request(
        db,
        request_id=request_id,
        user=user,
        project_id=project_id,
        approved_scopes=body.approved_scopes,
        context=_management_context(request),
    )


@router.post("/oauth/device-requests/{request_id}/reject")
def reject_device_request(
    request_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.reject_device_request(
        db,
        request_id=request_id,
        user=user,
        context=_management_context(request),
    )


@router.get("/oauth/grants")
def list_grants(
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.list_grants(db, user=user, project_id=get_project_id(request))


@router.post("/oauth/grants/revoke-by-application")
def revoke_grants_by_application(
    request: Request,
    body: GrantApplicationRevocationRequest,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.revoke_grants_by_application(
        db,
        client_id=body.client_id,
        project_id=get_project_id(request),
        user=user,
        context=_management_context(request),
    )


@router.post("/oauth/grants/{grant_id}/revoke")
def revoke_grant(
    grant_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.revoke_grant(
        db,
        grant_id=grant_id,
        user=user,
        context=_management_context(request),
    )


@router.get("/oauth/applications")
def list_applications(
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.list_applications(db, user=user)


@router.post("/oauth/applications", status_code=201)
def register_application(
    request: Request,
    body: ApplicationRegistrationRequest,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.register_application(
        db,
        user=user,
        client_id=body.client_id,
        display_name=body.display_name,
        client_type=body.client_type,
        allowed_audiences=body.allowed_audiences,
        allowed_scopes=body.allowed_scopes,
        operator_metadata=body.operator_metadata,
        context=_management_context(request),
    )


@router.post("/oauth/applications/{client_id}/revoke")
def revoke_application(
    client_id: str,
    request: Request,
    user: User = Depends(require_dashboard_user),
    db: Session = Depends(get_db),
):
    return oauth_service.revoke_application(
        db,
        user=user,
        client_id=client_id,
        context=_management_context(request),
    )
