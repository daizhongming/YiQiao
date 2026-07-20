import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - dependency may be absent until image rebuild
    GraphDatabase = None

try:
    from mem0.utils.entity_extraction import extract_entities as _mem0_extract_entities
except Exception:  # pragma: no cover - optional in older mem0 builds
    _mem0_extract_entities = None

_driver = None
_schema_ready = False
_last_error: str | None = None


class GraphBatchSyncError(RuntimeError):
    """Raised when an enabled Neo4j batch sync cannot be completed."""


_ENTITY_STOPWORDS = {
    "a",
    "an",
    "and",
    "assistant",
    "but",
    "for",
    "from",
    "i",
    "me",
    "my",
    "or",
    "the",
    "this",
    "that",
    "to",
    "user",
    "with",
}


def _enabled() -> bool:
    return os.environ.get("NEO4J_ENABLED", "false").lower() == "true"


def _database() -> str:
    return os.environ.get("NEO4J_DATABASE", "neo4j")


def _set_error(message: str, exc: Exception | None = None) -> None:
    global _last_error
    _last_error = message
    if exc is not None:
        logging.warning(message, exc_info=True)


def _get_driver():
    global _driver
    if not _enabled() or GraphDatabase is None:
        return None
    if _driver is None:
        password = os.environ.get("NEO4J_PASSWORD")
        if not password:
            _set_error("Neo4j password is not configured")
            return None
        try:
            _driver = GraphDatabase.driver(
                os.environ.get("NEO4J_URI", "bolt://neo4j:7687"),
                auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), password),
            )
        except Exception as exc:
            _set_error("Neo4j driver initialization failed", exc)
            return None
    return _driver


def _ensure_schema(session) -> None:
    global _schema_ready
    if _schema_ready:
        return
    statements = [
        "CREATE CONSTRAINT memory_id IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE",
        "CREATE CONSTRAINT entity_key IF NOT EXISTS FOR (e:Entity) REQUIRE e.key IS UNIQUE",
        "CREATE CONSTRAINT category_key IF NOT EXISTS FOR (c:Category) REQUIRE c.key IS UNIQUE",
        "CREATE INDEX memory_project IF NOT EXISTS FOR (m:Memory) ON (m.project_id)",
        "CREATE INDEX entity_project_norm IF NOT EXISTS FOR (e:Entity) ON (e.project_id, e.norm)",
        "CREATE INDEX entity_kind IF NOT EXISTS FOR (e:Entity) ON (e.kind)",
    ]
    for statement in statements:
        session.run(statement).consume()
    _schema_ready = True


def is_configured() -> bool:
    return _get_driver() is not None


def _norm(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\s_/\\-]+", " ", text)
    text = re.sub(r"[^\w .:@#]+", "", text, flags=re.UNICODE)
    return " ".join(text.split())


def _valid_entity(value: str) -> bool:
    norm = _norm(value)
    if len(norm) < 2 or len(norm) > 120:
        return False
    if norm in _ENTITY_STOPWORDS:
        return False
    if norm.isdigit():
        return False
    return True


def _fallback_entities(text: str | None) -> list[tuple[str, str]]:
    if not text:
        return []
    found: list[tuple[str, str]] = []
    patterns = [
        (r'"([^"]{2,120})"', "QUOTED"),
        (r"'([^']{2,120})'", "QUOTED"),
        (r"\u201c([^\u201d]{2,120})\u201d", "QUOTED"),
        (r"\b[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+\b", "IDENTIFIER"),
        (r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "IDENTIFIER"),
        (r"(?<!\w)[#@][A-Za-z0-9_][A-Za-z0-9_-]{1,80}\b", "IDENTIFIER"),
        (r"\b[A-Z][A-Za-z0-9]*(?:[\s-]+(?:[A-Z][A-Za-z0-9]*|[A-Z]{2,})){0,5}\b", "PROPER"),
        (r"\b(?:[a-z]+_[a-z0-9_]+|[a-z]+-[a-z0-9-]+|[A-Za-z]+[A-Z][A-Za-z0-9]*)\b", "IDENTIFIER"),
    ]
    for pattern, kind in patterns:
        for match in re.finditer(pattern, text):
            value = (match.group(1) if match.groups() else match.group(0)).strip()
            if _valid_entity(value):
                found.append((kind, value))

    seen = set()
    deduped = []
    for kind, value in found:
        key = _norm(value)
        if key and key not in seen:
            seen.add(key)
            deduped.append((kind, value))
    return deduped[:20]


