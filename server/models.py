# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import uuid
from datetime import datetime, timezone

from db import Base
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    key_prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(255))
    project_id: Mapped[str] = mapped_column(String(128), default="default-project")
    key_type: Mapped[str] = mapped_column(String(32), default="standard")
    scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class OAuthApplication(Base):
    __tablename__ = "oauth_applications"
    __table_args__ = (
        CheckConstraint("client_type IN ('public', 'confidential')", name="ck_oauth_applications_client_type"),
        CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_oauth_applications_status"),
        Index("ix_oauth_applications_status", "status"),
    )

    client_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    client_type: Mapped[str] = mapped_column(String(20), default="public")
    allowed_audiences: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")
    operator_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    device_authorizations: Mapped[list["OAuthDeviceAuthorization"]] = relationship(
        back_populates="application", passive_deletes=True
    )
    grants: Mapped[list["OAuthGrant"]] = relationship(back_populates="application", passive_deletes=True)
    audit_events: Mapped[list["OAuthAuditEvent"]] = relationship(back_populates="application", passive_deletes=True)


class OAuthDeviceAuthorization(Base):
    __tablename__ = "oauth_device_authorizations"
    __table_args__ = (
        UniqueConstraint("device_code_hash", name="uq_oauth_device_authorizations_device_code_hash"),
        UniqueConstraint("user_code_hash", name="uq_oauth_device_authorizations_user_code_hash"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'exchanged', 'expired')",
            name="ck_oauth_device_authorizations_status",
        ),
        CheckConstraint("code_challenge_method = 'S256'", name="ck_oauth_device_authorizations_pkce_method"),
        CheckConstraint("length(device_code_hash) = 64", name="ck_oauth_device_authorizations_device_hash"),
        CheckConstraint("length(user_code_hash) = 64", name="ck_oauth_device_authorizations_user_hash"),
        CheckConstraint("length(code_challenge) = 43", name="ck_oauth_device_authorizations_pkce_length"),
        CheckConstraint("interval_seconds >= 1", name="ck_oauth_device_authorizations_interval"),
        CheckConstraint("poll_count >= 0", name="ck_oauth_device_authorizations_poll_count"),
        Index("ix_oauth_device_authorizations_client_status", "client_id", "status"),
        Index("ix_oauth_device_authorizations_status_expires", "status", "expires_at"),
        Index("ix_oauth_device_authorizations_user_project", "user_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    device_code_hash: Mapped[str] = mapped_column(String(64))
    user_code_hash: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_applications.client_id", ondelete="RESTRICT"))
    audience: Mapped[str] = mapped_column(String(255))
    requested_scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_scopes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(128))
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=5)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    denied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exchanged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    application: Mapped["OAuthApplication"] = relationship(back_populates="device_authorizations")
    user: Mapped["User | None"] = relationship()
    grant: Mapped["OAuthGrant | None"] = relationship(back_populates="device_authorization", uselist=False)
    audit_events: Mapped[list["OAuthAuditEvent"]] = relationship(
        back_populates="device_authorization", passive_deletes=True
    )


