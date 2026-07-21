import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.dont_write_bytecode = True

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_chat_history.py"
_SPEC = importlib.util.spec_from_file_location("import_chat_history", _SCRIPT)
import_chat_history = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
sys.modules[_SPEC.name] = import_chat_history
_SPEC.loader.exec_module(import_chat_history)


def _chatgpt_node(content, *, role, parent=None, created_at=0, children=None):
    return {
        "parent": parent,
        "children": list(children or []),
        "message": {
            "create_time": created_at,
            "author": {"role": role},
            "content": {"parts": [content]},
        },
    }


def test_parse_markdown_roles(tmp_path):
    transcript = tmp_path / "claude.md"
    transcript.write_text("User: I prefer Neo4j for graph memory.\nAssistant: Noted.\n", encoding="utf-8")

    conversations = import_chat_history.parse_file(transcript, "claude")

    assert len(conversations) == 1
    assert [message.role for message in conversations[0].messages] == ["user", "assistant"]
    assert conversations[0].source_app == "claude"


def test_chatgpt_export_payload_timestamp_and_redaction(tmp_path):
    export = tmp_path / "conversations.json"
    export.write_text(
        """
        [
          {
            "id": "conv 1",
            "title": "Prefs",
            "create_time": 1704067200,
            "current_node": "b",
            "mapping": {
              "a": {"parent": null, "children": ["b"], "message": {"create_time": 1704067200, "author": {"role": "user"}, "content": {"parts": ["My API_KEY=sk-abcdefghijklmnopqrstuvwxyz and I like tea."]}}},
              "b": {"parent": "a", "children": [], "message": {"create_time": 1704067210, "author": {"role": "assistant"}, "content": {"parts": ["I will remember the tea preference."]}}}
            }
          }
        ]
        """,
        encoding="utf-8",
    )
    conversations = import_chat_history.parse_file(export, "chatgpt")
    args = SimpleNamespace(
        batch_id="batch-test",
        user_id="daz",
        agent_id="hermes",
        infer=True,
        no_redact=False,
        use_run_id=True,
    )

    payload = import_chat_history.build_payload(conversations[0], conversations[0].messages, 0, 1, args)

    assert payload["timestamp"] == "2024-01-01T00:00:00+00:00"
    assert payload["metadata"]["created_at"] == "2024-01-01T00:00:00+00:00"
    assert " " not in payload["run_id"]
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in payload["messages"][0]["content"]
    assert payload["metadata"]["source_app"] == "chatgpt"


@pytest.mark.parametrize(
    ("current_node", "expected"),
    [
        ("active", ["question", "active answer"]),
        (None, ["question", "latest fallback"]),
        ("missing", ["question", "latest fallback"]),
    ],
)
def test_chatgpt_mapping_selects_one_active_branch(current_node, expected):
    mapping = {
        "root": _chatgpt_node("question", role="user", created_at=1, children=["active", "latest"]),
        "active": _chatgpt_node("active answer", role="assistant", parent="root", created_at=2),
        "latest": _chatgpt_node(
            "latest fallback",
            role="assistant",
            parent="root",
            created_at=3,
            children=["tool-tip"],
        ),
        "tool-tip": {"parent": "latest", "children": [], "message": None},
    }

    conversations = import_chat_history.parse_chatgpt_export(
        [{"id": "branch", "mapping": mapping, "current_node": current_node}],
        Path("conversations.json"),
        "chatgpt",
    )

    assert [message.content for message in conversations[0].messages] == expected


def test_chatgpt_mapping_cycle_does_not_flatten_other_nodes():
    mapping = {
        "a": _chatgpt_node("tip", role="assistant", parent="b", created_at=2),
        "b": _chatgpt_node("parent", role="user", parent="a", created_at=1),
        "other": _chatgpt_node("other branch", role="assistant", created_at=99),
    }

    conversations = import_chat_history.parse_chatgpt_export(
        [{"id": "cycle", "mapping": mapping, "current_node": "a"}],
        Path("conversations.json"),
        "chatgpt",
    )

    assert [message.content for message in conversations[0].messages] == ["parent", "tip"]


def test_missing_api_key_error_only_advertises_yiqiao(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(import_chat_history, "default_env_files", lambda: [])
    monkeypatch.delenv("YIQIAO_API_KEY", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)

    result = import_chat_history.main(["--input", str(tmp_path / "missing.json")])
    error = capsys.readouterr().err

    assert result == 2
    assert "YIQIAO_API_KEY" in error
    assert "MEM0" not in error
