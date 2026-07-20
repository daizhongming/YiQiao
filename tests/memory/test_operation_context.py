import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mem0.exceptions import LLMError
from mem0.memory.main import Memory, MemoryOperationContext
from mem0.memory.storage import SQLiteManager


def _memory_response(*memories):
    return json.dumps({"memory": list(memories)})


def _extracted(text, source_indices, confidence=0.95, **extra):
    return {
        "text": text,
        "source_message_indices": source_indices,
        "confidence": confidence,
        **extra,
    }


@pytest.fixture
def import_memory(mocker):
    memory = object.__new__(Memory)
    memory.llm = MagicMock()
    memory.embedding_model = MagicMock()
    memory.embedding_model.embed.return_value = [0.1, 0.2, 0.3]
    memory.embedding_model.embed_batch.side_effect = lambda texts, _action: [[0.1, 0.2, 0.3] for _ in texts]
    memory.vector_store = MagicMock()
    memory.vector_store.search.return_value = []
    memory.db = MagicMock()
    memory.db.get_last_messages.return_value = []
    memory.custom_instructions = None
    memory.api_version = "v1.1"
    memory._entity_store = None
    memory._import_extract_entities = mocker.patch(
        "mem0.memory.main.extract_entities_batch",
        side_effect=lambda texts: [[] for _text in texts],
    )
    mocker.patch("mem0.memory.main.capture_event")
    return memory


def _source_messages(*contents):
    return [{"role": "user", "content": content, "source_index": 10 + index} for index, content in enumerate(contents)]


def _run_import(memory, operation_context, source_messages):
    return memory._add_to_vector_store(
        messages=[{"role": item["role"], "content": item["content"]} for item in source_messages],
        metadata={"project_id": "project-1"},
        filters={"user_id": "user-1", "project_id": "project-1"},
        infer=True,
        operation_context=operation_context,
    )


def test_import_message_ids_preserve_split_parts_and_deduplicate_overlap():
    first_part = {"role": "user", "content": "first half", "source_index": 7}
    second_part = {"role": "user", "content": "second half", "source_index": 7}
    metadata = {
        "conversation_id": "conversation-1",
        "import_key": "chunk-1",
        "project_id": "project-1",
        "source_app": "chatgpt",
        "source_path": "chat.md",
    }

    first_id = Memory._import_message_ids([first_part], metadata, "scope-1")[0]
    second_id = Memory._import_message_ids(
        [second_part],
        {**metadata, "import_key": "chunk-2"},
        "scope-1",
    )[0]
    overlap_id = Memory._import_message_ids(
        [first_part],
        {**metadata, "import_key": "overlap-chunk"},
        "scope-1",
    )[0]

    assert first_id != second_id
    assert overlap_id == first_id
    assert Memory._import_message_ids([second_part], metadata, "scope-1")[0] == second_id
    repeated_ids = Memory._import_message_ids([first_part, first_part], metadata, "scope-1")
    assert repeated_ids[0] == first_id
    assert repeated_ids[1] != first_id
    assert Memory._import_message_ids([first_part, first_part], metadata, "scope-1") == repeated_ids


def test_import_split_message_parts_survive_sqlite_replay(tmp_path):
    memory = object.__new__(Memory)
    memory.db = SQLiteManager(str(tmp_path / "history.db"))
    session_scope = "project_id=project-1&user_id=user-1"
    metadata = {
        "conversation_id": "conversation-1",
        "import_key": "chunk-1",
        "project_id": "project-1",
        "source_app": "chatgpt",
        "source_path": "chat.md",
    }
    first_part = [{"role": "user", "content": "first half", "source_index": 7}]
    second_part = [{"role": "user", "content": "second half", "source_index": 7}]

    try:
        memory._save_import_messages(first_part, metadata, session_scope)
        memory._save_import_messages(
            first_part,
            {**metadata, "import_key": "overlap-chunk"},
            session_scope,
        )
        memory._save_import_messages(
            second_part,
            {**metadata, "import_key": "chunk-2"},
            session_scope,
        )
        memory._save_import_messages(second_part, metadata, session_scope)

        saved = memory.db.get_last_messages(session_scope, limit=10)
        assert sorted(item["content"] for item in saved) == ["first half", "second half"]
    finally:
        memory.db.close()


