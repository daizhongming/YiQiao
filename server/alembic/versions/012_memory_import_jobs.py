"""Persist memory import jobs and their idempotency state.

Revision ID: 012
Revises: 011
Create Date: 2026-07-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_import_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("phase", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("input_files", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("entities", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("options", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("workspace", sa.String(1024), nullable=True),
        sa.Column("source_app", sa.String(64), nullable=False, server_default="auto"),
        sa.Column("infer", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total_input_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovered_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parsed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_conversations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memories_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_concurrency", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_workers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("peak_workers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retried_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("split_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("split_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graph_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("graph_error", sa.Text(), nullable=True),
        sa.Column("graph_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graph_pending_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graph_synced_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("graph_failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase_durations", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("current_file", sa.String(1024), nullable=True),
        sa.Column("current_conversation", sa.String(512), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_import_jobs_project_id", "memory_import_jobs", ["project_id"])
    op.create_index("ix_memory_import_jobs_status", "memory_import_jobs", ["status"])
    op.create_index("ix_memory_import_jobs_created_at", "memory_import_jobs", ["created_at"])
    op.create_index(
        "ix_memory_import_jobs_project_created",
        "memory_import_jobs",
        ["project_id", "created_at"],
    )

    op.create_table(
        "memory_import_chunks",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("import_key", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_message_indices", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("core_source_message_indices", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_message_start", sa.Integer(), nullable=True),
        sa.Column("source_message_end", sa.Integer(), nullable=True),
        sa.Column("source_turn_start", sa.Integer(), nullable=True),
        sa.Column("source_turn_end", sa.Integer(), nullable=True),
        sa.Column("parent_import_key", sa.String(128), nullable=True),
        sa.Column("split_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("overlap_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_path", sa.String(1024), nullable=True),
        sa.Column("conversation_title", sa.String(512), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("timings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("model_used", sa.String(255), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("audit_result", sa.String(64), nullable=True),
        sa.Column("audit_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("claimed_memory_hashes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("memory_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_type", sa.String(255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["memory_import_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "import_key", name="uq_memory_import_chunk_job_key"),
    )
    op.create_index("ix_memory_import_chunks_job_id", "memory_import_chunks", ["job_id"])
    op.create_index("ix_memory_import_chunks_project_id", "memory_import_chunks", ["project_id"])
    op.create_index("ix_memory_import_chunks_conversation_id", "memory_import_chunks", ["conversation_id"])
    op.create_index("ix_memory_import_chunks_status", "memory_import_chunks", ["status"])
    op.create_index(
        "ix_memory_import_chunks_job_status_order",
        "memory_import_chunks",
        ["job_id", "status", "chunk_index"],
    )

    op.create_table(
        "memory_import_manifests",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("import_key", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("chunk_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="claimed"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("memory_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["memory_import_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chunk_id"], ["memory_import_chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "import_key", name="uq_memory_import_manifest_project_key"),
    )
    op.create_index("ix_memory_import_manifests_project_id", "memory_import_manifests", ["project_id"])
    op.create_index("ix_memory_import_manifests_job_id", "memory_import_manifests", ["job_id"])
    op.create_index("ix_memory_import_manifests_status", "memory_import_manifests", ["status"])
    op.create_index(
        "ix_memory_import_manifests_project_status",
        "memory_import_manifests",
        ["project_id", "status"],
    )

    op.create_table(
        "memory_import_graph_items",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("chunk_id", sa.String(36), nullable=False),
        sa.Column("item_key", sa.String(128), nullable=False),
        sa.Column("memory_id", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["memory_import_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["memory_import_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "chunk_id", "item_key", name="uq_memory_import_graph_item_key"),
    )
    op.create_index("ix_memory_import_graph_items_job_id", "memory_import_graph_items", ["job_id"])
    op.create_index("ix_memory_import_graph_items_chunk_id", "memory_import_graph_items", ["chunk_id"])
    op.create_index("ix_memory_import_graph_items_memory_id", "memory_import_graph_items", ["memory_id"])
    op.create_index("ix_memory_import_graph_items_status", "memory_import_graph_items", ["status"])
    op.create_index(
        "ix_memory_import_graph_items_job_status_retry",
        "memory_import_graph_items",
        ["job_id", "status", "next_retry_at"],
    )

    op.create_table(
        "memory_import_errors",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("chunk_id", sa.String(36), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(64), nullable=True),
        sa.Column("error_type", sa.String(255), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["memory_import_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["memory_import_chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_memory_import_errors_job_id", "memory_import_errors", ["job_id"])
    op.create_index("ix_memory_import_errors_chunk_id", "memory_import_errors", ["chunk_id"])
    op.create_index("ix_memory_import_errors_created_at", "memory_import_errors", ["created_at"])
    op.create_index(
        "ix_memory_import_errors_job_created",
        "memory_import_errors",
        ["job_id", "created_at"],
    )

    op.create_table(
        "memory_import_hashes",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(512), nullable=False),
        sa.Column("memory_hash", sa.String(128), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("chunk_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="claimed"),
        sa.Column("memory_id", sa.String(255), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["memory_import_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chunk_id"], ["memory_import_chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "conversation_id",
            "memory_hash",
            name="uq_memory_import_hash_project_conversation_hash",
        ),
    )
    op.create_index("ix_memory_import_hashes_project_id", "memory_import_hashes", ["project_id"])
    op.create_index("ix_memory_import_hashes_conversation_id", "memory_import_hashes", ["conversation_id"])
    op.create_index("ix_memory_import_hashes_job_id", "memory_import_hashes", ["job_id"])
    op.create_index("ix_memory_import_hashes_status", "memory_import_hashes", ["status"])
    op.create_index(
        "ix_memory_import_hashes_lookup",
        "memory_import_hashes",
        ["project_id", "conversation_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("memory_import_hashes")
    op.drop_table("memory_import_errors")
    op.drop_table("memory_import_graph_items")
    op.drop_table("memory_import_manifests")
    op.drop_table("memory_import_chunks")
    op.drop_table("memory_import_jobs")
