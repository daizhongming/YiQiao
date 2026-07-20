import ast
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from mem0.configs.base import MemoryConfig
from mem0.memory import setup as memory_setup
from scripts import full_stack_smoke

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
EXTERNAL_PROVIDER_TEST_CLASSES = {
    Path("vector_stores/test_e2e_threshold.py"): {
        "TestMilvusCosineThreshold",
        "TestMilvusL2Threshold",
        "TestPGVectorThreshold",
        "TestRedisThreshold",
        "TestS3VectorsThreshold",
        "TestSupabaseThreshold",
        "TestValkeyThreshold",
    },
    Path("vector_stores/test_neptune_analytics.py"): {"TestNeptuneAnalyticsOperations"},
    Path("vector_stores/test_score_normalization.py"): {
        "TestMilvusCosine",
        "TestMilvusL2",
        "TestPGVector",
        "TestRedis",
        "TestS3Vectors",
        "TestSupabase",
        "TestValkey",
    },
}


def _initializer_fixture(tmp_path: Path, script_name: str) -> tuple[Path, Path]:
    root = tmp_path / script_name.replace(".", "-")
    scripts_dir = root / "scripts"
    server_dir = root / "server"
    scripts_dir.mkdir(parents=True)
    server_dir.mkdir()

    script = scripts_dir / script_name
    shutil.copyfile(PROJECT_ROOT / "scripts" / script_name, script)
    script.chmod(0o755)
    (server_dir / ".env.example").write_text(
        "POSTGRES_PASSWORD=\nNEO4J_PASSWORD=\nJWT_SECRET=\n",
        encoding="utf-8",
    )
    return root, script


def _initializer_secrets(root: Path) -> dict[str, str]:
    return full_stack_smoke._load_required_secrets(root / "server" / ".env")


def _assigned_expression(tree: ast.Module, name: str) -> ast.expr:
    matches = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == name
    ]
    assert len(matches) == 1
    return matches[0]


def _is_normalized_true_environment_flag(expression: ast.expr, variable: str) -> bool:
    expected = ast.parse(f"os.getenv({variable!r}, '').strip().lower() == 'true'", mode="eval").body
    return ast.dump(expression, include_attributes=False) == ast.dump(expected, include_attributes=False)


def _skipif_condition(node: ast.ClassDef) -> ast.expr:
    matches = [
        decorator.args[0]
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "skipif"
        and decorator.args
    ]
    assert len(matches) == 1
    return matches[0]


def _is_negated_name(expression: ast.expr, name: str) -> bool:
    return (
        isinstance(expression, ast.UnaryOp)
        and isinstance(expression.op, ast.Not)
        and isinstance(expression.operand, ast.Name)
        and expression.operand.id == name
    )


def test_mem0_state_is_scoped_to_the_pytest_temporary_root():
    isolated_state = Path(os.environ["MEM0_DIR"]).resolve()
    user_state = (Path.home() / ".mem0").resolve()

    assert isolated_state != user_state
    assert isolated_state.parent.name.startswith("yiqiao-pytest-")
    assert Path(MemoryConfig().history_db_path).resolve().is_relative_to(isolated_state)
    assert Path(memory_setup.mem0_dir).resolve() == isolated_state
    assert Path(memory_setup._config_path()).resolve().is_relative_to(isolated_state)


def test_tests_do_not_implicitly_load_dotenv_files():
    implicit_calls = []

    for path in TESTS_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function_name = None
            if isinstance(node.func, ast.Name):
                function_name = node.func.id
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "dotenv":
                    function_name = node.func.attr
            if function_name not in {"dotenv_values", "load_dotenv"}:
                continue
            has_explicit_source = bool(node.args) or any(
                keyword.arg in {"dotenv_path", "stream"} for keyword in node.keywords
            )
            if not has_explicit_source:
                implicit_calls.append(f"{path.relative_to(TESTS_DIR)}:{node.lineno}")

    assert implicit_calls == []


def test_external_provider_tests_require_exact_opt_in_before_probes():
    for relative_path, expected_classes in EXTERNAL_PROVIDER_TEST_CLASSES.items():
        path = TESTS_DIR / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        external_flag = _assigned_expression(tree, "RUN_EXTERNAL_PROVIDER_TESTS")
        assert _is_normalized_true_environment_flag(external_flag, "YIQIAO_RUN_EXTERNAL_PROVIDER_TESTS")

        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        assert expected_classes <= classes.keys()
        for class_name in expected_classes:
            condition = _skipif_condition(classes[class_name])
            assert isinstance(condition, ast.BoolOp) and isinstance(condition.op, ast.Or)
            assert len(condition.values) >= 2
            assert _is_negated_name(condition.values[0], "RUN_EXTERNAL_PROVIDER_TESTS")

        if relative_path.name == "test_neptune_analytics.py":
            neptune_flag = _assigned_expression(tree, "NEPTUNE_ANALYTICS_TESTS_ENABLED")
            assert _is_normalized_true_environment_flag(neptune_flag, "RUN_TEST_NEPTUNE_ANALYTICS")
            condition = _skipif_condition(classes["TestNeptuneAnalyticsOperations"])
            assert _is_negated_name(condition.values[1], "NEPTUNE_ANALYTICS_TESTS_ENABLED")


