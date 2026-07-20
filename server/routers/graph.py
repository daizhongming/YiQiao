from auth import require_project_read, require_project_write
from fastapi import APIRouter, Depends, Query, Request
from neo4j_graph import (
    fetch_graph,
    fetch_neighbors,
    graph_status,
    list_graph_entities,
    upsert_memory,
)
from project_scope import DEFAULT_PROJECT_ID, get_project_id
from server_state import get_memory_instance

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("")
def get_graph(request: Request, limit: int = Query(default=200, ge=1, le=1000), _auth=Depends(require_project_read)):
    return fetch_graph(project_id=get_project_id(request), limit=limit)


@router.get("/status")
def get_graph_status(request: Request, _auth=Depends(require_project_read)):
    return graph_status(project_id=get_project_id(request))


@router.get("/entities")
def get_graph_entities(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    _auth=Depends(require_project_read),
):
    return {"results": list_graph_entities(project_id=get_project_id(request), limit=limit)}


@router.get("/memories/{memory_id}/neighbors")
def get_memory_neighbors(
    request: Request,
    memory_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    _auth=Depends(require_project_read),
):
    return fetch_neighbors(memory_id=memory_id, project_id=get_project_id(request), limit=limit)


@router.post("/sync")
def sync_graph(
    request: Request,
    limit: int = Query(default=1000, ge=1, le=10000),
    _auth=Depends(require_project_write),
):
    project_id = get_project_id(request)
    results = get_memory_instance().vector_store.list(top_k=limit)
    rows = results[0] if results and isinstance(results, list) and isinstance(results[0], list) else results or []
    count = 0
    for row in rows:
        payload = getattr(row, "payload", None) or {}
        if (payload.get("project_id") or DEFAULT_PROJECT_ID) != project_id:
            continue
        upsert_memory(
            str(getattr(row, "id", "")),
            payload.get("data"),
            {key: payload.get(key) for key in ("user_id", "agent_id", "app_id", "run_id")},
            payload,
        )
        count += 1
    return {"synced": count, "status": graph_status(project_id)}