class OAuthGrant(Base):
    __tablename__ = "oauth_grants"
    __table_args__ = (
        UniqueConstraint("device_authorization_id", name="uq_oauth_grants_device_authorization_id"),
        UniqueConstraint("access_token_hash", name="uq_oauth_grants_access_token_hash"),
        CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_oauth_grants_status"),
        CheckConstraint("length(access_token_hash) = 64", name="ck_oauth_grants_access_token_hash"),
        CheckConstraint("access_expires_at <= refresh_expires_at", name="ck_oauth_grants_token_expiry_order"),
        Index("ix_oauth_grants_client_project_status", "client_id", "project_id", "status"),
        Index("ix_oauth_grants_user_project_status", "user_id", "project_id", "status"),
        Index("ix_oauth_grants_status_access_expires", "status", "access_expires_at"),
        Index("ix_oauth_grants_status_refresh_expires", "status", "refresh_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    device_authorization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("oauth_device_authorizations.id", ondelete="RESTRICT")
    )
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_applications.client_id", ondelete="RESTRICT"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    project_id: Mapped[str] = mapped_column(String(128))
    audience: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")
    access_token_hash: Mapped[str] = mapped_column(String(64))
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    application: Mapped["OAuthApplication"] = relationship(back_populates="grants")
    device_authorization: Mapped["OAuthDeviceAuthorization"] = relationship(back_populates="grant")
    user: Mapped["User"] = relationship()
    refresh_tokens: Mapped[list["OAuthRefreshToken"]] = relationship(
        back_populates="grant", cascade="all, delete-orphan", passive_deletes=True
    )
    audit_events: Mapped[list["OAuthAuditEvent"]] = relationship(back_populates="grant", passive_deletes=True)


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_oauth_refresh_tokens_token_hash"),
        UniqueConstraint("replaced_by_id", name="uq_oauth_refresh_tokens_replaced_by_id"),
        CheckConstraint("family_id = grant_id", name="ck_oauth_refresh_tokens_family_matches_grant"),
        CheckConstraint("status IN ('active', 'rotated', 'revoked', 'expired')", name="ck_oauth_refresh_tokens_status"),
        CheckConstraint("length(token_hash) = 64", name="ck_oauth_refresh_tokens_token_hash"),
        CheckConstraint("retain_until >= expires_at", name="ck_oauth_refresh_tokens_retention"),
        Index("ix_oauth_refresh_tokens_family_status", "family_id", "status"),
        Index("ix_oauth_refresh_tokens_grant_status", "grant_id", "status"),
        Index("ix_oauth_refresh_tokens_status_retain_until", "status", "retain_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    grant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oauth_grants.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column()
    token_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oauth_refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    grant: Mapped["OAuthGrant"] = relationship(back_populates="refresh_tokens")
    replaced_by: Mapped["OAuthRefreshToken | None"] = relationship(
        foreign_keys=[replaced_by_id], remote_side=[id], uselist=False
    )


class OAuthAuditEvent(Base):
    __tablename__ = "oauth_audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('success', 'denied', 'error')", name="ck_oauth_audit_events_outcome"),
        CheckConstraint(
            "remote_ip_hash IS NULL OR length(remote_ip_hash) = 64",
            name="ck_oauth_audit_events_remote_ip_hash",
        ),
        CheckConstraint(
            "user_agent_hash IS NULL OR length(user_agent_hash) = 64",
            name="ck_oauth_audit_events_user_agent_hash",
        ),
        CheckConstraint(
            "rate_limit_key_hash IS NULL OR length(rate_limit_key_hash) = 64",
            name="ck_oauth_audit_events_rate_limit_key_hash",
        ),
        Index("ix_oauth_audit_events_client_created", "client_id", "created_at"),
        Index("ix_oauth_audit_events_client_event_created", "client_id", "event_type", "created_at"),
        Index("ix_oauth_audit_events_grant_created", "grant_id", "created_at"),
        Index("ix_oauth_audit_events_remote_ip_event_created", "remote_ip_hash", "event_type", "created_at"),
        Index(
            "ix_oauth_audit_events_rate_limit_event_created",
            "rate_limit_key_hash",
            "event_type",
            "created_at",
        ),
        Index("ix_oauth_audit_events_project_event_created", "project_id", "event_type", "created_at"),
        Index("ix_oauth_audit_events_event_outcome_created", "event_type", "outcome", "created_at"),
        Index("ix_oauth_audit_events_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("oauth_applications.client_id", ondelete="SET NULL"), nullable=True
    )
    device_authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oauth_device_authorizations.id", ondelete="SET NULL"), nullable=True
    )
    grant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("oauth_grants.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    remote_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rate_limit_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    application: Mapped["OAuthApplication | None"] = relationship(back_populates="audit_events")
    device_authorization: Mapped["OAuthDeviceAuthorization | None"] = relationship(back_populates="audit_events")
    grant: Mapped["OAuthGrant | None"] = relationship(back_populates="audit_events")
    user: Mapped["User | None"] = relationship()


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    method: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(512))
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float)
    auth_type: Mapped[str] = mapped_column(String(32), default="none")
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operation: Mapped[str] = mapped_column(String(32), default="api_request", index=True)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    app_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    request_payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    response_payload: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class QuotaPolicy(Base):
    __tablename__ = "quota_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_id",
            "project_id",
            "metric",
            "period",
            name="uq_quota_policy_scope_metric_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    scope_type: Mapped[str] = mapped_column(String(24), index=True)
    scope_id: Mapped[str] = mapped_column(String(255), index=True)
    project_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    metric: Mapped[str] = mapped_column(String(32), index=True)
    period: Mapped[str] = mapped_column(String(16))
    limit_value: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16), default="monitor")
    warning_threshold: Mapped[float] = mapped_column(Float, default=0.8)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class RefreshTokenJti(Base):
    __tablename__ = "refresh_token_jtis"

    jti: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Settings(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String(255), default="Webhook")
    project_id: Mapped[str] = mapped_column(String(128), default="default-project")
    url: Mapped[str] = mapped_column(String(2048))
    events: Mapped[str] = mapped_column(Text)
    signing_secret: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    last_delivery_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MemoryImportJob(Base):
    __tablename__ = "memory_import_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    phase: Mapped[str] = mapped_column(String(32), default="queued")
    input_files: Mapped[list] = mapped_column(JSON, default=list)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_quota_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    workspace: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    workspace_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    source_retry_required: Mapped[bool] = mapped_column(Boolean, default=True)
    source_app: Mapped[str] = mapped_column(String(64), default="auto")
    infer: Mapped[bool] = mapped_column(Boolean, default=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    total_input_files: Mapped[int] = mapped_column(Integer, default=0)
    discovered_files: Mapped[int] = mapped_column(Integer, default=0)
    parsed_files: Mapped[int] = mapped_column(Integer, default=0)
    skipped_files: Mapped[int] = mapped_column(Integer, default=0)
    total_conversations: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    imported_chunks: Mapped[int] = mapped_column(Integer, default=0)
    skipped_chunks: Mapped[int] = mapped_column(Integer, default=0)
    failed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    memories_created: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    worker_count: Mapped[int] = mapped_column(Integer, default=0)
    current_concurrency: Mapped[int] = mapped_column(Integer, default=0)
    active_workers: Mapped[int] = mapped_column(Integer, default=0)
    peak_workers: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    retried_chunks: Mapped[int] = mapped_column(Integer, default=0)
    split_count: Mapped[int] = mapped_column(Integer, default=0)
    split_chunks: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    graph_status: Mapped[str] = mapped_column(String(32), default="pending")
    graph_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_attempts: Mapped[int] = mapped_column(Integer, default=0)
    graph_pending_items: Mapped[int] = mapped_column(Integer, default=0)
    graph_synced_items: Mapped[int] = mapped_column(Integer, default=0)
    graph_failed_items: Mapped[int] = mapped_column(Integer, default=0)
    phase_durations: Mapped[dict] = mapped_column(JSON, default=dict)

    current_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    current_conversation: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MemoryImportChunk(Base):
    __tablename__ = "memory_import_chunks"
    __table_args__ = (UniqueConstraint("job_id", "import_key", name="uq_memory_import_chunk_job_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(_new_uuid()))
    job_id: Mapped[str] = mapped_column(ForeignKey("memory_import_jobs.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    import_key: Mapped[str] = mapped_column(String(128))
    conversation_id: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=1)
    source_message_indices: Mapped[list] = mapped_column(JSON, default=list)
    core_source_message_indices: Mapped[list] = mapped_column(JSON, default=list)
    source_message_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_turn_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_turn_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_import_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    split_depth: Mapped[int] = mapped_column(Integer, default=0)
    overlap_turns: Mapped[int] = mapped_column(Integer, default=0)
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    conversation_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    model_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_result: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audit_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    claimed_memory_hashes: Mapped[list] = mapped_column(JSON, default=list)
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MemoryImportManifest(Base):
    __tablename__ = "memory_import_manifests"
    __table_args__ = (UniqueConstraint("project_id", "import_key", name="uq_memory_import_manifest_project_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(_new_uuid()))
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    import_key: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_import_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_import_chunks.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="claimed", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    memory_ids: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MemoryImportGraphItem(Base):
    __tablename__ = "memory_import_graph_items"
    __table_args__ = (UniqueConstraint("job_id", "chunk_id", "item_key", name="uq_memory_import_graph_item_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(_new_uuid()))
    job_id: Mapped[str] = mapped_column(ForeignKey("memory_import_jobs.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("memory_import_chunks.id", ondelete="CASCADE"), index=True)
    item_key: Mapped[str] = mapped_column(String(128))
    memory_id: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class MemoryImportError(Base):
    __tablename__ = "memory_import_errors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(_new_uuid()))
    job_id: Mapped[str] = mapped_column(ForeignKey("memory_import_jobs.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_import_chunks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class MemoryImportHash(Base):
    __tablename__ = "memory_import_hashes"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "conversation_id",
            "memory_hash",
            name="uq_memory_import_hash_project_conversation_hash",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(_new_uuid()))
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    conversation_id: Mapped[str] = mapped_column(String(512), index=True)
    memory_hash: Mapped[str] = mapped_column(String(128))
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_import_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_import_chunks.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="claimed", index=True)
    memory_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


# Explicit aliases keep repository consumers decoupled from the table naming convention.
ImportJobRecord = MemoryImportJob
ImportChunkRecord = MemoryImportChunk
ImportManifestRecord = MemoryImportManifest
ImportGraphItemRecord = MemoryImportGraphItem
ImportErrorRecord = MemoryImportError
ImportMemoryHashRecord = MemoryImportHash
ImportJob = MemoryImportJob
ImportChunk = MemoryImportChunk
ImportManifest = MemoryImportManifest
ImportGraphItem = MemoryImportGraphItem
ImportError = MemoryImportError
ImportMemoryHash = MemoryImportHash
MemoryImportMemoryHash = MemoryImportHash
