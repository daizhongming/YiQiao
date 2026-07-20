"""Add usage attribution and quota policies.

Revision ID: 010
Revises: 009
Create Date: 2026-07-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("organization_id", sa.String(128), nullable=True))
    op.add_column("request_logs", sa.Column("api_key_id", sa.Uuid(), nullable=True))
    op.add_column("request_logs", sa.Column("actor_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "request_logs",
        sa.Column("operation", sa.String(32), nullable=False, server_default="api_request"),
    )
    op.create_foreign_key(
        "fk_request_logs_api_key_id",
        "request_logs",
        "api_keys",
        ["api_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_request_logs_actor_user_id",
        "request_logs",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_request_logs_api_key_id", "request_logs", ["api_key_id"])
    op.create_index("ix_request_logs_actor_user_id", "request_logs", ["actor_user_id"])
    op.create_index("ix_request_logs_operation", "request_logs", ["operation"])
    op.execute(
        """
        UPDATE request_logs
        SET operation = CASE
            WHEN method IN ('POST', 'PUT', 'PATCH') AND (
                path = '/memories' OR path LIKE '/memories/%' OR path = '/v3/memories/add/'
            ) THEN 'memory_write'
            WHEN method = 'POST' AND (path = '/search' OR path = '/v3/memories/search/')
                THEN 'memory_search'
            WHEN method IN ('GET', 'POST') AND (
                path = '/memories' OR path = '/v3/memories/'
            ) THEN 'memory_read'
            ELSE 'api_request'
        END
        """
    )
    op.alter_column("request_logs", "operation", server_default=None)

    op.create_table(
        "quota_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.String(255), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False, server_default="monitor"),
        sa.Column("warning_threshold", sa.Float(), nullable=False, server_default="0.8"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_type",
            "scope_id",
            "project_id",
            "metric",
            "period",
            name="uq_quota_policy_scope_metric_period",
        ),
    )
    op.create_index("ix_quota_policies_scope_type", "quota_policies", ["scope_type"])
    op.create_index("ix_quota_policies_scope_id", "quota_policies", ["scope_id"])
    op.create_index("ix_quota_policies_project_id", "quota_policies", ["project_id"])
    op.create_index("ix_quota_policies_metric", "quota_policies", ["metric"])


def downgrade() -> None:
    op.drop_table("quota_policies")
    op.drop_index("ix_request_logs_operation", table_name="request_logs")
    op.drop_index("ix_request_logs_actor_user_id", table_name="request_logs")
    op.drop_index("ix_request_logs_api_key_id", table_name="request_logs")
    op.drop_constraint("fk_request_logs_actor_user_id", "request_logs", type_="foreignkey")
    op.drop_constraint("fk_request_logs_api_key_id", "request_logs", type_="foreignkey")
    op.drop_column("request_logs", "operation")
    op.drop_column("request_logs", "actor_user_id")
    op.drop_column("request_logs", "api_key_id")
    op.drop_column("request_logs", "organization_id")
