from numbers import Real
from typing import Any, Literal

from auth import require_project_write
from db import SessionLocal
from errors import upstream_error
from fastapi import APIRouter, Depends, Request
from project_scope import get_project_id
from pydantic import BaseModel, Field, field_validator
from server_state import get_memory_instance
from settings_store import get_json
from workspace import DEFAULT_WORKSPACE_SETTINGS, WORKSPACE_KEY, project_settings

router = APIRouter(prefix="/playground", tags=["playground"])

PLAYGROUND_MAX_HISTORY_MESSAGES = 20
PLAYGROUND_MAX_MESSAGE_LENGTH = 10_000
PLAYGROUND_MAX_HISTORY_MESSAGE_LENGTH = 50_000
PLAYGROUND_MAX_USER_ID_LENGTH = 255


class PlaygroundHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(
        min_length=1,
        max_length=PLAYGROUND_MAX_HISTORY_MESSAGE_LENGTH,
    )

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class PlaygroundChat(BaseModel):
    message: str = Field(min_length=1, max_length=PLAYGROUND_MAX_MESSAGE_LENGTH)
    user_id: str = Field(min_length=1, max_length=PLAYGROUND_MAX_USER_ID_LENGTH)
    history: list[PlaygroundHistoryMessage] = Field(
        default_factory=list,
        max_length=PLAYGROUND_MAX_HISTORY_MESSAGES,
    )
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message", "user_id", mode="before")
    @classmethod
    def normalize_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


def _playground_settings(project_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        workspace = get_json(db, WORKSPACE_KEY, DEFAULT_WORKSPACE_SETTINGS)
    return project_settings(workspace, project_id).get("playground") or {}


def _playground_project_id(project_id: str) -> str:
    return f"{project_id[:108]}.__playground__"


def _search_results(search: Any) -> list[dict[str, Any]]:
    memories = search.get("results", search) if isinstance(search, dict) else search
    if not isinstance(memories, list) or any(not isinstance(item, dict) for item in memories):
        raise upstream_error()
    return memories


def _memory_score(item: dict[str, Any]) -> float | None:
    score = item.get("score")
    if isinstance(score, Real) and not isinstance(score, bool):
        return float(score)
    return None


def _merge_search_results(searches: list[Any], top_k: int) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    memory_indexes: dict[Any, int] = {}
    for search in searches:
        for item in _search_results(search):
            memory_id = item.get("id")
            if memory_id is None:
                memories.append(item)
                continue

            try:
                existing_index = memory_indexes.get(memory_id)
            except TypeError:
                memories.append(item)
                continue
            if existing_index is None:
                memory_indexes[memory_id] = len(memories)
                memories.append(item)
                continue

            existing_score = _memory_score(memories[existing_index])
            candidate_score = _memory_score(item)
            if candidate_score is not None and (existing_score is None or candidate_score > existing_score):
                memories[existing_index] = item

    memories.sort(
        key=lambda item: (
            _memory_score(item) is None,
            -(_memory_score(item) or 0.0),
        )
    )
    return memories[:top_k]


@router.post("/chat")
def chat(request: Request, body: PlaygroundChat, _auth=Depends(require_project_write)):
    memory = get_memory_instance()
    project_id = get_project_id(request)
    storage_project_id = _playground_project_id(project_id)
    settings = _playground_settings(project_id)
    top_k = max(1, min(int(settings.get("top_k") or 10), 100))
    search_params: dict[str, Any] = {"top_k": top_k}
    if settings.get("threshold") is not None:
        search_params["threshold"] = max(0.0, min(float(settings["threshold"]), 1.0))
    if settings.get("reranking") is not None:
        search_params["rerank"] = bool(settings["reranking"])
    project_search = memory.search(
        query=body.message,
        filters={"user_id": body.user_id, "project_id": project_id},
        **search_params,
    )
    playground_search = memory.search(
        query=body.message,
        filters={"user_id": body.user_id, "project_id": storage_project_id},
        **search_params,
    )
    memory.add(
        messages=[{"role": "user", "content": body.message}],
        user_id=body.user_id,
        metadata={
            "source": "playground",
            "project_id": storage_project_id,
            "source_project_id": project_id,
        },
        infer=not bool(settings.get("force_add_only")),
    )

    memories = _merge_search_results([project_search, playground_search], top_k)
    context = "\n".join(str(item.get("memory") or item) for item in memories if item)
    memory_prompt = (
        f"Answer the user using the relevant memories when helpful.\n\nRelevant memories:\n{context or 'None'}"
    )
    messages: list[dict[str, str]] = []
    custom_instructions = str(settings.get("custom_instructions") or "").strip()
    if custom_instructions:
        messages.append({"role": "system", "content": custom_instructions})
    messages.append({"role": "system", "content": memory_prompt})
    messages.extend(message.model_dump() for message in body.history)
    messages.append({"role": "user", "content": body.message})
    try:
        reply = memory.llm.generate_response(
            messages,
            temperature=max(0.0, min(float(settings.get("temperature") or 0.1), 2.0)),
            top_p=max(0.0, min(float(settings.get("top_p") or 1.0), 1.0)),
            max_tokens=max(1, min(int(settings.get("max_tokens") or 2048), 131_072)),
        )
    except Exception:
        reply = (
            "I saved that message and searched the current memory set. "
            "Configure a working LLM in .env for generated replies."
        )
    return {"reply": reply, "memories": memories}
