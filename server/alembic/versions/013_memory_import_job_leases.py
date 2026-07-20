"""Add execution leases to memory import jobs.

Revision ID: 013
Revises: 012
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("memory_import_jobs", sa.Column("lease_owner", sa.String(128), nullable=True))
    op.add_column(
        "memory_import_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_memory_import_jobs_lease_expires_at",
        "memory_import_jobs",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_import_jobs_lease_expires_at", table_name="memory_import_jobs")
    op.drop_column("memory_import_jobs", "lease_expires_at")
    op.drop_column("memory_import_jobs", "lease_owner")
