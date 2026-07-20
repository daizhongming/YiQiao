"""Add project scope

Revision ID: 009
Revises: 008
Create Date: 2026-07-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("project_id", sa.String(128), nullable=False, server_default="default-project"))
    op.add_column("request_logs", sa.Column("project_id", sa.String(128), nullable=True))
    op.add_column("webhooks", sa.Column("project_id", sa.String(128), nullable=False, server_default="default-project"))
    op.alter_column("api_keys", "project_id", server_default=None)
    op.alter_column("webhooks", "project_id", server_default=None)


def downgrade() -> None:
    op.drop_column("webhooks", "project_id")
    op.drop_column("request_logs", "project_id")
    op.drop_column("api_keys", "project_id")