def _coerce_entities(raw_entities: Any) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if not raw_entities:
        return rows
    for item in raw_entities:
        entity_type = "ENTITY"
        name = None
        if isinstance(item, dict):
            name = item.get("name") or item.get("entity") or item.get("text") or item.get("value")
            entity_type = str(item.get("type") or item.get("entity_type") or entity_type)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            entity_type = str(item[0] or entity_type)
            name = item[1]
        else:
            name = item
        if name is not None and _valid_entity(str(name)):
            rows.append((entity_type, str(name)))
    return rows


def extract_graph_entities(text: str | None) -> list[dict[str, str]]:
    entities: list[tuple[str, str]] = []
    if text and _mem0_extract_entities is not None:
        try:
            entities = _coerce_entities(_mem0_extract_entities(text))
        except Exception:
            logging.debug("memory entity extraction failed", exc_info=True)
    fallback_entities = _fallback_entities(text)
    if entities:
        entities.extend((kind, name) for kind, name in fallback_entities if kind == "IDENTIFIER")
    else:
        entities = fallback_entities
    rows = []
    seen = set()
    for entity_type, name in entities:
        norm = _norm(str(name))
        if not norm or norm in seen:
            continue
        seen.add(norm)
        rows.append({"type": str(entity_type).upper(), "name": str(name), "norm": norm, "kind": "memory"})
    return rows


def _scope_entities(entities: dict[str, str | None]) -> list[dict[str, str]]:
    rows = []
    for key, value in entities.items():
        if not value:
            continue
        name = str(value)
        rows.append(
            {
                "type": key.replace("_id", "").upper(),
                "name": name,
                "norm": _norm(name),
                "kind": "scope",
            }
        )
    return rows


def _props(entities: dict[str, str | None]) -> dict[str, str]:
    return {key: str(entities.get(key) or "") for key in ("user_id", "agent_id", "app_id", "run_id")}


def _coerce_graph_datetime(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
        return _coerce_graph_datetime(seconds)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _prepare_memory_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Neo4j batch row {index} must be a mapping")

        memory_id = str(row.get("memory_id") or "").strip()
        if not memory_id:
            raise ValueError(f"Neo4j batch row {index} is missing memory_id")

        scope = row.get("entities") or {}
        if not isinstance(scope, dict):
            raise ValueError(f"Neo4j batch row {index} entities must be a mapping")
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"Neo4j batch row {index} metadata must be a mapping")

        text = row.get("text")
        project_id = str(metadata.get("project_id") or "default-project")
        raw_category = metadata.get("categories") or metadata.get("category")
        categories = raw_category if isinstance(raw_category, list) else [raw_category] if raw_category else []
        prepared.append(
            {
                "memory_id": memory_id,
                "text": text,
                "project_id": project_id,
                "created_at": _coerce_graph_datetime(metadata.get("created_at") or metadata.get("source_created_at")),
                "entities": _scope_entities(scope) + extract_graph_entities(text),
                "categories": [str(category) for category in categories if category],
                **_props(scope),
            }
        )
    return prepared


_BATCH_MEMORY_QUERY = """
UNWIND $rows AS row
MERGE (m:Memory {id: row.memory_id})
SET m.text = row.text,
    m.project_id = row.project_id,
    m.user_id = row.user_id,
    m.agent_id = row.agent_id,
    m.app_id = row.app_id,
    m.run_id = row.run_id,
    m.created_at = coalesce(m.created_at, CASE WHEN row.created_at IS NULL THEN datetime() ELSE datetime(row.created_at) END),
    m.source_created_at = row.created_at,
    m.updated_at = datetime()
WITH collect(DISTINCT m) AS memories
UNWIND memories AS memory
OPTIONAL MATCH (memory)-[old_out:MENTIONS|IN_CATEGORY|RELATED_TO]->()
WITH memories, collect(DISTINCT old_out) AS old_outgoing
FOREACH (relationship IN old_outgoing | DELETE relationship)
WITH memories
UNWIND memories AS memory
OPTIONAL MATCH ()-[old_in:HAS_MEMORY|RELATED_TO]->(memory)
WITH collect(DISTINCT old_in) AS old_incoming
FOREACH (relationship IN old_incoming | DELETE relationship)
"""