def test_memory_add_accepts_and_forwards_operation_context(mocker):
    memory = object.__new__(Memory)
    memory.config = SimpleNamespace(llm=SimpleNamespace(config={}))
    memory.llm = MagicMock()
    memory.api_version = "v1.1"
    memory._add_to_vector_store = MagicMock(return_value=[])
    operation_context = MemoryOperationContext()

    mocker.patch("mem0.memory.main.parse_vision_messages", side_effect=lambda messages, *_args: messages)
    mocker.patch("mem0.memory.main.display_first_run_notice")

    result = memory.add("hello", user_id="user-1", operation_context=operation_context)

    assert result == {"results": []}
    assert memory._add_to_vector_store.call_args.kwargs["operation_context"] is operation_context


def test_import_context_persists_traceability_claims_ids_and_phase_timings(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(
        _extracted("User lives in Hangzhou.", [10], attributed_to="user")
    )
    callback = MagicMock()
    claimed = []

    def claim(memory_hash):
        claimed.append(memory_hash)
        return True

    operation_context = MemoryOperationContext(
        primary_llm=primary,
        primary_model_label="fast-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        min_confidence=0.8,
        strict_vector_write=True,
        phase_callback=callback,
        memory_hash_claim=claim,
        id_factory=lambda memory_hash: f"memory-{memory_hash[:12]}",
    )

    result = _run_import(import_memory, operation_context, source_messages)

    expected_hash = hashlib.md5("User lives in Hangzhou.".encode()).hexdigest()
    assert result == [
        {
            "id": f"memory-{expected_hash[:12]}",
            "memory": "User lives in Hangzhou.",
            "event": "ADD",
            "source_message_indices": [10],
            "confidence": 0.95,
        }
    ]
    payload = import_memory.vector_store.insert.call_args.kwargs["payloads"][0]
    assert payload["source_message_indices"] == [10]
    assert payload["confidence"] == 0.95
    assert payload["hash"] == expected_hash
    assert claimed == [expected_hash]
    assert operation_context.claimed_memory_hashes == [expected_hash]
    assert operation_context.model_used == "fast-model"
    assert operation_context.fallback_reason is None

    phases = [call.args[0] for call in callback.call_args_list]
    assert phases == ["context_load", "llm", "embedding", "pgvector", "entity_processing"]
    assert set(operation_context.phase_timings) == set(phases)
    assert all(len(call.args) == 2 and isinstance(call.args[1], float) for call in callback.call_args_list)
    system_prompt = primary.generate_response.call_args.kwargs["messages"][0]["content"]
    assert "source_message_indices" in system_prompt
    assert "confidence" in system_prompt


def test_overlap_only_primary_routes_to_fallback_with_core_evidence(import_memory):
    source_messages = _source_messages("Earlier overlap fact.", "New core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("Earlier overlap fact.", [10]))
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("New core fact.", [11]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        primary_model_label="fast-model",
        fallback_model_label="quality-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[11],
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["New core fact."]
    assert operation_context.model_used == "quality-model"
    assert operation_context.fallback_reason == "primary_missing_core_evidence"
    system_prompt = primary.generate_response.call_args.kwargs["messages"][0]["content"]
    assert "core source_message_indices are [11]" in system_prompt
    assert "context-only source_message_indices are [10]" in system_prompt
    assert "supported only by overlap messages" in system_prompt


def test_mixed_overlap_response_keeps_core_memories_without_fallback(import_memory):
    source_messages = _source_messages("Earlier overlap fact.", "New core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(
        _extracted("Earlier overlap fact.", [10]),
        _extracted("New core fact.", [11]),
    )
    fallback = MagicMock()
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        primary_model_label="fast-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[11],
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["New core fact."]
    assert operation_context.model_used == "fast-model"
    assert operation_context.fallback_reason is None
    fallback.generate_response.assert_not_called()
    assert import_memory.embedding_model.embed_batch.call_args.args[0] == ["New core fact."]


def test_mixed_overlap_fallback_keeps_only_core_memories(import_memory):
    source_messages = _source_messages("Earlier overlap fact.", "New core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = "not json"
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(
        _extracted("Earlier overlap fact.", [10]),
        _extracted("New core fact.", [11]),
    )
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        fallback_model_label="quality-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[11],
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["New core fact."]
    assert operation_context.model_used == "quality-model"
    assert operation_context.fallback_reason == "primary_invalid_json"


def test_mixed_overlap_response_does_not_hide_other_validation_errors(import_memory):
    source_messages = _source_messages("Earlier overlap fact.", "New core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(
        _extracted("Earlier overlap fact.", [10]),
        _extracted("Invalid evidence.", [999]),
    )
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[11],
        strict_vector_write=True,
    )

    with pytest.raises(LLMError) as exc_info:
        _run_import(import_memory, operation_context, source_messages)

    assert getattr(exc_info.value, "reason", None) == "source_index_out_of_range"
    import_memory.vector_store.insert.assert_not_called()


def test_import_context_failure_records_operation_subphase(import_memory):
    source_messages = _source_messages("New core fact.")
    import_memory.db.get_last_messages.side_effect = OSError("disk I/O error")
    operation_context = MemoryOperationContext(
        source_messages=source_messages,
        require_source_message_indices=True,
        strict_vector_write=True,
    )

    with pytest.raises(OSError, match="disk I/O error") as exc_info:
        _run_import(import_memory, operation_context, source_messages)

    assert exc_info.value.import_subphase == "context_load"


def test_overlap_only_fallback_is_rejected_too(import_memory):
    source_messages = _source_messages("Earlier overlap fact.", "New core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = "not json"
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("Earlier overlap fact.", [10]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[11],
        strict_vector_write=True,
    )

    with pytest.raises(LLMError, match="Fallback import extraction failed validation") as exc_info:
        _run_import(import_memory, operation_context, source_messages)

    assert getattr(exc_info.value.__cause__, "reason", None) == "missing_core_evidence"
    import_memory.vector_store.insert.assert_not_called()
    import_memory.db.save_messages.assert_not_called()


def test_full_core_chunk_allows_evidence_from_any_available_source(import_memory):
    source_messages = _source_messages("First core fact.", "Second core fact.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("First core fact.", [10]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        core_source_message_indices=[10, 11],
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["First core fact."]
    assert operation_context.model_used == "primary"
    system_prompt = primary.generate_response.call_args.kwargs["messages"][0]["content"]
    assert "leading overlap messages" not in system_prompt


@pytest.mark.parametrize(
    ("primary_response", "expected_reason"),
    [
        ("not json", "primary_invalid_json"),
        (_memory_response(_extracted("User lives in Hangzhou.", [], 0.95)), "primary_missing_evidence"),
        (
            _memory_response(_extracted("User lives in Hangzhou.", [999], 0.95)),
            "primary_source_index_out_of_range",
        ),
        (_memory_response(_extracted("User lives in Hangzhou.", [10], 0.2)), "primary_low_confidence"),
        (
            _memory_response(_extracted("User lives in Hangzhou.", [10], 0.95, conflict=True)),
            "primary_conflict",
        ),
        (_memory_response(), "primary_empty_obvious_fact"),
    ],
)
def test_invalid_primary_extraction_routes_to_fallback(
    import_memory,
    primary_response,
    expected_reason,
):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = primary_response
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10], 0.99))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        primary_model_label="fast-model",
        fallback_model_label="quality-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        min_confidence=0.8,
        obvious_fact_empty_fallback=True,
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["User lives in Hangzhou."]
    assert primary.generate_response.call_count == 1
    assert fallback.generate_response.call_count == 1
    assert operation_context.model_used == "quality-model"
    assert operation_context.fallback_reason == expected_reason


def test_primary_provider_error_routes_to_fallback(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.side_effect = TimeoutError("provider timeout")
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        source_messages=source_messages,
        require_source_message_indices=True,
        strict_vector_write=True,
    )

    _run_import(import_memory, operation_context, source_messages)

    assert operation_context.model_used == "fallback"
    assert operation_context.fallback_reason == "primary_model_error"
    assert operation_context.provider_pressure_recovered is True


def test_primary_validation_fallback_does_not_report_provider_pressure(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = "not json"
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        source_messages=source_messages,
        require_source_message_indices=True,
        strict_vector_write=True,
    )

    _run_import(import_memory, operation_context, source_messages)

    assert operation_context.fallback_reason == "primary_invalid_json"
    assert operation_context.provider_pressure_recovered is False


def test_audit_provider_timeout_keeps_primary_and_reports_recovered_pressure(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    fallback = MagicMock()
    fallback.generate_response.side_effect = TimeoutError("audit provider timeout")
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        source_messages=source_messages,
        require_source_message_indices=True,
        audit=True,
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["User lives in Hangzhou."]
    assert operation_context.audit_result == "fallback_invalid"
    assert operation_context.provider_pressure_recovered is True


def test_force_fallback_uses_explicit_reason_without_calling_primary(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        fallback_model_label="quality-model",
        force_fallback=True,
        force_fallback_reason="complex_chunk",
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
    )

    _run_import(import_memory, operation_context, source_messages)

    primary.generate_response.assert_not_called()
    fallback.generate_response.assert_called_once()
    assert operation_context.model_used == "quality-model"
    assert operation_context.fallback_reason == "complex_chunk"


@pytest.mark.parametrize(
    ("use_more_complete", "expected_count", "expected_model", "expected_audit_result"),
    [
        (False, 1, "fast-model", "fallback_more_complete_not_selected"),
        (True, 2, "quality-model", "fallback_selected"),
    ],
)
def test_audit_calls_fallback_and_optionally_selects_more_complete_result(
    import_memory,
    use_more_complete,
    expected_count,
    expected_model,
    expected_audit_result,
):
    source_messages = _source_messages("I live in Hangzhou.", "I work as an engineer.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    fallback = MagicMock()
    fallback.generate_response.return_value = _memory_response(
        _extracted("User lives in Hangzhou.", [10], 0.99),
        _extracted("User works as an engineer.", [11], 0.98),
    )
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        fallback_llm=fallback,
        primary_model_label="fast-model",
        fallback_model_label="quality-model",
        require_source_message_indices=True,
        source_messages=source_messages,
        audit=True,
        audit_use_more_complete=use_more_complete,
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert len(result) == expected_count
    assert fallback.generate_response.call_count == 1
    assert operation_context.model_used == expected_model
    assert operation_context.audit_result == expected_audit_result
    assert operation_context.audit_metadata["fallback"]["memory_count"] == 2
    assert "content" not in json.dumps(operation_context.audit_metadata)


def test_hash_claim_filters_overlap_before_embedding_and_id_creation(import_memory):
    source_messages = _source_messages("I live in Hangzhou.", "I work as an engineer.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(
        _extracted("User lives in Hangzhou.", [10]),
        _extracted("User works as an engineer.", [11]),
    )
    claimed_hashes = []
    generated_hashes = []
    pre_vector_write = MagicMock()

    def claim(memory_hash):
        claimed_hashes.append(memory_hash)
        return len(claimed_hashes) == 2

    def make_id(memory_hash):
        generated_hashes.append(memory_hash)
        return f"memory-{memory_hash[:12]}"

    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        memory_hash_claim=claim,
        id_factory=make_id,
        pre_vector_write=pre_vector_write,
        strict_vector_write=True,
    )

    result = _run_import(import_memory, operation_context, source_messages)

    assert [item["memory"] for item in result] == ["User works as an engineer."]
    assert generated_hashes == claimed_hashes[1:]
    assert operation_context.claimed_memory_hashes == claimed_hashes[1:]
    pre_vector_write.assert_called_once_with(1)
    embedded_texts = import_memory.embedding_model.embed_batch.call_args.args[0]
    assert embedded_texts == ["User works as an engineer."]


def test_pre_vector_write_rejection_prevents_memory_side_effects(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    pre_vector_write = MagicMock(side_effect=RuntimeError("storage capacity exceeded"))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        pre_vector_write=pre_vector_write,
    )

    with pytest.raises(RuntimeError, match="storage capacity exceeded"):
        _run_import(import_memory, operation_context, source_messages)

    pre_vector_write.assert_called_once_with(1)
    import_memory.embedding_model.embed_batch.assert_not_called()
    import_memory.vector_store.insert.assert_not_called()
    import_memory.db.batch_add_history.assert_not_called()
    import_memory._import_extract_entities.assert_not_called()
    import_memory.db.save_messages.assert_not_called()


def test_execution_guard_rejection_prevents_vector_and_followup_writes(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    execution_guard = MagicMock(side_effect=RuntimeError("import lease lost"))
    memory_hash_claim = MagicMock(return_value=True)
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        execution_guard=execution_guard,
        memory_hash_claim=memory_hash_claim,
    )

    with pytest.raises(RuntimeError, match="import lease lost"):
        _run_import(import_memory, operation_context, source_messages)

    execution_guard.assert_called_once_with()
    memory_hash_claim.assert_not_called()
    import_memory.vector_store.insert.assert_not_called()
    import_memory.db.batch_add_history.assert_not_called()
    import_memory._import_extract_entities.assert_not_called()
    import_memory.db.save_messages.assert_not_called()


def test_message_only_import_checks_execution_guard_before_save(import_memory):
    source_messages = _source_messages("No durable fact here.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response()
    execution_guard = MagicMock(side_effect=RuntimeError("import lease lost"))
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        execution_guard=execution_guard,
    )

    with pytest.raises(RuntimeError, match="import lease lost"):
        _run_import(import_memory, operation_context, source_messages)

    execution_guard.assert_called_once_with()
    import_memory.vector_store.insert.assert_not_called()
    import_memory.db.batch_add_history.assert_not_called()
    import_memory.db.save_messages.assert_not_called()


def test_post_vector_lease_loss_can_reconcile_deterministic_side_effects(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    guard_calls = 0

    def lose_lease_after_vector():
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            raise RuntimeError("import lease lost")

    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        execution_guard=lose_lease_after_vector,
        id_factory=lambda memory_hash: f"memory-{memory_hash}",
    )

    with pytest.raises(RuntimeError, match="import lease lost"):
        _run_import(import_memory, operation_context, source_messages)

    insert_call = import_memory.vector_store.insert.call_args
    memory_id = insert_call.kwargs["ids"][0]
    payload = insert_call.kwargs["payloads"][0]
    assert memory_id == f"memory-{payload['hash']}"
    import_memory.db.batch_add_history.assert_not_called()
    import_memory.db.save_messages.assert_not_called()

    operation_context.execution_guard = lambda: None
    import_memory._complete_import_side_effects(
        [(memory_id, payload["data"], None, payload)],
        source_messages,
        {"user_id": "user-1", "project_id": "project-1"},
        {"project_id": "project-1"},
        operation_context=operation_context,
    )

    import_memory.db.batch_add_history.assert_called_once()
    assert import_memory.db.batch_add_history.call_args.kwargs == {"deduplicate_by_memory_event": True}
    import_memory.db.save_messages.assert_called_once()


def test_strict_vector_failure_raises_before_history_entities_or_success(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    import_memory.vector_store.insert.side_effect = RuntimeError("batch insert failed")
    callback = MagicMock()
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        phase_callback=callback,
        memory_hash_claim=lambda _memory_hash: True,
    )

    with pytest.raises(RuntimeError, match="batch insert failed"):
        _run_import(import_memory, operation_context, source_messages)

    assert import_memory.vector_store.insert.call_count == 1
    import_memory.db.batch_add_history.assert_not_called()
    import_memory.db.add_history.assert_not_called()
    import_memory.db.save_messages.assert_not_called()
    import_memory._import_extract_entities.assert_not_called()
    assert len(operation_context.claimed_memory_hashes) == 1
    assert [call.args[0] for call in callback.call_args_list] == [
        "context_load",
        "llm",
        "embedding",
        "pgvector",
    ]


def test_post_vector_history_failure_propagates_before_entities_or_success(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    import_memory.db.batch_add_history.side_effect = RuntimeError("history unavailable")
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        memory_hash_claim=lambda _memory_hash: True,
    )

    with pytest.raises(RuntimeError, match="history unavailable"):
        _run_import(import_memory, operation_context, source_messages)

    import_memory.vector_store.insert.assert_called_once()
    import_memory.db.batch_add_history.assert_called_once()
    assert import_memory.db.batch_add_history.call_args.kwargs == {"deduplicate_by_memory_event": True}
    import_memory._import_extract_entities.assert_not_called()
    import_memory.db.save_messages.assert_not_called()
    assert len(operation_context.claimed_memory_hashes) == 1


def test_post_vector_entity_failure_propagates_after_idempotent_history(import_memory):
    source_messages = _source_messages("I live in Hangzhou.")
    primary = MagicMock()
    primary.generate_response.return_value = _memory_response(_extracted("User lives in Hangzhou.", [10]))
    import_memory._import_extract_entities.side_effect = lambda _texts: [[("city", "Hangzhou")]]
    import_memory._entity_store = MagicMock()
    import_memory._entity_store.list.side_effect = RuntimeError("entity store unavailable")
    operation_context = MemoryOperationContext(
        primary_llm=primary,
        require_source_message_indices=True,
        source_messages=source_messages,
        strict_vector_write=True,
        memory_hash_claim=lambda _memory_hash: True,
    )

    with pytest.raises(RuntimeError, match="entity store unavailable"):
        _run_import(import_memory, operation_context, source_messages)

    import_memory.vector_store.insert.assert_called_once()
    import_memory.db.batch_add_history.assert_called_once()
    history_record = import_memory.db.batch_add_history.call_args.args[0][0]
    assert history_record["id"] == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"mem0:import-history:add:{history_record['memory_id']}",
        )
    )
    import_memory.db.save_messages.assert_not_called()


def test_partial_entity_insert_replay_uses_existing_row_without_duplicate(import_memory):
    records = [
        (
            "memory-1",
            "User lives in Hangzhou.",
            [0.1, 0.2, 0.3],
            {"project_id": "project-1"},
        )
    ]
    import_memory._import_extract_entities.side_effect = lambda _texts: [[("city", "Hangzhou")]]
    entity_store = MagicMock()
    inserted = {}

    def insert_then_fail(*, vectors, ids, payloads):
        inserted["row"] = SimpleNamespace(id=ids[0], payload=payloads[0])
        raise RuntimeError("connection lost after entity commit")

    entity_store.list.side_effect = lambda **_kwargs: [[inserted["row"]]] if "row" in inserted else []
    entity_store.search_batch.return_value = [[]]
    entity_store.insert.side_effect = insert_then_fail
    import_memory._entity_store = entity_store

    with pytest.raises(RuntimeError, match="after entity commit"):
        import_memory._process_import_entities(records, {"project_id": "project-1"})

    import_memory._process_import_entities(records, {"project_id": "project-1"})

    assert entity_store.insert.call_count == 1
    entity_store.update.assert_not_called()
    assert inserted["row"].payload["linked_memory_ids"] == ["memory-1"]
    assert inserted["row"].id == str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            'mem0:import-entity:{"project_id":"project-1"}:hangzhou',
        )
    )


