import os
import shutil
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="yiqiao-pytest-"))
_DATABASE_PATH = _TEST_ROOT / "yiqiao-test.db"

for _credential_name in (
    "ANTHROPIC_API_KEY",
    "EMBEDDING_API_KEY",
    "GOOGLE_API_KEY",
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "RERANK_API_KEY",
):
    os.environ.pop(_credential_name, None)

os.environ.update(
    {
        "AUTH_DISABLED": "true",
        "DATABASE_URL": f"sqlite:///{_DATABASE_PATH.as_posix()}",
        "HISTORY_DB_PATH": str(_TEST_ROOT / "history.db"),
        "JWT_SECRET": "yiqiao-test-only-jwt-secret-at-least-32-bytes",
        "MEMORY_IMPORT_STORAGE_ROOT": str(_TEST_ROOT / "memory-imports"),
        "POSTGRES_PASSWORD": "yiqiao-test-only-postgres-password",
        "YIQIAO_TELEMETRY": "false",
        "YIQIAO_DIR": str(_TEST_ROOT / "yiqiao-state"),
        "MEM0_TELEMETRY": "false",
    }
)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_ROOT, ignore_errors=True)
