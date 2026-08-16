# This file was modified in 2026 by YiQiao contributors. See NOTICE.

"""Re-embed existing PGVector memories with the active YiQiao embedder.

The command is intentionally conservative:

* without ``--apply`` it only scans rows and prints a JSON summary;
* ``--apply`` requires an explicit project scope and writes one transaction per
  batch;
* the old vectors are flushed to a JSONL backup before each batch is changed;
* ``--rollback BACKUP --apply`` restores vectors from that backup.

Run this inside the API container so it uses the same environment and provider
configuration as the running service, for example::

    docker compose exec yiqiao python scripts/reembed_memories.py \
      --project-id boss-helper --user-id daz --limit 1 --apply

The script never prints provider configuration or API keys.  It only supports
the PGVector backend because the backup/recovery contract needs access to the
stored vector values themselves.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_PROJECT_ID = "default-project"
DEFAULT_BATCH_SIZE = 50
DEFAULT_LIMIT = 1000
MAX_BATCH_SIZE = 1000
MAX_LIMIT = 1_000_000
_VECTOR_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class ReembedUsageError(ValueError):
    """An invalid command or unsupported runtime configuration."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_scope_id(value: str, name: str) -> str:
    candidate = value.strip()
    if not candidate or not _SAFE_ID.fullmatch(candidate):
        raise ReembedUsageError(f"{name} must contain only letters, numbers, dots, underscores, or hyphens.")
    return candidate


def _parse_vector(value: Any) -> list[float] | None:
    """Parse a pgvector value returned by psycopg without using eval.

    A NULL vector is a valid lexical-only record created while the embedding
    provider is unavailable. It is kept as ``None`` so the re-embed command can
    later fill it in and rollback can restore it safely.
    """

    if value is None:
        return None

    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if len(text) < 2 or text[0] != "[" or text[-1] != "]":
            raise ValueError("stored vector is not a bracketed pgvector value")
        body = text[1:-1].strip()
        raw_values = [] if not body else body.split(",")
    else:
        raise ValueError("stored vector has an unsupported type")

    vector: list[float] = []
    for raw in raw_values:
        if isinstance(raw, bool):
            raise ValueError("stored vector contains a boolean")
        token = str(raw).strip()
        if not _VECTOR_NUMBER.fullmatch(token):
            raise ValueError("stored vector contains a non-numeric value")
        number = float(token)
        if not math.isfinite(number):
            raise ValueError("stored vector contains a non-finite value")
        vector.append(number)
    if not vector:
        raise ValueError("stored vector is empty")
    return vector


def _safe_error(exc: BaseException) -> dict[str, str]:
    """Return a useful failure classification without persisting exception text."""

    # Provider exceptions can include request URLs, headers, or query strings.
    # Keep failure manifests free of those values; the type is enough to decide
    # whether a batch should be retried.
    return {"type": type(exc).__name__, "message": "operation failed; inspect container logs for details"}


def _scope_filters(project_id: str, user_id: str | None) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if project_id == DEFAULT_PROJECT_ID:
        # The API treats rows without a project_id as belonging to the default
        # project. Preserve that compatibility rule during migration.
        filters["$or"] = [
            {"project_id": project_id},
            {"$not": [{"project_id": "*"}]},
        ]
    else:
        filters["project_id"] = project_id
    if user_id:
        filters["user_id"] = user_id
    return filters


def _unwrap_rows(result: Any) -> list[Any]:
    if isinstance(result, (list, tuple)) and len(result) == 1 and isinstance(result[0], (list, tuple)):
        return list(result[0])
    return list(result or []) if isinstance(result, (list, tuple)) else []


def _config_fingerprint(config: Any, dimensions: int | None) -> dict[str, Any]:
    """Expose only non-secret embedder identity in manifests and summaries."""

    section = config.get("embedder") if isinstance(config, dict) else {}
    section = section if isinstance(section, dict) else {}
    section_config = section.get("config") if isinstance(section.get("config"), dict) else {}
    return {
        "provider": str(section.get("provider") or ""),
        "model": str(section_config.get("model") or ""),
        "dimensions": dimensions,
    }