def test_parallel_import_entity_links_preserve_all_memory_ids(import_memory):
    state = {}
    state_lock = threading.Lock()
    entity_store = MagicMock()

    def list_rows(**_kwargs):
        with state_lock:
            row = state.get("row")
            return [SimpleNamespace(id=row.id, payload=dict(row.payload))] if row else []

    def search_batch(**_kwargs):
        with state_lock:
            row = state.get("row")
            return [[SimpleNamespace(id=row.id, payload=dict(row.payload), score=1.0)]] if row else [[]]

    def insert(*, ids, payloads, **_kwargs):
        time.sleep(0.02)
        with state_lock:
            state["row"] = SimpleNamespace(id=ids[0], payload=dict(payloads[0]))

    def update(*, vector_id, payload, **_kwargs):
        with state_lock:
            state["row"] = SimpleNamespace(id=vector_id, payload=dict(payload))

    entity_store.list.side_effect = list_rows
    entity_store.search_batch.side_effect = search_batch
    entity_store.insert.side_effect = insert
    entity_store.update.side_effect = update
    import_memory._entity_store = entity_store
    import_memory._import_extract_entities.side_effect = lambda _texts: [[("city", "Hangzhou")]]

    def records(memory_id):
        return [(memory_id, "User lives in Hangzhou.", [0.1, 0.2, 0.3], {"project_id": "project-1"})]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                import_memory._process_import_entities,
                records(memory_id),
                {"project_id": "project-1"},
            )
            for memory_id in ("memory-1", "memory-2")
        ]
        for future in futures:
            future.result()

    assert state["row"].payload["linked_memory_ids"] == ["memory-1", "memory-2"]


