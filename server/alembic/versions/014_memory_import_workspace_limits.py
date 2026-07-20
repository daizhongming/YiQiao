"""Track retained memory-import workspaces.

Revision ID: 014
Revises: 013
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memory_import_jobs",
        sa.Column("workspace_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "memory_import_jobs",
        sa.Column("source_retry_required", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute(
        sa.text(
            """
            UPDATE memory_import_jobs
            SET source_retry_required = false
            WHERE status = 'completed'
               OR (
                    status = 'completed_with_errors'
                    AND graph_status = 'failed'
                    AND failed_chunks = 0
                    AND NOT EXISTS (
                        SELECT 1
                        FROM memory_import_chunks
                        WHERE memory_import_chunks.job_id = memory_import_jobs.id
                          AND memory_import_chunks.status NOT IN (
                              'completed', 'imported', 'succeeded', 'skipped', 'split'
                          )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM memory_import_errors
                        WHERE memory_import_errors.job_id = memory_import_jobs.id
                          AND memory_import_errors.retryable = true
                          AND COALESCE(memory_import_errors.error_type, '') <> 'graph_sync_error'
                    )
               )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("memory_import_jobs", "source_retry_required")
    op.drop_column("memory_import_jobs", "workspace_bytes")