_BATCH_ENTITY_QUERY = """
UNWIND $rows AS row
MATCH (m:Memory {id: row.memory_id})
UNWIND row.entities AS entity
MERGE (e:Entity {key: row.project_id + ':' + entity.kind + ':' + entity.norm})
SET e.type = entity.type,
    e.name = entity.name,
    e.norm = entity.norm,
    e.kind = entity.kind,
    e.project_id = row.project_id,
    e.updated_at = datetime(),
    e.created_at = coalesce(e.created_at, datetime())
MERGE (m)-[:MENTIONS]->(e)
MERGE (e)-[:HAS_MEMORY]->(m)
"""

_BATCH_CATEGORY_QUERY = """
UNWIND $rows AS row
MATCH (m:Memory {id: row.memory_id})
UNWIND row.categories AS category
MERGE (c:Category {key: row.project_id + ':' + category})
SET c.name = category, c.project_id = row.project_id
MERGE (m)-[:IN_CATEGORY]->(c)
"""

_BATCH_RELATED_QUERY = """
UNWIND $rows AS row
MATCH (m:Memory {id: row.memory_id, project_id: row.project_id})-[:MENTIONS]->(e:Entity {kind: 'memory'})
MATCH (e)-[:HAS_MEMORY]->(other:Memory {project_id: row.project_id})
WHERE other.id <> m.id
  AND (
    (m.user_id <> '' AND m.user_id = other.user_id)
    OR (m.agent_id <> '' AND m.agent_id = other.agent_id)
    OR (m.app_id <> '' AND m.app_id = other.app_id)
    OR (m.run_id <> '' AND m.run_id = other.run_id)
    OR (m.user_id = '' AND m.agent_id = '' AND m.app_id = '' AND m.run_id = ''
        AND other.user_id = '' AND other.agent_id = '' AND other.app_id = '' AND other.run_id = '')
  )
WITH m, other, collect(DISTINCT e.norm) AS entity_norms
MERGE (m)-[r:RELATED_TO]->(other)
SET r.entities = entity_norms, r.weight = size(entity_norms), r.updated_at = datetime()
MERGE (other)-[r2:RELATED_TO]->(m)
SET r2.entities = entity_norms, r2.weight = size(entity_norms), r2.updated_at = datetime()
"""


def _write_memory_batch(transaction, rows: list[dict[str, Any]]) -> int:
    for query in (_BATCH_MEMORY_QUERY, _BATCH_ENTITY_QUERY, _BATCH_CATEGORY_QUERY, _BATCH_RELATED_QUERY):
        transaction.run(query, rows=rows).consume()
    return len(rows)


def upsert_memories_batch(rows: list[dict[str, Any]]) -> int:
    """Synchronize memory graph updates in one managed write transaction."""
    if not _enabled():
        return 0

    try:
        prepared = _prepare_memory_batch(rows)
        if not prepared:
            return 0

        driver = _get_driver()
        if driver is None:
            raise RuntimeError(_last_error or "Neo4j driver is unavailable")

        with driver.session(database=_database()) as session:
            _ensure_schema(session)
            return int(session.execute_write(_write_memory_batch, prepared))
    except GraphBatchSyncError:
        raise
    except Exception as exc:
        message = f"Neo4j graph batch upsert failed: {exc}"
        _set_error(message, exc)
        raise GraphBatchSyncError(message) from exc