def test_atomic_entity_link_union_preserves_cross_instance_updates_from_stale_matches():
    state = {"linked_memory_ids": set()}
    state_lock = threading.Lock()

    class AtomicEntityStore:
        def __init__(self):
            self.fallback_updates = 0

        def atomic_payload_list_union(self, *, vector_id, key, values):
            assert vector_id == "entity-1"
            assert key == "linked_memory_ids"
            with state_lock:
                state["linked_memory_ids"].update(values)
            return True

        def update(self, **_kwargs):
            self.fallback_updates += 1

    first_memory = object.__new__(Memory)
    second_memory = object.__new__(Memory)
    first_store = AtomicEntityStore()
    second_store = AtomicEntityStore()
    first_memory._entity_store = first_store
    second_memory._entity_store = second_store
    stale_match = SimpleNamespace(id="entity-1", payload={"linked_memory_ids": []})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(memory._union_entity_linked_memory_ids, stale_match, [memory_id], strict=True)
            for memory, memory_id in (
                (first_memory, "memory-1"),
                (second_memory, "memory-2"),
            )
        ]
        assert all(future.result() for future in futures)

    assert state["linked_memory_ids"] == {"memory-1", "memory-2"}
    assert first_store.fallback_updates == 0
    assert second_store.fallback_updates == 0
