import binascii
import hashlib
import json
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import audit_modification_notices as notices

ROOT = Path(__file__).resolve().parents[1]
NOTICE_TEXT = "This file was modified in 2026 by YiQiao contributors. See NOTICE."


def _dockerfile_instructions(path: Path) -> list[str]:
    instructions: list[str] = []
    pending = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append(pending)
        pending = ""
    assert not pending
    return instructions


def _final_stage(path: Path) -> list[str]:
    instructions = _dockerfile_instructions(path)
    start = max(index for index, value in enumerate(instructions) if value.upper().startswith("FROM "))
    return instructions[start:]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def test_packaging_configs_include_modification_record():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "yiqiao"
    assert "MODIFICATIONS.md" in pyproject["project"]["license-files"]

    release = pyproject["tool"]["yiqiao"]["release"]
    assert release == {"publish": True, "channel": "pypi"}

    server_requirements = (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert f"yiqiao>={pyproject['project']['version']}" in server_requirements

    build = pyproject["tool"]["hatch"]["build"]
    assert "yiqiao/**/*.py" in build["include"]
    assert "yiqiao/**/*.py" in build["targets"]["sdist"]["include"]
    assert "yiqiao" in build["targets"]["wheel"]["packages"]
    assert "yiqiao" in build["targets"]["wheel"]["only-include"]

    dockerfiles = (ROOT / "server" / "Dockerfile", ROOT / "server" / "dashboard" / "Dockerfile")
    dashboard_ignore = (ROOT / "server" / "dashboard" / "Dockerfile.dockerignore").read_text(encoding="utf-8")

    legal_files = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "MODIFICATIONS.md")
    for path in dockerfiles:
        final_stage = _final_stage(path)
        legal_copy = next(
            instruction
            for instruction in final_stage
            if instruction.startswith("COPY ") and instruction.endswith(" /usr/share/licenses/yiqiao/")
        )
        final_run = "\n".join(instruction for instruction in final_stage if instruction.startswith("RUN "))
        for name in legal_files:
            assert name in legal_copy.split()
            assert f"test -s /usr/share/licenses/yiqiao/{name}" in final_run

    api_instructions = _dockerfile_instructions(dockerfiles[0])
    assert any(
        instruction == "COPY pyproject.toml README.md LICENSE NOTICE THIRD_PARTY_NOTICES.md MODIFICATIONS.md ./"
        for instruction in api_instructions
    )
    assert "COPY yiqiao ./yiqiao" in api_instructions
    assert "!MODIFICATIONS.md" in dashboard_ignore.splitlines()


def test_versioned_image_publication_is_bound_to_the_release_tag():
    workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert "fetch-tags: true" in workflow
    assert 'tag_ref="refs/tags/${RELEASE_VERSION}"' in workflow
    assert '"$tag_commit" != "$GITHUB_SHA"' in workflow
    assert "${{ inputs.version }}" in workflow


def test_python_package_publication_uses_pypi_trusted_publishing():
    workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text(encoding="utf-8")

    assert "publish-python-package:" in workflow
    assert "environment:" in workflow
    assert "name: pypi" in workflow
    assert "id-token: write" in workflow
    assert "python -m build" in workflow
    assert "python -m twine check --strict dist/*" in workflow
    assert "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33" in workflow


def test_git_name_status_includes_rename_and_copy_destinations():
    raw = b"M\0same.py\0R087\0old.py\0moved.py\0C100\0source.py\0copied.py\0"
    assert notices._parse_name_status(raw) == [
        ("M", Path("same.py")),
        ("R", Path("moved.py")),
        ("C", Path("copied.py")),
    ]