def upsert_memory(memory_id: str, text: str | None, entities: dict[str, str | None], metadata: dict[str, Any] | None):
    driver = _get_driver()
    if driver is None or not memory_id:
        return

    entity_rows = _scope_entities(entities) + extract_graph_entities(text)
    project_id = str((metadata or {}).get("project_id") or "default-project")
    raw_category = (metadata or {}).get("categories") or (metadata or {}).get("category")
    categories = raw_category if isinstance(raw_category, list) else [raw_category] if raw_category else []
    props = _props(entities)
    created_at = _coerce_graph_datetime((metadata or {}).get("created_at") or (metadata or {}).get("source_created_at"))

    try:
        with driver.session(database=_database()) as session:
            _ensure_schema(session)
            session.run(
                """
                MERGE (m:Memory {id: $memory_id})
                SET m.text = $text,
                    m.project_id = $project_id,
                    m.user_id = $user_id,
                    m.agent_id = $agent_id,
                    m.app_id = $app_id,
                    m.run_id = $run_id,
                    m.created_at = coalesce(m.created_at, CASE WHEN $created_at IS NULL THEN datetime() ELSE datetime($created_at) END),
                    m.source_created_at = $created_at,
                    m.updated_at = datetime()
                WITH m
                OPTIONAL MATCH (m)-[old_out:MENTIONS|IN_CATEGORY|RELATED_TO]->()
                DELETE old_out
                WITH m
                OPTIONAL MATCH ()-[old_in:HAS_MEMORY|RELATED_TO]->(m)
                DELETE old_in
                """,
                memory_id=memory_id,
                text=text,
                project_id=project_id,
                created_at=created_at,
                **props,
            )
            session.run(
                """
                MATCH (m:Memory {id: $memory_id})
                WITH m
                UNWIND $entities AS entity
                MERGE (e:Entity {key: $project_id + ':' + entity.kind + ':' + entity.norm})
                SET e.type = entity.type,
                    e.name = entity.name,
                    e.norm = entity.norm,
                    e.kind = entity.kind,
                    e.project_id = $project_id,
                    e.updated_at = datetime(),
                    e.created_at = coalesce(e.created_at, datetime())
                MERGE (m)-[:MENTIONS]->(e)
                MERGE (e)-[:HAS_MEMORY]->(m)
                """,
                memory_id=memory_id,
                project_id=project_id,
                entities=entity_rows,
            )
            session.run(
                """
                MATCH (m:Memory {id: $memory_id})
                WITH m
                UNWIND $categories AS category
                MERGE (c:Category {key: $project_id + ':' + category})
                SET c.name = category, c.project_id = $project_id
                MERGE (m)-[:IN_CATEGORY]->(c)
                """,
                memory_id=memory_id,
                project_id=project_id,
                categories=[str(category) for category in categories if category],
            )
            session.run(
                """
                MATCH (m:Memory {id: $memory_id, project_id: $project_id})-[:MENTIONS]->(e:Entity {kind: 'memory'})
                MATCH (e)-[:HAS_MEMORY]->(other:Memory {project_id: $project_id})
                WHERE other.id <> m.id
                  AND (
                    (m.user_id <> '' AND m.user_id = other.user_id)
                    OR (m.agent_id <> '' AND m.agent_id = other.agent_id)
                    OR (m.app_id <> '' AND m.app_id = other.app_id)
                    OR (m.run_id <> '' AND m.run_id = other.run_id)
                    OR (m.user_id = '' AND m.agent_id = '' AND m.app_id = '' AND m.run_id = ''
                        AND other.user_id = '' AND other.agent_id = '' AND other.app_id = '' AND other.run_id = '')
                  )
                WITH m, other, collect(DISTINCT e.norm) AS entity_norms
                MERGE (m)-[r:RELATED_TO]->(other)
                SET r.entities = entity_norms, r.weight = size(entity_norms), r.updated_at = datetime()
                MERGE (other)-[r2:RELATED_TO]->(m)
                SET r2.entities = entity_norms, r2.weight = size(entity_norms), r2.updated_at = datetime()
                """,
                memory_id=memory_id,
                project_id=project_id,
            )
    except Exception as exc:
        _set_error("Neo4j graph upsert failed", exc)


def _prune_orphans(session, project_id: str) -> None:
    session.run(
        """
        MATCH (e:Entity {project_id: $project_id})
        WHERE NOT (e)-[:HAS_MEMORY]->(:Memory {project_id: $project_id})
        DETACH DELETE e
        """,
        project_id=project_id,
    )
    session.run(
        """
        MATCH (c:Category {project_id: $project_id})
        WHERE NOT (:Memory {project_id: $project_id})-[:IN_CATEGORY]->(c)
        DETACH DELETE c
        """,
        project_id=project_id,
    )


def delete_memory(memory_id: str):
    driver = _get_driver()
    if driver is None:
        return
    try:
        with driver.session(database=_database()) as session:
            project_row = session.run(
                "MATCH (m:Memory {id: $memory_id}) RETURN m.project_id AS project_id", memory_id=memory_id
            ).single()
            project_id = str(project_row["project_id"] or "default-project") if project_row else "default-project"
            session.run("MATCH (m:Memory {id: $memory_id}) DETACH DELETE m", memory_id=memory_id)
            _prune_orphans(session, project_id)
    except Exception as exc:
        _set_error("Neo4j graph delete failed", exc)


