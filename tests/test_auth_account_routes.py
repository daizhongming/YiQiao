import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_SERVER_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "server")
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from routers import auth as auth_router  # noqa: E402


def test_delete_me_removes_every_membership_and_deletes_user(monkeypatch):
    workspace = {
        "members": [
            {
                "email": "owner@example.com",
                "role": "OWNER",
                "project_id": "project_a",
            },
            {
                "email": "OWNER@example.com",
                "role": "OWNER",
                "organization_id": "org_a",
            },
            {
                "email": "other@example.com",
                "role": "READER",
                "project_id": "project_a",
            },
        ]
    }
    saved = {}

    monkeypatch.setattr(auth_router, "get_json", lambda *_args: workspace)

    def set_json(_db, _key, value, *, commit=True):
        saved["value"] = value
        saved["commit"] = commit
        return value

    monkeypatch.setattr(auth_router, "set_json", set_json)
    db = MagicMock()
    user = SimpleNamespace(email="owner@example.com")

    result = auth_router.delete_me(user=user, db=db)

    assert result.message == "Account deleted."
    assert saved["commit"] is False
    assert [member["email"] for member in saved["value"]["members"]] == ["other@example.com"]
    db.delete.assert_called_once_with(user)
    db.commit.assert_called_once_with()


def test_refresh_converts_jwt_subject_to_uuid(monkeypatch):
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, role="admin")
    db = MagicMock()
    db.get.return_value = user

    monkeypatch.setattr(
        auth_router,
        "decode_token",
        lambda _token: {"type": "refresh", "sub": str(user_id), "jti": "refresh-jti"},
    )
    consume = MagicMock()
    monkeypatch.setattr(auth_router, "consume_refresh_jti", consume)
    monkeypatch.setattr(auth_router, "create_access_token", lambda *_args: "new-access")
    monkeypatch.setattr(auth_router, "create_refresh_token", lambda *_args: "new-refresh")

    refresh_handler = getattr(auth_router.refresh, "__wrapped__", auth_router.refresh)
    result = refresh_handler(
        request=MagicMock(),
        body=auth_router.RefreshRequest(refresh_token="old-refresh"),
        db=db,
    )

    db.get.assert_called_once_with(auth_router.User, user_id)
    consume.assert_called_once_with("refresh-jti", db)
    assert result.access_token == "new-access"
    assert result.refresh_token == "new-refresh"


def test_refresh_rejects_invalid_uuid_subject(monkeypatch):
    monkeypatch.setattr(
        auth_router,
        "decode_token",
        lambda _token: {"type": "refresh", "sub": "not-a-uuid", "jti": "refresh-jti"},
    )

    with pytest.raises(auth_router.HTTPException) as exc_info:
        refresh_handler = getattr(auth_router.refresh, "__wrapped__", auth_router.refresh)
        refresh_handler(
            request=MagicMock(),
            body=auth_router.RefreshRequest(refresh_token="old-refresh"),
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid refresh token subject."