def _default_backup_path() -> Path:
    history_path = Path(os.environ.get("HISTORY_DB_PATH", "/app/history/history.db"))
    root = Path(os.environ.get("YIQIAO_REEMBED_BACKUP_DIR", str(history_path.parent / "reembed-backups")))
    return root / f"reembed-{_utc_stamp()}-{uuid.uuid4().hex[:8]}.jsonl"


def _set_private_mode(path: Path) -> None:
    try:
        # Best effort on Windows; this is effective on Linux containers.
        path.chmod(0o600)
    except OSError:
        pass


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "a"
    with path.open(mode, encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _set_private_mode(path)


def _read_backup(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file():
        raise ReembedUsageError(f"backup file does not exist: {path}")
    header: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReembedUsageError(f"invalid JSON in backup line {line_number}") from exc
            if header is None:
                if not isinstance(item, dict):
                    raise ReembedUsageError(f"backup header on line {line_number} must be a JSON object")
                if item.get("kind") != "yiqiao-reembed-backup":
                    raise ReembedUsageError("backup header is missing or has an unsupported kind")
                if item.get("version") != 1:
                    raise ReembedUsageError("backup has an unsupported version")
                if not isinstance(item.get("collection"), str) or not item["collection"]:
                    raise ReembedUsageError("backup header has no collection")
                header = item
                continue
            if not isinstance(item, dict):
                raise ReembedUsageError(f"backup row on line {line_number} must be a JSON object")
            memory_id = str(item.get("id") or "")
            if not memory_id:
                raise ReembedUsageError(f"backup line {line_number} has no memory id")
            try:
                uuid.UUID(memory_id)
            except ValueError as exc:
                raise ReembedUsageError(f"backup line {line_number} has an invalid memory id") from exc
            vector = _parse_vector(item.get("vector"))
            if any(row["id"] == memory_id for row in rows):
                raise ReembedUsageError(f"backup line {line_number} repeats memory id")
            rows.append({"id": memory_id, "vector": vector})
    if header is None:
        raise ReembedUsageError("backup file is empty")
    return header, rows


def _require_pgvector(memory: Any) -> Any:
    store = getattr(memory, "vector_store", None)
    if store is None or not callable(getattr(store, "_get_cursor", None)) or not callable(getattr(store, "_col", None)):
        raise ReembedUsageError("re-embedding currently requires the PGVector backend")
    if not callable(getattr(store, "update", None)):
        raise ReembedUsageError("the configured vector store cannot update vectors")
    return store


def _query_rows(store: Any, filters: dict[str, Any], after_id: str | None, batch_size: int) -> list[dict[str, Any]]:
    """Read IDs, vectors, and text with a keyset query.

    The query uses the vector store's own filter compiler and quoted table
    identifier. No payload or vector is printed by this command.
    """

    from mem0.vector_stores.pgvector import _build_filter_conditions, sql

    conditions, params = _build_filter_conditions(filters)
    if after_id:
        conditions.append("id > %s::uuid")
        params.append(after_id)
    where = sql.SQL("WHERE " + " AND ".join(conditions)) if conditions else sql.SQL("")
    statement = sql.SQL("SELECT id, vector::text, payload->>'data' FROM {} {} ORDER BY id LIMIT %s").format(
        store._col(), where
    )
    with store._get_cursor() as cursor:
        cursor.execute(statement, (*params, batch_size))
        raw_rows = cursor.fetchall()
    rows: list[dict[str, Any]] = []
    for memory_id, vector, text in raw_rows:
        rows.append(
            {"id": str(memory_id), "vector": _parse_vector(vector), "text": text if isinstance(text, str) else ""}
        )
    return rows


def _update_vectors(store: Any, vectors: Sequence[dict[str, Any]]) -> None:
    """Update one batch atomically, without changing payload or memory history.

    Re-embedding rows carry their scanned vector as ``expected_vector``. The
    conditional update turns a concurrent API write into a failed batch instead
    of silently replacing the newer vector. Rollback rows intentionally omit
    that field and restore unconditionally from the operator-selected backup.
    """

    from mem0.vector_stores.pgvector import sql

    with store._get_cursor(commit=True) as cursor:
        for item in vectors:
            has_expected_vector = "expected_vector" in item
            expected_vector = item.get("expected_vector")
            if not has_expected_vector:
                statement = sql.SQL("UPDATE {} SET vector = %s WHERE id = %s::uuid").format(store._col())
                parameters = (item["vector"], item["id"])
            elif expected_vector is None:
                statement = sql.SQL("UPDATE {} SET vector = %s WHERE id = %s::uuid AND vector IS NULL").format(
                    store._col()
                )
                parameters = (item["vector"], item["id"])
            else:
                statement = sql.SQL("UPDATE {} SET vector = %s WHERE id = %s::uuid AND vector = %s").format(
                    store._col()
                )
                parameters = (item["vector"], item["id"], expected_vector)
            cursor.execute(statement, parameters)
            if cursor.rowcount != 1:
                raise ValueError(f"memory id {item['id']} changed or was not found during update")


def _get_current_vectors(store: Any, ids: Sequence[str]) -> list[dict[str, Any]]:
    from mem0.vector_stores.pgvector import sql

    if not ids:
        return []
    statement = sql.SQL("SELECT id, vector::text FROM {} WHERE id = ANY(%s::uuid[])").format(store._col())
    with store._get_cursor() as cursor:
        cursor.execute(statement, (list(ids),))
        return [{"id": str(memory_id), "vector": _parse_vector(vector)} for memory_id, vector in cursor.fetchall()]


def _embed_rows(memory: Any, rows: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    texts = [row["text"] for row in rows]
    if any(not text.strip() for text in texts):
        failed = [
            {"id": row["id"], "error": {"type": "MissingMemoryText", "message": "memory has no text content"}}
            for row in rows
            if not row["text"].strip()
        ]
        rows = [row for row in rows if row["text"].strip()]
        texts = [row["text"] for row in rows]
    else:
        failed = []
    if not rows:
        return [], failed

    embedder = getattr(memory, "embedding_model", None)
    if embedder is None:
        raise ReembedUsageError("the active memory instance has no embedding model")
    try:
        embeddings = list(embedder.embed_batch(texts, "update"))
        if len(embeddings) != len(rows):
            raise ValueError("embed_batch returned an unexpected number of vectors")
    except Exception:
        # A provider may reject a large batch. Retry one item at a time so a
        # transient/problematic row is recorded without losing good rows.
        embeddings = []
        for row in rows:
            try:
                embeddings.append(embedder.embed(row["text"], "update"))
            except Exception as exc:
                failed.append({"id": row["id"], "error": _safe_error(exc)})
                embeddings.append(None)

    expected_dims = getattr(getattr(memory, "vector_store", None), "embedding_model_dims", None)
    updated: list[dict[str, Any]] = []
    for row, embedding in zip(rows, embeddings):
        if embedding is None:
            continue
        try:
            vector = _parse_vector(embedding)
            if expected_dims is not None and len(vector) != int(expected_dims):
                raise ValueError(f"embedding dimension {len(vector)} does not match configured dimension")
            updated.append({"id": row["id"], "vector": vector, "expected_vector": row["vector"]})
        except Exception as exc:
            failed.append({"id": row["id"], "error": _safe_error(exc)})
    return updated, failed


def _summary(
    *, mode: str, project_id: str | None, user_id: str | None, scanned: int, updated: int, failed: int
) -> dict[str, Any]:
    return {
        "mode": mode,
        "project_id": project_id,
        "user_id": user_id,
        "scanned": scanned,
        "updated": updated,
        "failed": failed,
    }


def reembed(args: argparse.Namespace) -> int:
    if not args.project_id:
        raise ReembedUsageError("--project-id is required for re-embedding")
    project_id = _safe_scope_id(args.project_id, "project id")
    user_id = _safe_scope_id(args.user_id, "user id") if args.user_id else None

    # Importing main is deliberately delayed so --help and parser tests do not
    # initialize a database or provider client.
    # ``main`` performs the same runtime initialization as the API process.
    __import__("main")
    from server_state import get_current_config, get_memory_instance

    memory = get_memory_instance()
    store = _require_pgvector(memory)
    dimensions = getattr(store, "embedding_model_dims", None)
    collection = getattr(store, "collection_name", None)
    if not isinstance(collection, str) or not collection:
        raise ReembedUsageError("the configured vector store has no collection name")
    fingerprint = _config_fingerprint(get_current_config(), dimensions)
    filters = _scope_filters(project_id, user_id)
    backup_path = Path(args.backup) if args.backup else _default_backup_path()
    failures_path = (
        Path(args.failures) if args.failures else backup_path.with_suffix(backup_path.suffix + ".failures.jsonl")
    )

    if args.apply:
        if backup_path.exists() and backup_path.stat().st_size:
            raise ReembedUsageError(f"backup already exists; choose a new path: {backup_path}")
        if backup_path.resolve() == failures_path.resolve():
            raise ReembedUsageError("--backup and --failures must use different paths")
        if failures_path.exists() and failures_path.stat().st_size:
            raise ReembedUsageError(f"failure file already exists; choose a new path: {failures_path}")
        _write_jsonl(
            backup_path,
            [
                {
                    "kind": "yiqiao-reembed-backup",
                    "version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project_id": project_id,
                    "user_id": user_id,
                    "collection": collection,
                    "embedder": fingerprint,
                }
            ],
            exclusive=True,
        )

    scanned = updated = failed_count = 0
    after_id: str | None = None
    remaining = args.limit if args.limit > 0 else None
    sample_ids: list[str] = []
    while remaining is None or remaining > 0:
        fetch_size = min(args.batch_size, remaining) if remaining is not None else args.batch_size
        rows = _query_rows(store, filters, after_id, fetch_size)
        if not rows:
            break
        scanned += len(rows)
        if len(sample_ids) < 5:
            sample_ids.extend(row["id"] for row in rows[: 5 - len(sample_ids)])
        after_id = rows[-1]["id"]
        if remaining is not None:
            remaining -= len(rows)

        if not args.apply:
            continue

        # Persist the exact old vectors before contacting the provider or
        # changing the database. A crash after this point is recoverable.
        _write_jsonl(backup_path, ({"id": row["id"], "vector": row["vector"]} for row in rows))
        new_vectors, failures = _embed_rows(memory, rows)
        if failures:
            _write_jsonl(
                failures_path,
                ({"id": item["id"], "error": item["error"]} for item in failures),
            )
            failed_count += len(failures)
        if new_vectors:
            try:
                _update_vectors(store, new_vectors)
                updated += len(new_vectors)
            except Exception as exc:
                # The transaction in _update_vectors rolls back the whole
                # batch. Record every candidate as failed; the backup remains
                # valid for a later rollback.
                batch_failures = [{"id": item["id"], "error": _safe_error(exc)} for item in new_vectors]
                _write_jsonl(failures_path, batch_failures)
                failed_count += len(batch_failures)

    result = _summary(
        mode="apply" if args.apply else "dry-run",
        project_id=project_id,
        user_id=user_id,
        scanned=scanned,
        updated=updated,
        failed=failed_count,
    )
    result["embedder"] = fingerprint
    result["sample_ids"] = sample_ids
    if args.apply:
        result["backup"] = str(backup_path)
        if failed_count:
            result["failures"] = str(failures_path)
    # Keep output machine-readable and free of provider configuration.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if not failed_count else 1


def rollback(args: argparse.Namespace) -> int:
    header, rows = _read_backup(Path(args.rollback))
    if not rows:
        print(json.dumps({"mode": "rollback", "restored": 0}, separators=(",", ":")))
        return 0
    __import__("main")
    from server_state import get_memory_instance

    memory = get_memory_instance()
    store = _require_pgvector(memory)
    collection = getattr(store, "collection_name", None)
    if header.get("collection") != collection:
        raise ReembedUsageError("backup collection does not match the active PGVector collection")
    expected_dims = getattr(store, "embedding_model_dims", None)
    for row in rows:
        if row["vector"] is not None and expected_dims is not None and len(row["vector"]) != int(expected_dims):
            raise ReembedUsageError(f"backup vector dimension does not match active store for {row['id']}")

    safety_path = None
    if args.apply:
        safety_path = Path(args.rollback).with_name(Path(args.rollback).stem + f".pre-rollback-{_utc_stamp()}.jsonl")
        current = _get_current_vectors(store, [row["id"] for row in rows])
        _write_jsonl(
            safety_path,
            [
                {
                    "kind": "yiqiao-reembed-backup",
                    "version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "project_id": header.get("project_id"),
                    "user_id": header.get("user_id"),
                    "collection": collection,
                    "embedder": header.get("embedder"),
                },
                *current,
            ],
            exclusive=True,
        )

    restored = 0
    failed: list[dict[str, Any]] = []
    for offset in range(0, len(rows), args.batch_size):
        batch = rows[offset : offset + args.batch_size]
        if not args.apply:
            continue
        try:
            _update_vectors(store, batch)
            restored += len(batch)
        except Exception as exc:
            error = _safe_error(exc)
            failed.extend({"id": row["id"], "error": error} for row in batch)

    result = {
        "mode": "rollback-apply" if args.apply else "rollback-dry-run",
        "scanned": len(rows),
        "restored": restored,
        "would_restore": len(rows) if not args.apply else 0,
        "failed": len(failed),
    }
    if safety_path is not None:
        result["safety_backup"] = str(safety_path)
    if failed:
        failure_path = (
            Path(args.failures)
            if args.failures
            else Path(args.rollback).with_suffix(Path(args.rollback).suffix + ".rollback-failures.jsonl")
        )
        if failure_path.resolve() == Path(args.rollback).resolve():
            raise ReembedUsageError("--failures must use a different path from --rollback")
        _write_jsonl(failure_path, failed)
        result["failures"] = str(failure_path)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if not failed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write vectors; without this flag the command is dry-run")
    parser.add_argument("--rollback", metavar="BACKUP", help="restore vectors from a backup JSONL file")
    parser.add_argument("--project-id", help="project scope (required for re-embedding)")
    parser.add_argument("--user-id", help="optional user scope")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="maximum rows; 0 means no limit")
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="rows per transaction/provider batch"
    )
    parser.add_argument("--backup", help="backup JSONL path (apply mode only)")
    parser.add_argument("--failures", help="failure JSONL path")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.limit < 0 or args.limit > MAX_LIMIT:
        raise ReembedUsageError(f"--limit must be between 0 and {MAX_LIMIT}")
    if args.batch_size < 1 or args.batch_size > MAX_BATCH_SIZE:
        raise ReembedUsageError(f"--batch-size must be between 1 and {MAX_BATCH_SIZE}")
    if args.rollback and (args.project_id or args.user_id):
        raise ReembedUsageError("--rollback cannot be combined with project or user scope")
    if args.rollback and args.backup:
        raise ReembedUsageError("--backup is only used for re-embedding, not rollback")
    if args.rollback:
        return
    if not args.project_id:
        raise ReembedUsageError("--project-id is required for re-embedding")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        _validate_args(args)
        if args.rollback:
            return rollback(args)
        return reembed(args)
    except ReembedUsageError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print(json.dumps({"mode": "interrupted"}, separators=(",", ":")), file=sys.stderr)
        return 130
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
