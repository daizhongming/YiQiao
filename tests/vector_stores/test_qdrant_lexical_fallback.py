# This file was modified in 2026 by YiQiao contributors. See NOTICE.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, SparseVector

from mem0.memory.main import AsyncMemory, Memory
from mem0.vector_stores.qdrant import Qdrant


def _qdrant(*, dimensions=4):
    client = MagicMock(spec=QdrantClient)
    store = Qdrant(
        collection_name="lexical-fallback",
        embedding_model_dims=dimensions,
        client=client,
    )
    client.reset_mock()
    return store, client


def _condition(query_filter, section, key):
    return next(
        condition for condition in (getattr(query_filter, section) or []) if getattr(condition, "key", None) == key
    )


def _memory_with_qdrant(memory_type):
    with patch.object(memory_type, "__init__", return_value=None):
        memory = memory_type()

    store, client = _qdrant(dimensions=4)
    store._has_bm25_slot = False
    client.scroll.return_value = ([], None)
    client.retrieve.return_value = []

    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.side_effect = RuntimeError("embedding provider unavailable")
    memory.vector_store = store
    memory.db = MagicMock()
    memory.db.get_last_messages.return_value = []
    return memory, client


class _UpdateStore:
    def __init__(self, *, supports_lexical_only_records):
        self.supports_lexical_only_records = supports_lexical_only_records
        self.updated = []
        self.existing = SimpleNamespace(
            id="memory-1",
            payload={
                "data": "The deployment code is atlas-41.",
                "hash": "old-hash",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "project_id": "project-a",
                "user_id": "alice",
            },
        )

    def get(self, *, vector_id):
        assert vector_id == "memory-1"
        return self.existing

    def update(self, **kwargs):
        self.updated.append(kwargs)


def _memory_for_update(memory_type, *, lexical_only_supported):
    with patch.object(memory_type, "__init__", return_value=None):
        memory = memory_type()

    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.side_effect = RuntimeError("embedding provider unavailable")
    memory.vector_store = _UpdateStore(supports_lexical_only_records=lexical_only_supported)
    memory.db = MagicMock()
    if memory_type is AsyncMemory:
        memory._remove_memory_from_entity_store = AsyncMock()
        memory._link_entities_for_memory = AsyncMock()
    else:
        memory._remove_memory_from_entity_store = MagicMock()
        memory._link_entities_for_memory = MagicMock()
    return memory


def test_none_vector_insert_uses_fixed_size_placeholder_and_keeps_bm25():
    store, client = _qdrant(dimensions=4)
    store._has_bm25_slot = True
    encoder = MagicMock()
    encoder.embed.return_value = iter(
        [
            MagicMock(
                indices=MagicMock(tolist=lambda: [7]),
                values=MagicMock(tolist=lambda: [0.75]),
            )
        ]
    )
    store._bm25_encoder = encoder

    store.insert(
        vectors=[None],
        payloads=[
            {
                "data": "The deployment code is atlas-42.",
                "text_lemmatized": "deployment code atlas 42",
                "embedding_status": "pending",
            }
        ],
        ids=["pending-memory"],
    )

    assert store.supports_lexical_only_records is True
    point = client.upsert.call_args.kwargs["points"][0]
    assert point.vector[""] == [0.0, 0.0, 0.0, 0.0]
    assert point.vector["bm25"] == SparseVector(indices=[7], values=[0.75])
    assert point.payload["embedding_status"] == "pending"


def test_dense_search_excludes_pending_placeholder_vectors():
    store, client = _qdrant()
    client.query_points.return_value = MagicMock(points=[])

    store.search(
        query="deployment code",
        vectors=[0.1, 0.2, 0.3, 0.4],
        top_k=3,
        filters={"project_id": "project-a"},
    )

    query_filter = client.query_points.call_args.kwargs["query_filter"]
    assert isinstance(query_filter, Filter)
    assert _condition(query_filter, "must", "project_id").match.value == "project-a"
    assert _condition(query_filter, "must_not", "embedding_status").match.value == "pending"


def test_dense_batch_search_excludes_pending_placeholder_vectors():
    store, client = _qdrant()
    client.query_batch_points.return_value = [MagicMock(points=[])]

    store.search_batch(
        queries=["deployment code"],
        vectors_list=[[0.1, 0.2, 0.3, 0.4]],
        top_k=3,
        filters={"project_id": "project-a"},
    )

    request = client.query_batch_points.call_args.kwargs["requests"][0]
    assert isinstance(request.filter, Filter)
    assert _condition(request.filter, "must", "project_id").match.value == "project-a"
    assert _condition(request.filter, "must_not", "embedding_status").match.value == "pending"


def test_keyword_search_can_return_pending_record():
    store, client = _qdrant()
    store._has_bm25_slot = True
    sparse_query = SparseVector(indices=[7], values=[1.0])
    pending_hit = MagicMock(
        id="pending-memory",
        score=0.9,
        payload={"data": "The deployment code is atlas-42.", "embedding_status": "pending"},
    )
    client.query_points.return_value = MagicMock(points=[pending_hit])

    with patch.object(store, "_encode_bm25", return_value=sparse_query):
        result = store.keyword_search(
            "atlas-42",
            top_k=3,
            filters={"project_id": "project-a"},
        )

    assert result == [pending_hit]
    call = client.query_points.call_args.kwargs
    assert call["using"] == "bm25"
    assert call["query"] == sparse_query
    assert _condition(call["query_filter"], "must", "project_id").match.value == "project-a"
    assert call["query_filter"].must_not is None