def delete_memories(project_id: str, filters: dict[str, Any]):
    driver = _get_driver()
    if driver is None:
        return
    props = {key: filters.get(key) for key in ("user_id", "agent_id", "app_id", "run_id")}
    try:
        with driver.session(database=_database()) as session:
            session.run(
                """
                MATCH (m:Memory {project_id: $project_id})
                WHERE ($user_id IS NULL OR m.user_id = $user_id)
                  AND ($agent_id IS NULL OR m.agent_id = $agent_id)
                  AND ($app_id IS NULL OR m.app_id = $app_id)
                  AND ($run_id IS NULL OR m.run_id = $run_id)
                DETACH DELETE m
                """,
                project_id=project_id,
                **props,
            )
            _prune_orphans(session, project_id)
    except Exception as exc:
        _set_error("Neo4j graph bulk delete failed", exc)


def related_memories(query: str, project_id: str, filters: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    driver = _get_driver()
    if driver is None:
        return []
    norms = [item["norm"] for item in extract_graph_entities(query)]
    if not norms:
        return []
    props = {key: filters.get(key) for key in ("user_id", "agent_id", "app_id", "run_id")}
    try:
        with driver.session(database=_database()) as session:
            rows = session.run(
                """
                MATCH (e:Entity {project_id: $project_id, kind: 'memory'})-[:HAS_MEMORY]->(m:Memory {project_id: $project_id})
                WHERE any(norm IN $norms WHERE e.norm = norm OR (size(norm) >= 3 AND e.norm CONTAINS norm) OR (size(e.norm) >= 3 AND norm CONTAINS e.norm))
                  AND ($user_id IS NULL OR m.user_id = $user_id)
                  AND ($agent_id IS NULL OR m.agent_id = $agent_id)
                  AND ($app_id IS NULL OR m.app_id = $app_id)
                  AND ($run_id IS NULL OR m.run_id = $run_id)
                WITH m,
                     collect(DISTINCT e.name) AS matched_entities,
                     count(DISTINCT e.norm) AS hits,
                     sum(CASE WHEN e.norm IN $norms THEN 2 ELSE 1 END) AS strength
                OPTIONAL MATCH (m)-[rel:RELATED_TO]->(:Memory {project_id: $project_id})
                RETURN m.id AS id,
                       m.text AS text,
                       matched_entities AS matched_entities,
                       hits AS hits,
                       strength AS strength,
                       count(DISTINCT rel) AS shared_degree
                ORDER BY strength DESC, shared_degree DESC, hits DESC
                LIMIT $limit
                """,
                project_id=project_id,
                norms=norms,
                limit=limit,
                **props,
            )
            return [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "matched_entities": row["matched_entities"],
                    "boost": min(0.4, 0.10 * int(row["hits"] or 1) + 0.02 * min(int(row["shared_degree"] or 0), 5)),
                }
                for row in rows
            ]
    except Exception as exc:
        _set_error("Neo4j related memory query failed", exc)
        return []


def graph_status(project_id: str = "default-project") -> dict[str, Any]:
    driver = _get_driver()
    if driver is None:
        return {
            "configured": False,
            "enabled": _enabled(),
            "reachable": False,
            "last_error": _last_error,
        }
    try:
        with driver.session(database=_database()) as session:
            _ensure_schema(session)
            session.run("RETURN 1").consume()
            counts = session.run(
                """
                WITH $project_id AS project_id
                CALL (project_id) {
                    MATCH (m:Memory {project_id: project_id})
                    RETURN count(m) AS memories
                }
                CALL (project_id) {
                    MATCH (e:Entity {project_id: project_id, kind: 'memory'})
                    RETURN count(e) AS entities
                }
                CALL (project_id) {
                    MATCH (:Memory {project_id: project_id})-[r:RELATED_TO]->(:Memory {project_id: project_id})
                    RETURN count(r) AS relationships
                }
                RETURN memories, entities, relationships
                """,
                project_id=project_id,
            ).single()
            return {
                "configured": True,
                "enabled": True,
                "reachable": True,
                "project_id": project_id,
                "memories": int(counts["memories"] or 0) if counts else 0,
                "entities": int(counts["entities"] or 0) if counts else 0,
                "relationships": int(counts["relationships"] or 0) if counts else 0,
                "last_error": _last_error,
            }
    except Exception as exc:
        _set_error("Neo4j status query failed", exc)
        return {"configured": True, "enabled": True, "reachable": False, "last_error": _last_error}


