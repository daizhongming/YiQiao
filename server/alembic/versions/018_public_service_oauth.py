# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Replace client-specific pairing state with Public Service OAuth.

Revision ID: 018
Revises: 017
Create Date: 2026-07-22

Downgrade recreates the legacy table schema empty. It cannot restore discarded
pairing rows and deliberately does not undo legacy-key revocations.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_applications",
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("client_type", sa.String(length=20), nullable=False, server_default="public"),
        sa.Column("allowed_audiences", sa.JSON(), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("operator_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("client_type IN ('public', 'confidential')", name="ck_oauth_applications_client_type"),
        sa.CheckConstraint("status IN ('active', 'disabled', 'revoked')", name="ck_oauth_applications_status"),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index("ix_oauth_applications_status", "oauth_applications", ["status"], unique=False)

    op.create_table(
        "oauth_device_authorizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_code_hash", sa.String(length=64), nullable=False),
        sa.Column("user_code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("audience", sa.String(length=255), nullable=False),
        sa.Column("requested_scopes", sa.JSON(), nullable=False),
        sa.Column("approved_scopes", sa.JSON(), nullable=True),
        sa.Column("code_challenge", sa.String(length=128), nullable=False),
        sa.Column("code_challenge_method", sa.String(length=10), nullable=False, server_default="S256"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("poll_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("denied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exchanged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'exchanged', 'expired')",
            name="ck_oauth_device_authorizations_status",
        ),
        sa.CheckConstraint("code_challenge_method = 'S256'", name="ck_oauth_device_authorizations_pkce_method"),
        sa.CheckConstraint("length(device_code_hash) = 64", name="ck_oauth_device_authorizations_device_hash"),
        sa.CheckConstraint("length(user_code_hash) = 64", name="ck_oauth_device_authorizations_user_hash"),
        sa.CheckConstraint("length(code_challenge) = 43", name="ck_oauth_device_authorizations_pkce_length"),
        sa.CheckConstraint("interval_seconds >= 1", name="ck_oauth_device_authorizations_interval"),
        sa.CheckConstraint("poll_count >= 0", name="ck_oauth_device_authorizations_poll_count"),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_applications.client_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_code_hash", name="uq_oauth_device_authorizations_device_code_hash"),
        sa.UniqueConstraint("user_code_hash", name="uq_oauth_device_authorizations_user_code_hash"),
    )
    op.create_index(
        "ix_oauth_device_authorizations_client_status",
        "oauth_device_authorizations",
        ["client_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_device_authorizations_status_expires",
        "oauth_device_authorizations",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_device_authorizations_user_project",
        "oauth_device_authorizations",
        ["user_id", "project_id"],
        unique=False,
    )

    op.create_table(
        "oauth_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_authorization_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=False),
        sa.Column("audience", sa.String(length=255), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("access_token_hash", sa.String(length=64), nullable=False),
        sa.Column("access_token_prefix", sa.String(length=16), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_oauth_grants_status"),
        sa.CheckConstraint("length(access_token_hash) = 64", name="ck_oauth_grants_access_token_hash"),
        sa.CheckConstraint("access_expires_at <= refresh_expires_at", name="ck_oauth_grants_token_expiry_order"),
        sa.ForeignKeyConstraint(["device_authorization_id"], ["oauth_device_authorizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_applications.client_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_authorization_id", name="uq_oauth_grants_device_authorization_id"),
        sa.UniqueConstraint("access_token_hash", name="uq_oauth_grants_access_token_hash"),
    )
    op.create_index(
        "ix_oauth_grants_client_project_status",
        "oauth_grants",
        ["client_id", "project_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_grants_user_project_status",
        "oauth_grants",
        ["user_id", "project_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_grants_status_access_expires",
        "oauth_grants",
        ["status", "access_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_grants_status_refresh_expires",
        "oauth_grants",
        ["status", "refresh_expires_at"],
        unique=False,
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("family_id = grant_id", name="ck_oauth_refresh_tokens_family_matches_grant"),
        sa.CheckConstraint(
            "status IN ('active', 'rotated', 'revoked', 'expired')", name="ck_oauth_refresh_tokens_status"
        ),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_oauth_refresh_tokens_token_hash"),
        sa.CheckConstraint("retain_until >= expires_at", name="ck_oauth_refresh_tokens_retention"),
        sa.ForeignKeyConstraint(["grant_id"], ["oauth_grants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replaced_by_id"], ["oauth_refresh_tokens.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_oauth_refresh_tokens_token_hash"),
        sa.UniqueConstraint("replaced_by_id", name="uq_oauth_refresh_tokens_replaced_by_id"),
    )
    op.create_index(
        "ix_oauth_refresh_tokens_family_status",
        "oauth_refresh_tokens",
        ["family_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_refresh_tokens_grant_status",
        "oauth_refresh_tokens",
        ["grant_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_refresh_tokens_status_retain_until",
        "oauth_refresh_tokens",
        ["status", "retain_until"],
        unique=False,
    )

    op.create_table(
        "oauth_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("device_authorization_id", sa.Uuid(), nullable=True),
        sa.Column("grant_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("remote_ip_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=64), nullable=True),
        sa.Column("rate_limit_key_hash", sa.String(length=64), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("outcome IN ('success', 'denied', 'error')", name="ck_oauth_audit_events_outcome"),
        sa.CheckConstraint(
            "remote_ip_hash IS NULL OR length(remote_ip_hash) = 64",
            name="ck_oauth_audit_events_remote_ip_hash",
        ),
        sa.CheckConstraint(
            "user_agent_hash IS NULL OR length(user_agent_hash) = 64",
            name="ck_oauth_audit_events_user_agent_hash",
        ),
        sa.CheckConstraint(
            "rate_limit_key_hash IS NULL OR length(rate_limit_key_hash) = 64",
            name="ck_oauth_audit_events_rate_limit_key_hash",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["oauth_applications.client_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_authorization_id"], ["oauth_device_authorizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["grant_id"], ["oauth_grants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oauth_audit_events_client_created",
        "oauth_audit_events",
        ["client_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_client_event_created",
        "oauth_audit_events",
        ["client_id", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_grant_created",
        "oauth_audit_events",
        ["grant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_remote_ip_event_created",
        "oauth_audit_events",
        ["remote_ip_hash", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_rate_limit_event_created",
        "oauth_audit_events",
        ["rate_limit_key_hash", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_project_event_created",
        "oauth_audit_events",
        ["project_id", "event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_oauth_audit_events_event_outcome_created",
        "oauth_audit_events",
        ["event_type", "outcome", "created_at"],
        unique=False,
    )
    op.create_index("ix_oauth_audit_events_created_at", "oauth_audit_events", ["created_at"], unique=False)

    now = datetime.now(timezone.utc)
    oauth_applications = sa.table(
        "oauth_applications",
        sa.column("client_id", sa.String()),
        sa.column("display_name", sa.String()),
        sa.column("client_type", sa.String()),
        sa.column("allowed_audiences", sa.JSON()),
        sa.column("allowed_scopes", sa.JSON()),
        sa.column("status", sa.String()),
        sa.column("operator_metadata", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        oauth_applications,
        [
            {
                "client_id": "boss-helper",
                "display_name": "BossHelper",
                "client_type": "public",
                "allowed_audiences": ["yiqiao:memory-api"],
                "allowed_scopes": ["memory:read", "memory:write"],
                "status": "active",
                "operator_metadata": {"registration_source": "migration-018"},
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    api_keys = sa.table(
        "api_keys",
        sa.column("key_type", sa.String()),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.get_bind().execute(
        api_keys.update()
        .where(api_keys.c.key_type == "boss_helper")
        .where(api_keys.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    op.drop_table("boss_helper_pairings")


def downgrade() -> None:
    op.create_table(
        "boss_helper_pairings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_code_hash", sa.String(length=64), nullable=False),
        sa.Column("user_code_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_boss_helper_pairings_device_code_hash",
        "boss_helper_pairings",
        ["device_code_hash"],
        unique=True,
    )
    op.create_index("ix_boss_helper_pairings_user_code_hash", "boss_helper_pairings", ["user_code_hash"], unique=True)
    op.create_index("ix_boss_helper_pairings_status", "boss_helper_pairings", ["status"], unique=False)
    op.create_index("ix_boss_helper_pairings_project_id", "boss_helper_pairings", ["project_id"], unique=False)
    op.create_index("ix_boss_helper_pairings_api_key_id", "boss_helper_pairings", ["api_key_id"], unique=False)
    op.create_index("ix_boss_helper_pairings_expires_at", "boss_helper_pairings", ["expires_at"], unique=False)

    op.drop_table("oauth_audit_events")
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_grants")
    op.drop_table("oauth_device_authorizations")
    op.drop_table("oauth_applications")
