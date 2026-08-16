import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import full_stack_smoke


def _args(**overrides):
    values = {
        "project_name": "yiqiao-mcp-smoke-unit",
        "api_port": 18888,
        "dashboard_port": 13000,
        "mcp_port": 18765,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_stack_isolates_mcp_image_bind_and_port(monkeypatch):
    monkeypatch.setenv("MCP_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("YIQIAO_MCP_IMAGE", "host-image:unsafe")
    monkeypatch.setenv("YIQIAO_MCP_PROFILE", "read-only")
    monkeypatch.setenv("YIQIAO_MCP_ALLOWED_HOSTS", "unsafe.example:*")
    monkeypatch.setenv("YIQIAO_MCP_ALLOWED_ORIGINS", "https://unsafe.example:*")
    monkeypatch.setenv("YIQIAO_MCP_CONNECT_TIMEOUT", "999")
    monkeypatch.setenv("YIQIAO_MCP_REQUEST_TIMEOUT", "999")

    stack = full_stack_smoke.Stack(_args(mcp_port=19001))

    assert stack.mcp_port == 19001
    assert stack.env["MCP_BIND_ADDRESS"] == "127.0.0.1"
    assert stack.env["MCP_PORT"] == "19001"
    assert stack.env["YIQIAO_MCP_IMAGE"] == "yiqiao-mcp-smoke-unit-mcp:smoke"
    assert stack.env["YIQIAO_MCP_PROFILE"] == "memory"
    assert stack.env["YIQIAO_MCP_ALLOWED_HOSTS"] == "127.0.0.1:*,localhost:*,[::1]:*,yiqiao-mcp:*"
    assert stack.env["YIQIAO_MCP_ALLOWED_ORIGINS"] == "http://127.0.0.1:*,http://localhost:*,http://[::1]:*"
    assert stack.env["YIQIAO_MCP_CONNECT_TIMEOUT"] == "5"
    assert stack.env["YIQIAO_MCP_REQUEST_TIMEOUT"] == "30"


def test_mcp_service_joins_frontend_network_for_host_port_publication():
    compose = (full_stack_smoke.SERVER / "docker-compose.yaml").read_text(encoding="utf-8")
    mcp_service = compose.split("  yiqiao-mcp:\n", 1)[1].split("\nvolumes:", 1)[0]

    assert '"${MCP_BIND_ADDRESS:-127.0.0.1}:${MCP_PORT:-8765}:8000"' in mcp_service
    assert "      - frontend\n      - backend\n" in mcp_service


def test_mcp_service_forwards_documented_proxy_and_timeout_controls():
    compose = (full_stack_smoke.SERVER / "docker-compose.yaml").read_text(encoding="utf-8")
    mcp_service = compose.split("  yiqiao-mcp:\n", 1)[1].split("\nvolumes:", 1)[0]

    for variable in (
        "YIQIAO_MCP_ALLOWED_HOSTS",
        "YIQIAO_MCP_ALLOWED_ORIGINS",
        "YIQIAO_MCP_CONNECT_TIMEOUT",
        "YIQIAO_MCP_REQUEST_TIMEOUT",
    ):
        assert f"      {variable}:" in mcp_service


def test_mcp_service_has_a_healthcheck_for_the_published_endpoint():
    compose = (full_stack_smoke.SERVER / "docker-compose.yaml").read_text(encoding="utf-8")
    mcp_service = compose.split("  yiqiao-mcp:\n", 1)[1].split("\nvolumes:", 1)[0]

    assert "    healthcheck:" in mcp_service
    assert "http://127.0.0.1:8000/healthz" in mcp_service


def test_make_targets_wait_for_mcp_before_reporting_ready():
    makefile = (full_stack_smoke.SERVER / "Makefile").read_text(encoding="utf-8")

    assert "MCP_URL ?= http://localhost:$(MCP_PORT)" in makefile
    assert "wait-mcp:" in makefile
    assert 'curl -fsS "$(MCP_URL)/healthz"' in makefile
    for target in ("up", "up-build", "up-production"):
        body = makefile.split(f"{target}:", 1)[1].split("\n\n", 1)[0]
        assert "wait-mcp" in body


def test_mcp_contract_passes_key_only_in_child_environment(monkeypatch):
    observed = {}
    api_key = "yiqiao_project_key_secret"
    stack = full_stack_smoke.Stack(_args())

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "PASS", "")

    monkeypatch.setattr(full_stack_smoke.subprocess, "run", fake_run)

    full_stack_smoke._run_mcp_contract(
        stack,
        "hermes",
        api_key,
        12.5,
        expect_success=True,
    )

    assert api_key not in observed["command"]
    assert observed["env"][full_stack_smoke.MCP_KEY_ENV] == api_key
    assert observed["command"][-6:] == [
        "--url",
        full_stack_smoke.MCP_CONTAINER_URL,
        "--key-env",
        full_stack_smoke.MCP_KEY_ENV,
        "--timeout",
        "12.5",
    ]
    assert observed["cwd"] == full_stack_smoke.ROOT
    assert observed["capture_output"] is True
    assert f"{stack.project}_backend" in observed["command"]
    assert stack.env["YIQIAO_MCP_IMAGE"] in observed["command"]
    assert (
        f"{full_stack_smoke.MCP_CONTRACT_SMOKE}:{full_stack_smoke.MCP_CONTRACT_CONTAINER_PATH}:ro"
        in observed["command"]
    )


def test_mcp_contract_redacts_every_known_key_on_failure(monkeypatch):
    active_key = "active-key-material"
    other_key = "other-key-material"
    stack = full_stack_smoke.Stack(_args())

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, f"stdout {active_key}", f"stderr {other_key}")

    monkeypatch.setattr(full_stack_smoke.subprocess, "run", fake_run)

    with pytest.raises(full_stack_smoke.SmokeError) as error:
        full_stack_smoke._run_mcp_contract(
            stack,
            "openclaw",
            active_key,
            30,
            expect_success=True,
            sensitive_values=(other_key,),
        )

    message = str(error.value)
    assert active_key not in message
    assert other_key not in message
    assert message.count("[REDACTED]") == 2