def list_graph_entities(project_id: str = "default-project", limit: int = 100) -> list[dict[str, Any]]:
    driver = _get_driver()
    if driver is None:
        return []
    try:
        with driver.session(database=_database()) as session:
            rows = session.run(
                """
                MATCH (e:Entity {project_id: $project_id, kind: 'memory'})-[:HAS_MEMORY]->(m:Memory {project_id: $project_id})
                RETURN e.name AS name,
                       e.norm AS norm,
                       e.type AS type,
                       count(DISTINCT m) AS memory_count
                ORDER BY memory_count DESC, name ASC
                LIMIT $limit
                """,
                project_id=project_id,
                limit=limit,
            )
            return [
                {
                    "name": row["name"],
                    "norm": row["norm"],
                    "type": row["type"],
                    "memory_count": int(row["memory_count"] or 0),
                }
                for row in rows
            ]
    except Exception as exc:
        _set_error("Neo4j entity list failed", exc)
        return []


def fetch_neighbors(memory_id: str, project_id: str = "default-project", limit: int = 50) -> dict[str, Any]:
    driver = _get_driver()
    if driver is None:
        return {"configured": False, "memory_id": memory_id, "neighbors": []}
    try:
        with driver.session(database=_database()) as session:
            rows = session.run(
                """
                MATCH (m:Memory {id: $memory_id, project_id: $project_id})-[r:RELATED_TO]->(other:Memory {project_id: $project_id})
                RETURN other.id AS id, other.text AS text, r.entities AS entities, r.weight AS weight
                ORDER BY coalesce(r.weight, 0) DESC
                LIMIT $limit
                """,
                memory_id=memory_id,
                project_id=project_id,
                limit=limit,
            )
            return {
                "configured": True,
                "memory_id": memory_id,
                "neighbors": [
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "entities": row["entities"] or [],
                        "weight": int(row["weight"] or 0),
                    }
                    for row in rows
                ],
            }
    except Exception as exc:
        _set_error("Neo4j neighbor query failed", exc)
        return {"configured": True, "memory_id": memory_id, "neighbors": [], "error": _last_error}


def fetch_graph(project_id: str = "default-project", limit: int = 200) -> dict[str, Any]:
    driver = _get_driver()
    if driver is None:
        return {"configured": False, "nodes": [], "edges": [], "status": graph_status(project_id)}

    nodes: dict[str, dict[str, str]] = {}
    edges: list[dict[str, str]] = []
    try:
        with driver.session(database=_database()) as session:
            _ensure_schema(session)
            rows = session.run(
                """
                MATCH (a)-[r]->(b)
                WHERE type(r) IN ['MENTIONS', 'IN_CATEGORY', 'RELATED_TO']
                  AND coalesce(a.project_id, 'default-project') = $project_id
                  AND coalesce(b.project_id, 'default-project') = $project_id
                RETURN labels(a)[0] AS source_label,
                       coalesce(a.key, a.id, a.name) AS source_id,
                       coalesce(a.name, a.id, a.key) AS source_title,
                       coalesce(a.kind, '') AS source_kind,
                       labels(b)[0] AS target_label,
                       coalesce(b.key, b.id, b.name) AS target_id,
                       coalesce(b.name, b.id, b.key) AS target_title,
                       coalesce(b.kind, '') AS target_kind,
                       type(r) AS type,
                       coalesce(properties(r).weight, 1) AS weight
                LIMIT $limit
                """,
                project_id=project_id,
                limit=limit,
            )
            for row in rows:
                source = str(row["source_id"])
                target = str(row["target_id"])
                nodes[source] = {
                    "id": source,
                    "label": row["source_label"],
                    "title": str(row["source_title"] or source),
                    "kind": str(row["source_kind"] or ""),
                }
                nodes[target] = {
                    "id": target,
                    "label": row["target_label"],
                    "title": str(row["target_title"] or target),
                    "kind": str(row["target_kind"] or ""),
                }
                edges.append(
                    {
                        "source": source,
                        "target": target,
                        "type": row["type"],
                        "weight": str(row["weight"] or "1"),
                    }
                )
    except Exception as exc:
        _set_error("Neo4j graph fetch failed", exc)
        return {"configured": True, "nodes": [], "edges": [], "error": _last_error, "status": graph_status(project_id)}

    return {"configured": True, "nodes": list(nodes.values()), "edges": edges, "status": graph_status(project_id)}