def test_smoke_initializer_commands_are_platform_specific(monkeypatch, tmp_path):
    env_file = tmp_path / "server" / ".env"
    monkeypatch.setattr(full_stack_smoke, "ROOT", tmp_path)
    monkeypatch.setattr(full_stack_smoke, "ENV_FILE", env_file)

    windows = full_stack_smoke._initializer_command("win32")
    posix = full_stack_smoke._initializer_command("linux")

    assert windows[:6] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(tmp_path / "scripts" / "init.ps1"),
    ]
    assert windows[-2:] == ["-EnvFile", str(env_file)]
    assert posix == ["sh", str(tmp_path / "scripts" / "init.sh")]


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="requires a POSIX shell")
def test_posix_initializer_normalizes_option_like_generated_secrets(tmp_path):
    root, script = _initializer_fixture(tmp_path, "init.sh")
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    hostile_secret = "-" + ("A" * 70)

    openssl = fake_bin / "openssl"
    openssl.write_text(f"#!/bin/sh\nprintf '%s\\n' '{hostile_secret}'\n", encoding="utf-8")
    openssl.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        ["sh", str(script)],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert set(_initializer_secrets(root).values()) == {f"y{hostile_secret[:63]}"}


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="requires a POSIX shell")
def test_posix_initializer_rejects_option_like_existing_neo4j_password(tmp_path):
    root, script = _initializer_fixture(tmp_path, "init.sh")
    (root / "server" / ".env").write_text(
        "POSTGRES_PASSWORD=postgres-safe\nNEO4J_PASSWORD=-not-a-valid-neo4j-password\nJWT_SECRET=jwt-safe\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["sh", str(script)],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "NEO4J_PASSWORD must not start with '-'" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="requires PowerShell")
def test_powershell_initializer_generates_neo4j_safe_secrets(tmp_path):
    assert POWERSHELL is not None
    root, script = _initializer_fixture(tmp_path, "init.ps1")
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    if os.name == "nt":
        docker = fake_bin / "docker.cmd"
        docker.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")
    else:
        docker = fake_bin / "docker"
        docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-EnvFile",
            str(root / "server" / ".env"),
        ],
        cwd=root,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert all(value.startswith("y") and len(value) == 64 for value in _initializer_secrets(root).values())


@pytest.mark.skipif(POWERSHELL is None, reason="requires PowerShell")
def test_powershell_initializer_rejects_option_like_existing_neo4j_password(tmp_path):
    assert POWERSHELL is not None
    root, script = _initializer_fixture(tmp_path, "init.ps1")
    env_file = root / "server" / ".env"
    env_file.write_text(
        "POSTGRES_PASSWORD=postgres-safe\nNEO4J_PASSWORD='-not-a-valid-neo4j-password'\nJWT_SECRET=jwt-safe\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-EnvFile",
            str(env_file),
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "NEO4J_PASSWORD must not start with '-'" in result.stderr


@pytest.mark.parametrize("preexisting", [False, True])
def test_smoke_prepare_uses_initializer_and_generated_secrets(monkeypatch, tmp_path, preexisting):
    env_file = tmp_path / "server" / ".env"
    env_file.parent.mkdir()
    generated = {
        "POSTGRES_PASSWORD": "generated-postgres",
        "NEO4J_PASSWORD": "generated-neo4j",
        "JWT_SECRET": "generated-jwt",
    }
    contents = "".join(f"{key}={value}\n" for key, value in generated.items())
    if preexisting:
        env_file.write_text(contents, encoding="utf-8")

    monkeypatch.setattr(full_stack_smoke, "ROOT", tmp_path)
    monkeypatch.setattr(full_stack_smoke, "ENV_FILE", env_file)
    for key in full_stack_smoke.REQUIRED_SECRETS:
        monkeypatch.setenv(key, f"host-{key.lower()}")

    args = full_stack_smoke.argparse.Namespace(
        project_name="yiqiao-smoke-unit",
        api_port=18888,
        dashboard_port=13000,
    )
    stack = full_stack_smoke.Stack(args)
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        if not preexisting:
            env_file.write_text(contents, encoding="utf-8")
        return full_stack_smoke.subprocess.CompletedProcess(command, 0, "initialized", "")

    monkeypatch.setattr(full_stack_smoke.subprocess, "run", fake_run)
    stack.prepare()

    assert observed["cwd"] == tmp_path
    assert observed["check"] is False
    assert observed["capture_output"] is True
    assert observed["env"]["YIQIAO_ENV_FILE"] == str(env_file)
    assert all(key not in observed["env"] for key in full_stack_smoke.REQUIRED_SECRETS)
    assert stack.created_env is not preexisting
    assert {key: stack.env[key] for key in generated} == generated


@pytest.mark.parametrize("response", [{}, {"status": "error"}, [], None])
def test_smoke_health_requires_ok_status(monkeypatch, response):
    monkeypatch.setattr(full_stack_smoke, "_wait_for_json", lambda url, timeout: response)

    with pytest.raises(full_stack_smoke.SmokeError, match="Unexpected dashboard health response"):
        full_stack_smoke._wait_for_health("dashboard", "http://dashboard/api/health", 1)


def test_smoke_checks_both_services_before_and_after_restart():
    source = Path(full_stack_smoke.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=full_stack_smoke.__file__)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    service_names = [
        call.args[0].value
        for call in ast.walk(main)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_wait_for_health"
        and isinstance(call.args[0], ast.Constant)
    ]

    assert service_names == ["API", "dashboard", "API", "dashboard"]
