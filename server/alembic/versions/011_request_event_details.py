"""Add request event details used by the activity explorer.

Revision ID: 011
Revises: 010
Create Date: 2026-07-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("event_type", sa.String(32), nullable=True))
    op.add_column("request_logs", sa.Column("user_id", sa.String(255), nullable=True))
    op.add_column("request_logs", sa.Column("agent_id", sa.String(255), nullable=True))
    op.add_column("request_logs", sa.Column("app_id", sa.String(255), nullable=True))
    op.add_column("request_logs", sa.Column("run_id", sa.String(255), nullable=True))
    op.add_column("request_logs", sa.Column("request_payload", sa.JSON(), nullable=True))
    op.add_column("request_logs", sa.Column("response_payload", sa.JSON(), nullable=True))
    op.add_column("request_logs", sa.Column("result_count", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE request_logs
        SET event_type = CASE operation
            WHEN 'memory_write' THEN 'ADD'
            WHEN 'memory_search' THEN 'SEARCH'
            WHEN 'memory_read' THEN 'GET_ALL'
            ELSE UPPER(method)
        END
        """
    )
    for column in ("event_type", "user_id", "agent_id", "app_id", "run_id"):
        op.create_index(f"ix_request_logs_{column}", "request_logs", [column])


def downgrade() -> None:
    for column in ("run_id", "app_id", "agent_id", "user_id", "event_type"):
        op.drop_index(f"ix_request_logs_{column}", table_name="request_logs")
    op.drop_column("request_logs", "result_count")
    op.drop_column("request_logs", "response_payload")
    op.drop_column("request_logs", "request_payload")
    op.drop_column("request_logs", "run_id")
    op.drop_column("request_logs", "app_id")
    op.drop_column("request_logs", "agent_id")
    op.drop_column("request_logs", "user_id")
    op.drop_column("request_logs", "event_type")
