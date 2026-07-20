# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


class SQLiteManager:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._migrate_history_table()
        self._create_history_table()
        self._create_messages_table()

    def _migrate_history_table(self) -> None:
        """
        If a pre-existing history table had the old group-chat columns,
        rename it, create the new schema, copy the intersecting data, then
        drop the old table.
        """
        with self._lock:
            try:
                # Start a transaction
                self.connection.execute("BEGIN")
                cur = self.connection.cursor()

                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history'")
                if cur.fetchone() is None:
                    self.connection.execute("COMMIT")
                    return  # nothing to migrate

                cur.execute("PRAGMA table_info(history)")
                old_cols = {row[1] for row in cur.fetchall()}

                expected_cols = {
                    "id",
                    "memory_id",
                    "old_memory",
                    "new_memory",
                    "event",
                    "created_at",
                    "updated_at",
                    "is_deleted",
                    "actor_id",
                    "role",
                    "project_id",
                }

                if old_cols == expected_cols:
                    self.connection.execute("COMMIT")
                    return

                logger.info("Migrating history table to the current schema.")

                # Clean up any existing history_old table from previous failed migration
                cur.execute("DROP TABLE IF EXISTS history_old")

                # Rename the current history table
                cur.execute("ALTER TABLE history RENAME TO history_old")

                # Create the new history table with updated schema
                cur.execute(
                    """
                    CREATE TABLE history (
                        id           TEXT PRIMARY KEY,
                        memory_id    TEXT,
                        old_memory   TEXT,
                        new_memory   TEXT,
                        event        TEXT,
                        created_at   DATETIME,
                        updated_at   DATETIME,
                        is_deleted   INTEGER,
                        actor_id     TEXT,
                        role         TEXT,
                        project_id   TEXT
                    )
                """
                )

                # Copy data from old table to new table
                intersecting = list(expected_cols & old_cols)
                if intersecting:
                    cols_csv = ", ".join(intersecting)
                    cur.execute(f"INSERT INTO history ({cols_csv}) SELECT {cols_csv} FROM history_old")

                # Drop the old table
                cur.execute("DROP TABLE history_old")

                # Commit the transaction
                self.connection.execute("COMMIT")
                logger.info("History table migration completed successfully.")

            except Exception as e:
                # Rollback the transaction on any error
                self.connection.execute("ROLLBACK")
                logger.error(f"History table migration failed: {e}")
                raise

    def _create_history_table(self) -> None:
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id           TEXT PRIMARY KEY,
                        memory_id    TEXT,
                        old_memory   TEXT,
                        new_memory   TEXT,
                        event        TEXT,
                        created_at   DATETIME,
                        updated_at   DATETIME,
                        is_deleted   INTEGER,
                        actor_id     TEXT,
                        role         TEXT,
                        project_id   TEXT
                    )
                """
                )
                self.connection.execute("CREATE INDEX IF NOT EXISTS ix_history_project_id ON history(project_id)")
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create history table: {e}")
                raise

    def _create_messages_table(self) -> None:
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id TEXT PRIMARY KEY,
                        session_scope TEXT,
                        role TEXT,
                        content TEXT,
                        name TEXT,
                        created_at DATETIME
                    )
                """
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create messages table: {e}")
                raise

    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    INSERT INTO history (
                        id, memory_id, old_memory, new_memory, event,
                        created_at, updated_at, is_deleted, actor_id, role, project_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        str(uuid.uuid4()),
                        memory_id,
                        old_memory,
                        new_memory,
                        event,
                        created_at,
                        updated_at,
                        is_deleted,
                        actor_id,
                        role,
                        project_id,
                    ),
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to add history record: {e}")
                raise

    def batch_add_history(
        self,
        records: List[Dict[str, Any]],
        *,
        deduplicate_by_memory_event: bool = False,
    ) -> None:
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                prepared = [
                    (
                        str(record.get("id") or uuid.uuid4()),
                        record.get("memory_id"),
                        record.get("old_memory"),
                        record.get("new_memory"),
                        record.get("event"),
                        record.get("created_at"),
                        record.get("updated_at"),
                        record.get("is_deleted", 0),
                        record.get("actor_id"),
                        record.get("role"),
                        record.get("project_id"),
                    )
                    for record in records
                ]
                if deduplicate_by_memory_event:
                    self.connection.executemany(
                        """
                        INSERT OR IGNORE INTO history (
                            id, memory_id, old_memory, new_memory, event,
                            created_at, updated_at, is_deleted, actor_id, role, project_id
                        )
                        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        WHERE NOT EXISTS (
                            SELECT 1 FROM history
                            WHERE memory_id = ? AND event = ? AND COALESCE(is_deleted, 0) = ?
                        )
                    """,
                        [(*record, record[1], record[4], record[7]) for record in prepared],
                    )
                else:
                    self.connection.executemany(
                        """
                        INSERT INTO history (
                            id, memory_id, old_memory, new_memory, event,
                            created_at, updated_at, is_deleted, actor_id, role, project_id
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        prepared,
                    )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to batch add history records: {e}")
                raise

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT id, memory_id, old_memory, new_memory, event,
                       created_at, updated_at, is_deleted, actor_id, role
                FROM history
                WHERE memory_id = ?
                ORDER BY created_at ASC, DATETIME(updated_at) ASC
            """,
                (memory_id,),
            )
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "old_memory": r[2],
                "new_memory": r[3],
                "event": r[4],
                "created_at": r[5],
                "updated_at": r[6],
                "is_deleted": bool(r[7]),
                "actor_id": r[8],
                "role": r[9],
            }
            for r in rows
        ]

    def save_messages(
        self,
        messages: List[Dict[str, Any]],
        session_scope: str,
        *,
        message_ids: Optional[List[str]] = None,
    ) -> None:
        if not messages:
            return
        if message_ids is not None and len(message_ids) != len(messages):
            raise ValueError("message_ids must have the same length as messages")
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                now = datetime.now(timezone.utc).isoformat()
                insert_verb = "INSERT OR IGNORE" if message_ids is not None else "INSERT"
                for index, message in enumerate(messages):
                    self.connection.execute(
                        f"""
                        {insert_verb} INTO messages (id, session_scope, role, content, name, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """,
                        (
                            message_ids[index] if message_ids is not None else str(uuid.uuid4()),
                            session_scope,
                            message.get("role"),
                            message.get("content"),
                            message.get("name"),
                            now,
                        ),
                    )
                # Evict old messages beyond the most recent 10 for this scope.
                # Wrapped in a derived table to force SQLite to materialize the
                # ORDER BY before the outer NOT IN evaluates it.
                self.connection.execute(
                    """
                    DELETE FROM messages WHERE session_scope = ? AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM messages WHERE session_scope = ? ORDER BY created_at DESC LIMIT 10
                        )
                    )
                """,
                    (session_scope, session_scope),
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to save messages: {e}")
                raise

    def get_last_messages(self, session_scope: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            # Subquery picks the latest N rows (DESC + LIMIT), outer query
            # re-sorts them chronologically (ASC) for the caller.
            cur = self.connection.execute(
                """
                SELECT role, content, name, created_at FROM (
                    SELECT role, content, name, created_at
                    FROM messages
                    WHERE session_scope = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
            """,
                (session_scope, limit),
            )
            rows = cur.fetchall()

        return [
            {
                "role": r[0],
                "content": r[1],
                "name": r[2],
                "created_at": r[3],
            }
            for r in rows
        ]

    def delete_project_data(self, project_id: str, memory_ids: Iterable[str] = ()) -> Dict[str, int]:
        """Delete history and conversation context owned by one project storage scope.

        ``memory_ids`` removes legacy history rows written before project scope was persisted.
        Message matching uses scope-component boundaries, so similarly named projects are not
        affected.
        """
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        normalized_memory_ids = list(dict.fromkeys(str(item) for item in memory_ids if item))

        with self._lock:
            try:
                self.connection.execute("BEGIN")
                history_deleted = self.connection.execute(
                    "DELETE FROM history WHERE project_id = ?",
                    (normalized_project_id,),
                ).rowcount
                for offset in range(0, len(normalized_memory_ids), 500):
                    batch = normalized_memory_ids[offset : offset + 500]
                    placeholders = ", ".join("?" for _ in batch)
                    history_deleted += self.connection.execute(
                        f"DELETE FROM history WHERE memory_id IN ({placeholders})",
                        batch,
                    ).rowcount

                scope_component = f"&project_id={normalized_project_id}&"
                messages_deleted = self.connection.execute(
                    "DELETE FROM messages WHERE instr('&' || session_scope || '&', ?) > 0",
                    (scope_component,),
                ).rowcount
                self.connection.execute("COMMIT")
                return {
                    "history": history_deleted,
                    "messages": messages_deleted,
                }
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error("Failed to delete project-scoped history data: %s", e)
                raise

    def reset(self) -> None:
        """Drop both tables. Caller is expected to replace this instance."""
        if not self.connection:
            raise RuntimeError("Cannot reset a closed SQLiteManager")
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("DROP TABLE IF EXISTS history")
                self.connection.execute("DROP TABLE IF EXISTS messages")
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to reset tables: {e}")
                raise

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def __del__(self):
        self.close()
