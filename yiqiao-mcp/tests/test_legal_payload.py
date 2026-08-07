from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MCP_ROOT = ROOT / "yiqiao-mcp"
LEGAL_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "MODIFICATIONS.md")


def test_companion_legal_files_match_the_repository_payload() -> None:
    for name in LEGAL_FILES:
        assert (MCP_ROOT / name).read_bytes() == (ROOT / name).read_bytes()


def test_companion_distribution_and_image_include_legal_files() -> None:
    with (MCP_ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    assert set(project["license-files"]) == set(LEGAL_FILES)

    dockerfile = (MCP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    copy_instruction = "COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md /usr/share/licenses/yiqiao/"
    assert copy_instruction in dockerfile
