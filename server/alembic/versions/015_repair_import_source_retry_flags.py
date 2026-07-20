"""Repair legacy memory-import source retry flags.

Revision ID: 015
Revises: 014
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_RETRY_REPAIR_SQL = """
UPDATE memory_import_jobs
SET source_retry_required = true
WHERE source_retry_required = false
  AND status = 'completed_with_errors'
  AND (
      EXISTS (
          SELECT 1
          FROM memory_import_chunks
          WHERE memory_import_chunks.job_id = memory_import_jobs.id
            AND memory_import_chunks.status NOT IN (
                'completed', 'imported', 'succeeded', 'skipped', 'split'
            )
      )
      OR EXISTS (
          SELECT 1
          FROM memory_import_errors
          WHERE memory_import_errors.job_id = memory_import_jobs.id
            AND memory_import_errors.retryable = true
            AND COALESCE(memory_import_errors.error_type, '') <> 'graph_sync_error'
      )
  )
"""


def upgrade() -> None:
    op.execute(sa.text(SOURCE_RETRY_REPAIR_SQL))


def downgrade() -> None:
    # The previous false values cannot be distinguished from legitimate graph-only rows.
    pass
