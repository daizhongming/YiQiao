from pathlib import Path

import mem0
import yiqiao


def test_yiqiao_root_exports_memory_classes_and_version():
    assert yiqiao.__all__ == ["AsyncMemory", "Memory"]
    assert yiqiao.AsyncMemory is mem0.AsyncMemory
    assert yiqiao.Memory is mem0.Memory
    assert yiqiao.__version__ == mem0.__version__


def test_legacy_root_remains_compatible():
    assert mem0.__all__ == ["AsyncMemory", "Memory"]
    assert mem0.AsyncMemory is not None
    assert mem0.Memory is not None
    assert not hasattr(mem0, "AsyncMemoryClient")
    assert not hasattr(mem0, "MemoryClient")


def test_deployment_templates_only_advertise_yiqiao_settings():
    root = Path(__file__).resolve().parents[1]
    env_example = (root / "server" / ".env.example").read_text(encoding="utf-8")
    compose = (root / "server" / "docker-compose.yaml").read_text(encoding="utf-8")

    assert "MEM0_" not in env_example
    assert "MEM0_TELEMETRY" not in compose
