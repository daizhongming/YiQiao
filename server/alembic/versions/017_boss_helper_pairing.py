"""Add scoped API keys and persistent BossHelper pairing state.

Revision ID: 017
Revises: 016
Create Date: 2026-07-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("key_type", sa.String(32), nullable=False, server_default="standard"),
    )
    op.add_column("api_keys", sa.Column("scopes", sa.JSON(), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "boss_helper_pairings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_code_hash", sa.String(64), nullable=False),
        sa.Column("user_code_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column(
            "approved_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "api_key_id",
            sa.Uuid(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_boss_helper_pairings_device_code_hash",
        "boss_helper_pairings",
        ["device_code_hash"],
        unique=True,
    )
    op.create_index(
        "ix_boss_helper_pairings_user_code_hash",
        "boss_helper_pairings",
        ["user_code_hash"],
        unique=True,
    )
    op.create_index(
        "ix_boss_helper_pairings_status",
        "boss_helper_pairings",
        ["status"],
    )
    op.create_index(
        "ix_boss_helper_pairings_project_id",
        "boss_helper_pairings",
        ["project_id"],
    )
    op.create_index(
        "ix_boss_helper_pairings_api_key_id",
        "boss_helper_pairings",
        ["api_key_id"],
    )
    op.create_index(
        "ix_boss_helper_pairings_expires_at",
        "boss_helper_pairings",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_boss_helper_pairings_expires_at", table_name="boss_helper_pairings")
    op.drop_index("ix_boss_helper_pairings_api_key_id", table_name="boss_helper_pairings")
    op.drop_index("ix_boss_helper_pairings_project_id", table_name="boss_helper_pairings")
    op.drop_index("ix_boss_helper_pairings_status", table_name="boss_helper_pairings")
    op.drop_index("ix_boss_helper_pairings_user_code_hash", table_name="boss_helper_pairings")
    op.drop_index("ix_boss_helper_pairings_device_code_hash", table_name="boss_helper_pairings")
    op.drop_table("boss_helper_pairings")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("api_keys", "scopes")
    op.drop_column("api_keys", "key_type")