def test_expected_mcp_contract_failure_is_an_acceptance_result(monkeypatch):
    stack = full_stack_smoke.Stack(_args())

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "permission denied")

    monkeypatch.setattr(full_stack_smoke.subprocess, "run", fake_run)

    result = full_stack_smoke._run_mcp_contract(
        stack,
        "hermes",
        "read-only-key",
        30,
        expect_success=False,
    )

    assert result.returncode == 1


def test_psql_uses_validated_non_secret_variables(monkeypatch):
    stack = full_stack_smoke.Stack(_args())
    observed = {}

    def fake_run(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, "3|1|1|0\n", "")

    monkeypatch.setattr(stack, "run", fake_run)
    output = stack.psql(
        "SELECT :'key_id', :'project_id';",
        variables={
            "project_id": "project_1234abcd",
            "key_id": "1fce4e40-2c3f-4a58-8d33-2f7fd51be715",
        },
    )

    assert output == "3|1|1|0"
    assert observed["kwargs"] == {
        "capture": True,
        "input_text": "SELECT :'key_id', :'project_id';\n",
    }
    assert observed["args"][:4] == ("exec", "-T", "postgres", "psql")
    assert "-c" not in observed["args"]
    assert "key_id=1fce4e40-2c3f-4a58-8d33-2f7fd51be715" in observed["args"]
    assert "project_id=project_1234abcd" in observed["args"]
    assert "X-API-Key" not in " ".join(observed["args"])


@pytest.mark.parametrize(
    ("variables", "match"),
    [
        ({"Bad-Name": "project-a"}, "Unsafe psql variable name"),
        ({"project_id": "project-a'; DROP TABLE api_keys; --"}, "Unsafe psql variable value"),
    ],
)
def test_psql_rejects_unsafe_variables(variables, match):
    stack = full_stack_smoke.Stack(_args())

    with pytest.raises(full_stack_smoke.SmokeError, match=match):
        stack.psql("SELECT 1", variables=variables)


def test_mcp_health_uses_healthz(monkeypatch):
    observed = {}

    def fake_wait(service, url, timeout):
        observed.update(service=service, url=url, timeout=timeout)
        return {"status": "ok"}

    monkeypatch.setattr(full_stack_smoke, "_wait_for_health", fake_wait)

    assert full_stack_smoke._wait_for_mcp_health("http://127.0.0.1:18765", 42) == {"status": "ok"}
    assert observed == {
        "service": "MCP",
        "url": "http://127.0.0.1:18765/healthz",
        "timeout": 42,
    }


def test_request_log_accounting_rejects_cross_project_attribution(monkeypatch):
    stack = full_stack_smoke.Stack(_args())
    project_key = full_stack_smoke.ProjectApiKey(
        id="1fce4e40-2c3f-4a58-8d33-2f7fd51be715",
        project_id="project-a",
        value="secret-key",
    )
    monkeypatch.setattr(stack, "psql", lambda *_args, **_kwargs: "3|1|1|1")

    with pytest.raises(full_stack_smoke.SmokeError, match="outside its project") as error:
        full_stack_smoke._wait_for_key_accounting(
            stack,
            project_key,
            minimum_total=3,
            minimum_writes=1,
            minimum_searches=1,
            timeout=1,
        )

    assert "secret-key" not in str(error.value)
    assert "secret-key" not in repr(project_key)


def test_main_builds_and_rechecks_mcp_service():
    source = Path(full_stack_smoke.__file__).read_text(encoding="utf-8")

    assert 'stack.run("build", "--no-cache", "yiqiao", "yiqiao-dashboard", "yiqiao-mcp")' in source
    assert source.count("_wait_for_mcp_health(mcp_url, args.timeout)") == 2


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "-inf"])
def test_parse_args_rejects_nonpositive_or_nonfinite_timeout(monkeypatch, timeout):
    monkeypatch.setattr(sys, "argv", ["full_stack_smoke.py", f"--timeout={timeout}"])

    with pytest.raises(SystemExit) as error:
        full_stack_smoke.parse_args()

    assert error.value.code == 2


def test_parse_args_rejects_overlapping_service_ports(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["full_stack_smoke.py", "--api-port", "18888", "--mcp-port", "18888"],
    )

    with pytest.raises(SystemExit) as error:
        full_stack_smoke.parse_args()

    assert error.value.code == 2
