import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

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
