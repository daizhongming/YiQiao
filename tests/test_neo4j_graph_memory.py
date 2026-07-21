from unittest.mock import MagicMock

import pytest

import server.neo4j_graph as neo4j_graph
from server.neo4j_graph import (
    GraphBatchSyncError,
    _prune_orphans,
    extract_graph_entities,
    graph_status,
    related_memories,
    upsert_memories_batch,
)


def test_fallback_entity_extraction_captures_proper_identifiers_and_quotes(monkeypatch):
    monkeypatch.setattr(neo4j_graph, "_core_extract_entities", None)
    entities = extract_graph_entities('Alice uses Neo4j for "Graph Memory" in yiqiao_cloud.')
    names = {entity["name"] for entity in entities}

    assert "Alice" in names
    assert "Neo4j" in names
    assert "Graph Memory" in names
    assert "yiqiao_cloud" in names


def test_primary_entity_extraction_only_merges_identifier_fallbacks(monkeypatch):
    monkeypatch.setattr(neo4j_graph, "_core_extract_entities", lambda _text: [("PERSON", "Alice")])

    entities = extract_graph_entities("Tell me about Alice in yiqiao_cloud.")
    names = {entity["name"] for entity in entities}

    assert names == {"Alice", "yiqiao_cloud"}


def test_graph_helpers_are_safe_when_neo4j_disabled(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "false")

    assert related_memories("Alice", "default-project", {}, limit=5) == []
    status = graph_status("default-project")
    assert status["configured"] is False
    assert status["reachable"] is False


def test_prune_orphans_removes_entities_and_categories():
    session = MagicMock()

    _prune_orphans(session, "project-a")

    assert session.run.call_count == 2
    entity_query = session.run.call_args_list[0].args[0]
    category_query = session.run.call_args_list[1].args[0]
    assert "MATCH (e:Entity" in entity_query
    assert "MATCH (c:Category" in category_query
    assert "IN_CATEGORY" in category_query
    assert all(call.kwargs == {"project_id": "project-a"} for call in session.run.call_args_list)


def test_batch_upsert_uses_one_session_and_one_write_transaction(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setattr(neo4j_graph, "_schema_ready", False)
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    transaction = MagicMock()

    def execute_write(callback, rows):
        return callback(transaction, rows)

    session.execute_write.side_effect = execute_write
    monkeypatch.setattr(neo4j_graph, "_get_driver", lambda: driver)
    rows = [
        {
            "memory_id": "memory-1",
            "text": "Alice uses Neo4j.",
            "entities": {"user_id": "user-1", "agent_id": None},
            "metadata": {"project_id": "project-a", "category": "preference"},
        },
        {
            "memory_id": "memory-2",
            "text": "Alice likes graph databases.",
            "entities": {"user_id": "user-1"},
            "metadata": {"project_id": "project-a", "categories": ["technical"]},
        },
    ]

    assert upsert_memories_batch(rows) == 2
    driver.session.assert_called_once_with(database=neo4j_graph._database())
    assert session.run.call_count == 6
    assert session.run.return_value.consume.call_count == 6
    session.execute_write.assert_called_once()
    assert transaction.run.call_count == 4
    assert all("UNWIND $rows AS row" in call.args[0] for call in transaction.run.call_args_list)
    assert all(call.kwargs["rows"][0]["memory_id"] == "memory-1" for call in transaction.run.call_args_list)

    queries = "\n".join(call.args[0] for call in transaction.run.call_args_list)
    assert "MERGE (m:Memory" in queries
    assert "MERGE (e:Entity" in queries
    assert "MERGE (c:Category" in queries
    assert "RELATED_TO" in queries
    prepared = transaction.run.call_args_list[0].kwargs["rows"]
    assert {entity["kind"] for entity in prepared[0]["entities"]} == {"memory", "scope"}


def test_batch_upsert_propagates_transaction_failure_and_sets_last_error(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "true")
    monkeypatch.setattr(neo4j_graph, "_schema_ready", True)
    monkeypatch.setattr(neo4j_graph, "_last_error", None)
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.execute_write.side_effect = RuntimeError("write unavailable")
    monkeypatch.setattr(neo4j_graph, "_get_driver", lambda: driver)

    with pytest.raises(GraphBatchSyncError, match="write unavailable") as exc_info:
        upsert_memories_batch([{"memory_id": "memory-1", "text": "Alice", "entities": {}, "metadata": {}}])

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert neo4j_graph._last_error == "Neo4j graph batch upsert failed: write unavailable"
    session.execute_write.assert_called_once()


def test_batch_upsert_returns_zero_without_opening_driver_when_disabled(monkeypatch):
    monkeypatch.setenv("NEO4J_ENABLED", "false")
    get_driver = MagicMock()
    monkeypatch.setattr(neo4j_graph, "_get_driver", get_driver)

    assert upsert_memories_batch([{"memory_id": "memory-1", "text": "Alice", "entities": {}, "metadata": {}}]) == 0
    get_driver.assert_not_called()