def test_modified_paths_fails_closed_for_low_similarity_move(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "YiQiao Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "copy_source.py").write_text("".join(f"copy_line_{index} = {index}\n" for index in range(40)))
    (repo / "rename_source.py").write_text("".join(f"rename_line_{index} = {index}\n" for index in range(40)))
    (repo / "heavy_source.py").write_text("".join(f"old_line_{index} = {index}\n" for index in range(40)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "rename_source.py").rename(repo / "renamed.py")
    with (repo / "renamed.py").open("a", encoding="utf-8") as handle:
        handle.write("rename_tail = True\n")
    (repo / "copied.py").write_bytes((repo / "copy_source.py").read_bytes())
    _git(repo, "add", "-A")
    monkeypatch.setattr(notices, "BASE_COMMIT", base)
    monkeypatch.setattr(notices, "YIQIAO_ORIGINATED_PATHS", frozenset())

    paths = notices.modified_paths(repo)
    assert Path("renamed.py") in paths
    assert Path("copied.py") in paths

    (repo / "heavy_source.py").unlink()
    (repo / "heavy_moved.py").write_text("".join(f"unrelated_{index} = 'z'\n" for index in range(40)))
    _git(repo, "add", "-A")
    with pytest.raises(notices.AuditError, match="unreviewed added path: heavy_moved.py"):
        notices.modified_paths(repo)


def test_modified_paths_keeps_reviewed_copy_classification_after_staging(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "YiQiao Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "source.txt").write_text("reviewed legal payload\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    (repo / "reviewed-copy.txt").write_bytes((repo / "source.txt").read_bytes())
    monkeypatch.setattr(notices, "BASE_COMMIT", base)
    monkeypatch.setattr(notices, "YIQIAO_ORIGINATED_PATHS", frozenset({"reviewed-copy.txt"}))

    assert notices.modified_paths(repo) == []
    _git(repo, "add", "reviewed-copy.txt")
    assert notices.modified_paths(repo) == []
    (repo / "source.txt").unlink()
    _git(repo, "add", "-A")
    assert notices.modified_paths(repo) == []


def test_text_notice_must_be_structural(tmp_path):
    relative = Path("example.py")
    disguised = f'value = "# {NOTICE_TEXT}"\n'.encode()
    (tmp_path / relative).write_bytes(disguised)
    assert notices.check_notice(tmp_path, relative) is not None

    updated = notices.apply_text_notice(relative, disguised)
    (tmp_path / relative).write_bytes(updated)
    assert notices.check_notice(tmp_path, relative) == "duplicate or ambiguous text notice"
    assert updated.splitlines()[0] == f"# {NOTICE_TEXT}".encode()

    clean = b"value = 'safe'\n"
    updated = notices.apply_text_notice(relative, clean)
    (tmp_path / relative).write_bytes(updated)
    assert notices.check_notice(tmp_path, relative) is None

    markdown = Path("example.md")
    fenced = f"# Title\n```text\n> **Modification notice:** {NOTICE_TEXT}\n```\n".encode()
    (tmp_path / markdown).write_bytes(fenced)
    assert notices.check_notice(tmp_path, markdown) is not None


def test_xml_declaration_remains_first_and_is_parsed(tmp_path):
    relative = Path("image.svg")
    raw = b'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg"/>\n'
    updated = notices.apply_text_notice(relative, raw)
    (tmp_path / relative).write_bytes(updated)
    assert updated.startswith(raw.splitlines(keepends=True)[0])
    assert updated.splitlines()[1] == f"<!-- {NOTICE_TEXT} -->".encode()
    assert notices.check_notice(tmp_path, relative) is None

    compact = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
    first = notices.apply_text_notice(relative, compact)
    second = notices.apply_text_notice(relative, first)
    assert first == second
    assert first.count(NOTICE_TEXT.encode()) == 1
    (tmp_path / relative).write_bytes(first)
    assert notices.check_notice(tmp_path, relative) is None


def test_empty_json_notice_is_valid_and_first(tmp_path):
    updated = notices.apply_json_notice(Path("empty.json"), b"{}\n")
    parsed = json.loads(updated)
    assert next(iter(parsed)) == notices.JSON_NOTICE_KEY
    assert parsed[notices.JSON_NOTICE_KEY] == NOTICE_TEXT

    duplicate = (
        f'{{"{notices.JSON_NOTICE_KEY}": "conflicting", "{notices.JSON_NOTICE_KEY}": {json.dumps(NOTICE_TEXT)}}}'
    ).encode()
    with pytest.raises(notices.AuditError, match="incorrect JSON notice"):
        notices.apply_json_notice(Path("duplicate.json"), duplicate)
    (tmp_path / "duplicate.json").write_bytes(duplicate)
    assert notices.check_notice(tmp_path, Path("duplicate.json")) == "missing or incorrect JSON notice"


def test_manifest_comparison_and_write_preserve_line_endings(tmp_path):
    expected = b"# Record\n\nBody\n"
    crlf = expected.replace(b"\n", b"\r\n")
    assert notices._manifest_matches(crlf, expected)
    assert not notices._manifest_matches(b"# Record\rBody\r", expected)
    assert not notices._manifest_matches(b"# Different\n\nBody\n", expected)

    manifest = tmp_path / "MODIFICATIONS.md"
    manifest.write_bytes(crlf)
    assert notices._manifest_for_write(manifest, expected) == crlf


def test_manifest_records_the_reviewed_originated_path_inventory():
    content = notices.manifest_content([]).decode("utf-8")
    originated_paths = sorted(notices.YIQIAO_ORIGINATED_PATHS)
    digest = hashlib.sha256("\n".join(originated_paths).encode()).hexdigest().upper()

    assert f"YiQiao-originated files: **{len(originated_paths)}**" in content
    assert f"Originated path-list SHA-256: `{digest}`" in content


def test_png_conflicting_notice_is_rejected(tmp_path):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = notices.PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IEND", b"")
    updated = notices.apply_png_notice(Path("image.png"), raw)
    chunks = notices.png_chunks(Path("image.png"), updated)
    conflicting = _png_chunk(b"tEXt", notices.PNG_NOTICE_KEY + b"\0conflicting value")
    updated = updated[: chunks[1].end] + conflicting + updated[chunks[1].end :]

    relative = Path("image.png")
    (tmp_path / relative).write_bytes(updated)
    assert notices.check_notice(tmp_path, relative) == "missing, duplicate, or conflicting PNG notice"
