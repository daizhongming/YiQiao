from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CompanionError(Exception):
    code: str
    message: str
    status: int | None = None
    request_id: str | None = None
    retry_after: int | None = None

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.status is not None:
            result["status"] = self.status
        if self.request_id is not None:
            result["request_id"] = self.request_id
        if self.retry_after is not None:
            result["retry_after"] = self.retry_after
        return result


class InputError(CompanionError):
    def __init__(self, message: str = "Tool input is invalid.") -> None:
        super().__init__(code="invalid_tool_input", message=message, status=422)


class CredentialError(CompanionError):
    def __init__(self, message: str = "A project API key is required for this tool call.") -> None:
        super().__init__(code="authentication_required", message=message, status=401)