def test_sync_memory_add_survives_embedding_failure_with_qdrant():
    memory, client = _memory_with_qdrant(Memory)

    result = memory._add_to_vector_store(
        [{"role": "user", "content": "The deployment code is atlas-42."}],
        {"project_id": "project-a"},
        {"project_id": "project-a", "user_id": "alice"},
        infer=False,
        _scope_lock_acquired=True,
    )

    assert len(result) == 1
    point = client.upsert.call_args.kwargs["points"][0]
    assert point.vector[""] == [0.0, 0.0, 0.0, 0.0]
    assert point.payload["embedding_status"] == "pending"
    assert point.payload["data"] == "The deployment code is atlas-42."


@pytest.mark.asyncio
async def test_async_memory_add_survives_embedding_failure_with_qdrant():
    memory, client = _memory_with_qdrant(AsyncMemory)

    result = await memory._add_to_vector_store(
        [{"role": "user", "content": "The deployment code is atlas-42."}],
        {"project_id": "project-a"},
        {"project_id": "project-a", "user_id": "alice"},
        infer=False,
        _scope_lock_acquired=True,
    )

    assert len(result) == 1
    point = client.upsert.call_args.kwargs["points"][0]
    assert point.vector[""] == [0.0, 0.0, 0.0, 0.0]
    assert point.payload["embedding_status"] == "pending"
    assert point.payload["data"] == "The deployment code is atlas-42."


def test_sync_memory_update_downgrades_to_pending_payload_when_embedding_fails():
    memory = _memory_for_update(Memory, lexical_only_supported=True)

    with (
        patch("mem0.memory.main.capture_event"),
        patch("mem0.memory.main.display_first_run_notice"),
    ):
        result = memory.update("memory-1", data="The deployment code is atlas-42.")

    assert result == {"message": "Memory updated successfully!"}
    assert len(memory.vector_store.updated) == 1
    update = memory.vector_store.updated[0]
    assert update["vector_id"] == "memory-1"
    assert update["vector"] is None
    assert update["payload"]["data"] == "The deployment code is atlas-42."
    assert update["payload"]["embedding_status"] == "pending"


def test_sync_metadata_only_update_preserves_existing_vector_without_embedding():
    memory = _memory_for_update(Memory, lexical_only_supported=True)

    with (
        patch("mem0.memory.main.capture_event"),
        patch("mem0.memory.main.display_first_run_notice"),
    ):
        result = memory.update("memory-1", metadata={"reviewed": True})

    assert result == {"message": "Memory updated successfully!"}
    memory.embedding_model.embed.assert_not_called()
    update = memory.vector_store.updated[0]
    assert update["vector"] is None
    assert update["payload"]["reviewed"] is True
    assert "embedding_status" not in update["payload"]


@pytest.mark.asyncio
async def test_async_memory_update_downgrades_to_pending_payload_when_embedding_fails():
    memory = _memory_for_update(AsyncMemory, lexical_only_supported=True)

    with (
        patch("mem0.memory.main.capture_event"),
        patch("mem0.memory.main.display_first_run_notice_async", new_callable=AsyncMock),
    ):
        result = await memory.update("memory-1", data="The deployment code is atlas-42.")

    assert result == {"message": "Memory updated successfully!"}
    assert len(memory.vector_store.updated) == 1
    update = memory.vector_store.updated[0]
    assert update["vector_id"] == "memory-1"
    assert update["vector"] is None
    assert update["payload"]["data"] == "The deployment code is atlas-42."
    assert update["payload"]["embedding_status"] == "pending"


@pytest.mark.asyncio
async def test_async_metadata_only_update_preserves_existing_vector_without_embedding():
    memory = _memory_for_update(AsyncMemory, lexical_only_supported=True)

    with (
        patch("mem0.memory.main.capture_event"),
        patch("mem0.memory.main.display_first_run_notice_async", new_callable=AsyncMock),
    ):
        result = await memory.update("memory-1", metadata={"reviewed": True})

    assert result == {"message": "Memory updated successfully!"}
    memory.embedding_model.embed.assert_not_called()
    update = memory.vector_store.updated[0]
    assert update["vector"] is None
    assert update["payload"]["reviewed"] is True
    assert "embedding_status" not in update["payload"]


def test_sync_memory_update_keeps_embedding_error_for_unsupported_store():
    memory = _memory_for_update(Memory, lexical_only_supported=False)

    with patch("mem0.memory.main.capture_event"), pytest.raises(RuntimeError, match="embedding provider unavailable"):
        memory.update("memory-1", data="The deployment code is atlas-42.")

    assert memory.vector_store.updated == []


@pytest.mark.asyncio
async def test_async_memory_update_keeps_embedding_error_for_unsupported_store():
    memory = _memory_for_update(AsyncMemory, lexical_only_supported=False)

    with patch("mem0.memory.main.capture_event"), pytest.raises(RuntimeError, match="embedding provider unavailable"):
        await memory.update("memory-1", data="The deployment code is atlas-42.")

    assert memory.vector_store.updated == []
