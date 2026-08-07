from __future__ import annotations

import pytest

from yiqiao_mcp.cli import _parser, build_settings
from yiqiao_mcp.config import DEFAULT_ALLOWED_HOSTS, DEFAULT_ALLOWED_ORIGINS, Settings


def test_cli_defaults_to_loopback_with_loopback_transport_allowlists(monkeypatch):
    monkeypatch.delenv("YIQIAO_MCP_HOST", raising=False)
    monkeypatch.delenv("YIQIAO_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("YIQIAO_MCP_ALLOWED_ORIGINS", raising=False)

    settings, _log_level = build_settings([])

    assert settings.host == "127.0.0.1"
    assert settings.allowed_hosts == DEFAULT_ALLOWED_HOSTS
    assert settings.allowed_origins == DEFAULT_ALLOWED_ORIGINS


def test_cli_has_no_api_key_argument_or_help_text(capsys):
    parser = _parser(Settings())
    option_strings = {option for action in parser._actions for option in action.option_strings}

    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out.casefold()
    assert "--api-key" not in option_strings
    assert "api key" not in help_text
    assert "api-key" not in help_text


@pytest.mark.parametrize(
    "arguments",
    [
        ["--connect-timeout", "nan"],
        ["--connect-timeout", "inf"],
        ["--request-timeout=-inf"],
    ],
)
def test_cli_rejects_nonfinite_timeouts(arguments, capsys):
    with pytest.raises(SystemExit) as raised:
        build_settings(arguments)

    assert raised.value.code == 2
    assert "positive finite" in capsys.readouterr().err
