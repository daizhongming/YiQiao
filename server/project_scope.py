from fastapi import Request

DEFAULT_PROJECT_ID = "default-project"
PROJECT_HEADER = "X-Project-ID"


def normalize_project_id(value: str | None) -> str:
    candidate = (value or DEFAULT_PROJECT_ID).strip()[:128]
    if not candidate:
        return DEFAULT_PROJECT_ID
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return "".join(ch for ch in candidate if ch in allowed) or DEFAULT_PROJECT_ID


def get_project_id(request: Request) -> str:
    return normalize_project_id(
        getattr(request.state, "project_id", None)
        or request.headers.get(PROJECT_HEADER)
        or request.query_params.get("project_id")
    )
