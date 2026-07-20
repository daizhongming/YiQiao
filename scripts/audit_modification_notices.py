"""Audit Apache-2.0 modification notices against the YiQiao upstream base."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

BASE_COMMIT = "cd79fa8914b5b1cf66daacc957d826065df57df8"
UPSTREAM_REPOSITORY = "https://github.com/mem0ai/mem0.git"
NOTICE_TEXT = "This file was modified in 2026 by YiQiao contributors. See NOTICE."
JSON_NOTICE_KEY = "_yiqiaoModificationNotice"
PNG_NOTICE_KEY = b"Modification Notice"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MANIFEST_PATH = Path("MODIFICATIONS.md")

HASH_SUFFIXES = {
    ".dockerignore",
    ".example",
    ".gitignore",
    ".ini",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
HASH_NAMES = {".dockerignore", ".gitignore", "Dockerfile", "Makefile"}
SLASH_SUFFIXES = {".mjs", ".ts", ".tsx"}
JSON_SUFFIXES = {".json", ".webmanifest"}
PYTHON_ENCODING_RE = re.compile(rb"coding[:=][ \t]*[-\w.]+")
DOCKER_DIRECTIVE_RE = re.compile(rb"#\s*(syntax|escape)\s*=", re.IGNORECASE)
XML_DECLARATION_RE = re.compile(rb"^<\?xml(?:\s[^?]*)?\?>", re.IGNORECASE)

# Added paths are reviewed as YiQiao-originated. Any future addition must be
# explicitly reviewed here so a low-similarity move cannot bypass section 4(b).
YIQIAO_ORIGINATED_PATHS = frozenset(
    """
.dockerignore
.github/workflows/full-stack.yml
.github/workflows/images.yml
.github/workflows/security.yml
.gitleaks.toml
BRANDING_EXCEPTIONS.md
CODE_OF_CONDUCT.md
docs/yiqiao/MIGRATION.md
docs/yiqiao/OPERATIONS.md
docs/yiqiao/README.md
docs/yiqiao/SECURITY_AUDIT.md
docs/yiqiao/TROUBLESHOOTING.md
MODIFICATIONS.md
NOTICE
OPEN_SOURCE_READINESS.md
scripts/audit_modification_notices.py
scripts/chat_import_artifact_compare.py
scripts/chat_import_benchmark.py
scripts/chat_import_quality_queries.sql
scripts/full_stack_smoke.py
scripts/import_chat_history.py
scripts/init.ps1
scripts/init.sh
scripts/verify_chat_import_consistency.py
server/alembic/versions/007_create_webhooks.py
server/alembic/versions/008_add_webhook_name.py
server/alembic/versions/009_add_project_scope.py
server/alembic/versions/010_usage_quotas.py
server/alembic/versions/011_request_event_details.py
server/alembic/versions/012_memory_import_jobs.py
server/alembic/versions/013_memory_import_job_leases.py
server/alembic/versions/014_memory_import_workspace_limits.py
server/alembic/versions/015_repair_import_source_retry_flags.py
server/alembic/versions/016_memory_import_storage_quota_snapshot.py
server/alembic/versions/017_boss_helper_pairing.py
server/chat_import.py
server/dashboard/.prettierignore
server/dashboard/.prettierrc.json
server/dashboard/Dockerfile.dockerignore
server/dashboard/public/favicon.svg
server/dashboard/public/fonts/FONT_LICENSES.md
server/dashboard/public/fonts/OFL-1.1.txt
server/dashboard/src/app/(root)/dashboard/billing/page.tsx
server/dashboard/src/app/(root)/dashboard/entities/[type]/[id]/page.tsx
server/dashboard/src/app/(root)/dashboard/graph/galaxy-graph.tsx
server/dashboard/src/app/(root)/dashboard/graph/page.tsx
server/dashboard/src/app/(root)/dashboard/install/page.tsx
server/dashboard/src/app/(root)/dashboard/integrations/boss-helper/page.test.tsx
server/dashboard/src/app/(root)/dashboard/integrations/boss-helper/page.tsx
server/dashboard/src/app/(root)/dashboard/memories/memory-import-dialog.tsx
server/dashboard/src/app/(root)/dashboard/memory-exports/page.tsx
server/dashboard/src/app/(root)/dashboard/page.tsx
server/dashboard/src/app/(root)/dashboard/settings/[section]/page.tsx
server/dashboard/src/app/(root)/dashboard/settings/settings-client.tsx
server/dashboard/src/app/(root)/dashboard/settings/settings-cloud-client.tsx
server/dashboard/src/app/(root)/dashboard/settings/usage-limits/page.tsx
server/dashboard/src/app/(root)/playground/page.tsx
server/dashboard/src/app/icon.svg
server/dashboard/src/components/i18n/language-toggle.tsx
server/dashboard/src/components/requests/request-activity.tsx
server/dashboard/src/lib/i18n.tsx
server/dashboard/src/lib/language-preference.ts
server/dashboard/vitest.config.ts
server/docker-compose.build.yaml
server/docker-compose.e2e.yaml
server/docker-compose.production.yaml
server/import_quota.py
server/import_repository.py
server/init-db.sql
server/neo4j_graph.py
server/project_scope.py
server/routers/boss_helper.py
server/routers/exports.py
server/routers/graph.py
server/routers/memories.py
server/routers/playground.py
server/routers/settings.py
server/routers/usage.py
server/routers/webhooks.py
server/settings_store.py
server/usage_service.py
server/webhook_dispatcher.py
server/workspace.py
tests/conftest.py
tests/e2e/openai_stub.py
tests/memory/test_operation_context.py
tests/test_auth_account_routes.py
tests/test_boss_helper_migration.py
tests/test_boss_helper_pairing.py
tests/test_chat_import.py
tests/test_chat_import_artifact_compare.py
tests/test_chat_import_benchmark.py
tests/test_dashboard_font_licenses.py
tests/test_environment_isolation.py
tests/test_exports_router.py
tests/test_import_chat_history.py
tests/test_import_quota.py
tests/test_import_repository.py
tests/test_memories_router.py
tests/test_memory_imports_api.py
tests/test_neo4j_graph_memory.py
tests/test_playground_routes.py
tests/test_public_api.py
tests/test_release_legal_payload.py
tests/test_requests_entities_routes.py
tests/test_server_configuration.py
tests/test_server_telemetry_privacy.py
tests/test_settings_rbac_routes.py
tests/test_usage_quotas.py
tests/test_verify_chat_import_consistency.py
tests/test_webhooks_router.py
tests/test_workspace_rbac.py
tests/vector_stores/test_pgvector_import_queries.py
THIRD_PARTY_NOTICES.md
""".strip().splitlines()
)


class AuditError(RuntimeError):
    """The modification-notice audit cannot safely continue."""


@dataclass(frozen=True)
class PngChunk:
    kind: bytes
    payload: bytes
    start: int
    end: int


class JsonObject(list):
    """Preserve root object pairs so duplicate notice keys cannot collapse."""


def _git(root: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return result.stdout


def _parse_name_status(raw: bytes) -> list[tuple[str, Path]]:
    fields = raw.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()

    records: list[tuple[str, Path]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii")
        code = status[:1]
        if code == "M":
            if index + 1 >= len(fields):
                raise AuditError("truncated modified-file status from git diff")
            records.append((code, Path(fields[index + 1].decode("utf-8"))))
            index += 2
        elif code in {"R", "C"}:
            if index + 2 >= len(fields):
                raise AuditError("truncated rename/copy status from git diff")
            records.append((code, Path(fields[index + 2].decode("utf-8"))))
            index += 3
        else:
            raise AuditError(f"unexpected git diff status: {status}")
    return records


def _nul_paths(raw: bytes) -> set[Path]:
    return {Path(value.decode("utf-8")) for value in raw.split(b"\0") if value}


def _ensure_base_commit(root: Path, *, fetch_base: bool = False) -> None:
    available = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"],
        check=False,
        capture_output=True,
    )
    if available.returncode == 0:
        return

    if fetch_base:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "fetch",
                "--no-tags",
                "--depth=1",
                UPSTREAM_REPOSITORY,
                BASE_COMMIT,
            ],
            check=True,
            capture_output=True,
        )
        available = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"],
            check=False,
            capture_output=True,
        )
        if available.returncode == 0:
            return

    raise AuditError(f"upstream base {BASE_COMMIT} is unavailable; rerun with --fetch-base")


def modified_paths(root: Path, *, fetch_base: bool = False) -> list[Path]:
    _ensure_base_commit(root, fetch_base=fetch_base)

    changed_raw = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--find-renames",
        "--find-copies",
        "--find-copies-harder",
        "-l0",
        "--diff-filter=MRC",
        "--name-status",
        "-z",
        BASE_COMMIT,
        "--",
    )
    added_raw = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--diff-filter=A",
        "--name-only",
        "-z",
        BASE_COMMIT,
        "--",
    )
    untracked_raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    assert isinstance(changed_raw, bytes)
    assert isinstance(added_raw, bytes)
    assert isinstance(untracked_raw, bytes)

    records = _parse_name_status(changed_raw)
    renamed_or_copied = {path for status, path in records if status in {"R", "C"}}
    originated = (_nul_paths(added_raw) | _nul_paths(untracked_raw)) - renamed_or_copied
    expected_originated = {Path(path) for path in YIQIAO_ORIGINATED_PATHS}
    unreviewed = sorted(originated - expected_originated, key=lambda path: path.as_posix())
    stale = sorted(expected_originated - originated, key=lambda path: path.as_posix())
    if unreviewed or stale:
        details = [f"unreviewed added path: {path.as_posix()}" for path in unreviewed]
        details.extend(f"stale originated-path allowlist entry: {path.as_posix()}" for path in stale)
        raise AuditError("YiQiao-originated path inventory mismatch; " + "; ".join(details))

    return sorted({path for _, path in records}, key=lambda path: path.as_posix())


def notice_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix == ".svg":
        return "xml"
    if suffix == ".css":
        return "css"
    if suffix in SLASH_SUFFIXES:
        return "slash"
    if suffix in JSON_SUFFIXES:
        return "json"
    if suffix == ".png":
        return "png"
    if suffix in HASH_SUFFIXES or path.name in HASH_NAMES:
        return "hash"
    raise AuditError(f"unsupported modified-file format: {path.as_posix()}")


def _newline(raw: bytes) -> bytes:
    crlf = raw.find(b"\r\n")
    lf = raw.find(b"\n")
    if crlf >= 0 and (lf < 0 or crlf <= lf):
        return b"\r\n"
    return b"\n"


def _without_bom(raw: bytes) -> tuple[bytes, bytes]:
    bom = b"\xef\xbb\xbf"
    if raw.startswith(bom):
        return bom, raw[len(bom) :]
    return b"", raw


def rendered_notice(path: Path) -> str:
    kind = notice_kind(path)
    if kind == "markdown":
        return f"> **Modification notice:** {NOTICE_TEXT}"
    if kind == "xml":
        return f"<!-- {NOTICE_TEXT} -->"
    if kind == "css":
        return f"/* {NOTICE_TEXT} */"
    if kind == "slash":
        return f"// {NOTICE_TEXT}"
    if kind == "hash":
        return f"# {NOTICE_TEXT}"
    raise AuditError(f"{path.as_posix()} does not use a text notice")


def _protected_line_count(path: Path, lines: list[bytes]) -> int:
    protected = 0
    if lines and lines[0].startswith(b"#!"):
        protected = 1
    if path.suffix.lower() == ".py":
        for index, line in enumerate(lines[:2]):
            if PYTHON_ENCODING_RE.search(line):
                protected = max(protected, index + 1)
    if path.name == "Dockerfile":
        for index, line in enumerate(lines):
            if DOCKER_DIRECTIVE_RE.match(line.strip()):
                protected = index + 1
                continue
            break
    if path.suffix.lower() == ".svg" and lines and lines[0].startswith(b"<?xml"):
        protected = max(protected, 1)
    return protected


def _has_structural_text_notice(path: Path, raw: bytes) -> bool:
    expected = rendered_notice(path).encode("utf-8")
    _, content = _without_bom(raw)
    lines = content.splitlines()
    if notice_kind(path) == "markdown" and lines and lines[0].lstrip().startswith(b"# "):
        notice_index = 1
    else:
        notice_index = _protected_line_count(path, content.splitlines(keepends=True))
    return notice_index < len(lines) and lines[notice_index] == expected


def apply_text_notice(path: Path, raw: bytes) -> bytes:
    expected = rendered_notice(path).encode("utf-8")
    if _has_structural_text_notice(path, raw):
        return raw

    bom, content = _without_bom(raw)
    newline = _newline(content)
    lines = content.splitlines(keepends=True)

    if notice_kind(path) == "xml":
        declaration = XML_DECLARATION_RE.match(content)
        if declaration:
            remainder = content[declaration.end() :]
            if remainder.startswith(b"\r\n"):
                remainder = remainder[2:]
            elif remainder.startswith(b"\n"):
                remainder = remainder[1:]
            return bom + content[: declaration.end()] + newline + expected + newline + newline + remainder

    if notice_kind(path) == "markdown" and lines and lines[0].lstrip().startswith(b"# "):
        return bom + lines[0] + expected + newline + b"".join(lines[1:])

    protected = _protected_line_count(path, lines)
    return bom + b"".join(lines[:protected]) + expected + newline + newline + b"".join(lines[protected:])


def _json_object(path: Path, raw: bytes) -> JsonObject:
    parsed = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=JsonObject)
    if not isinstance(parsed, JsonObject):
        raise AuditError(f"JSON root must be an object: {path.as_posix()}")
    return parsed


def apply_json_notice(path: Path, raw: bytes) -> bytes:
    parsed = _json_object(path, raw)
    notice_values = [value for key, value in parsed if key == JSON_NOTICE_KEY]
    if notice_values:
        if len(notice_values) != 1 or notice_values[0] != NOTICE_TEXT:
            raise AuditError(f"incorrect JSON notice: {path.as_posix()}")
        return raw

    bom, content = _without_bom(raw)
    object_start = content.find(b"{")
    if object_start < 0 or content[:object_start].strip():
        raise AuditError(f"cannot safely locate JSON object start: {path.as_posix()}")
    newline = _newline(content)
    field = f'  "{JSON_NOTICE_KEY}": {json.dumps(NOTICE_TEXT)}'.encode()
    if parsed:
        return bom + content[: object_start + 1] + newline + field + b"," + content[object_start + 1 :]

    object_end = content.rfind(b"}")
    if object_end <= object_start or content[object_start + 1 : object_end].strip():
        raise AuditError(f"cannot safely locate empty JSON object end: {path.as_posix()}")
    return bom + content[: object_start + 1] + newline + field + newline + content[object_end:]


def png_chunks(path: Path, raw: bytes) -> list[PngChunk]:
    if not raw.startswith(PNG_SIGNATURE):
        raise AuditError(f"invalid PNG signature: {path.as_posix()}")

    chunks: list[PngChunk] = []
    offset = len(PNG_SIGNATURE)
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise AuditError(f"truncated PNG chunk: {path.as_posix()}")
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(raw):
            raise AuditError(f"invalid PNG chunk length: {path.as_posix()}")
        kind = raw[offset + 4 : offset + 8]
        payload = raw[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", raw[offset + 8 + length : end])[0]
        actual_crc = binascii.crc32(kind + payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise AuditError(f"invalid PNG chunk CRC: {path.as_posix()}:{kind!r}")
        chunks.append(PngChunk(kind=kind, payload=payload, start=offset, end=end))
        offset = end
        if kind == b"IEND":
            break

    if not chunks or chunks[0].kind != b"IHDR" or chunks[-1].kind != b"IEND" or offset != len(raw):
        raise AuditError(f"invalid PNG chunk order: {path.as_posix()}")
    return chunks


def apply_png_notice(path: Path, raw: bytes) -> bytes:
    chunks = png_chunks(path, raw)
    expected_payload = PNG_NOTICE_KEY + b"\0" + NOTICE_TEXT.encode("latin-1")
    notices = [chunk for chunk in chunks if chunk.kind == b"tEXt" and chunk.payload.startswith(PNG_NOTICE_KEY + b"\0")]
    if notices:
        if len(notices) != 1 or notices[0].payload != expected_payload:
            raise AuditError(f"incorrect PNG modification notice: {path.as_posix()}")
        return raw

    kind = b"tEXt"
    crc = binascii.crc32(kind + expected_payload) & 0xFFFFFFFF
    encoded = struct.pack(">I", len(expected_payload)) + kind + expected_payload + struct.pack(">I", crc)
    insert_at = chunks[0].end
    return raw[:insert_at] + encoded + raw[insert_at:]


def apply_notice(root: Path, relative: Path) -> None:
    path = root / relative
    raw = path.read_bytes()
    kind = notice_kind(relative)
    if kind == "json":
        updated = apply_json_notice(relative, raw)
    elif kind == "png":
        updated = apply_png_notice(relative, raw)
    else:
        updated = apply_text_notice(relative, raw)
    if updated != raw:
        path.write_bytes(updated)


def check_notice(root: Path, relative: Path) -> str | None:
    path = root / relative
    raw = path.read_bytes()
    kind = notice_kind(relative)
    if kind == "json":
        parsed = _json_object(relative, raw)
        notice_values = [value for key, value in parsed if key == JSON_NOTICE_KEY]
        if len(notice_values) != 1 or notice_values[0] != NOTICE_TEXT:
            return "missing or incorrect JSON notice"
        if not parsed or parsed[0][0] != JSON_NOTICE_KEY:
            return "JSON notice is not the first property"
        return None
    if kind == "png":
        chunks = png_chunks(relative, raw)
        expected = PNG_NOTICE_KEY + b"\0" + NOTICE_TEXT.encode("latin-1")
        notices = [
            chunk for chunk in chunks if chunk.kind == b"tEXt" and chunk.payload.startswith(PNG_NOTICE_KEY + b"\0")
        ]
        if len(notices) != 1 or notices[0].payload != expected:
            return "missing, duplicate, or conflicting PNG notice"
        if chunks.index(notices[0]) != 1:
            return "PNG notice is not immediately after IHDR"
        return None

    if kind == "xml":
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            return f"invalid XML: {exc}"
    if not _has_structural_text_notice(relative, raw):
        return "notice is not a top-level comment or visible Markdown line at the required position"
    expected = rendered_notice(relative).encode("utf-8")
    if raw.count(expected) != 1:
        return "duplicate or ambiguous text notice"
    return None


def _manifest_matches(actual: bytes, expected: bytes) -> bool:
    normalized = actual.replace(b"\r\n", b"\n")
    return b"\r" not in normalized and normalized == expected


def _manifest_for_write(path: Path, expected: bytes) -> bytes:
    if path.is_file():
        current = path.read_bytes()
        normalized = current.replace(b"\r\n", b"\n")
        if b"\r\n" in current and b"\r" not in normalized:
            return expected.replace(b"\n", b"\r\n")
    return expected


def manifest_content(paths: list[Path]) -> bytes:
    path_digest = hashlib.sha256("\n".join(path.as_posix() for path in paths).encode()).hexdigest()
    labels = {
        "css": "CSS comment",
        "hash": "source comment",
        "json": "JSON metadata",
        "markdown": "visible Markdown notice",
        "png": "PNG tEXt metadata",
        "slash": "source comment",
        "xml": "XML comment",
    }
    lines = [
        "# YiQiao Modification Record",
        "",
        "YiQiao is derived from the upstream Mem0 work at this commit:",
        "",
        f"`{BASE_COMMIT}`",
        "",
        "In accordance with Apache License 2.0 section 4(b),",
        "each upstream file changed by YiQiao carries this statement:",
        "",
        f"> {NOTICE_TEXT}",
        "",
        "Text and XML files carry the statement near the top of the file. JSON",
        f"objects use the first `{JSON_NOTICE_KEY}` property. PNG files use a",
        f"standard `{PNG_NOTICE_KEY.decode()}` `tEXt` chunk immediately after",
        "`IHDR`, without changing image data. Git history is supplementary and",
        "is not the sole modification record.",
        "",
        "Run `python scripts/audit_modification_notices.py` from a complete Git",
        "checkout to verify this list and every embedded notice. Unknown modified",
        "file formats fail closed.",
        "",
        f"Modified upstream files: **{len(paths)}**",
        "",
        f"Path-list SHA-256: `{path_digest.upper()}`",
        "",
        "## Files",
        "",
    ]
    lines.extend(f"- `{path.as_posix()}` ({labels[notice_kind(path)]})" for path in paths)
    return ("\n".join(lines) + "\n").encode("utf-8")


def audit(root: Path, paths: list[Path]) -> list[str]:
    failures: list[str] = []
    for relative in paths:
        try:
            failure = check_notice(root, relative)
        except (AuditError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            failure = str(exc)
        if failure:
            failures.append(f"{relative.as_posix()}: {failure}")

    expected_manifest = manifest_content(paths)
    manifest = root / MANIFEST_PATH
    if not manifest.is_file():
        failures.append(f"{MANIFEST_PATH.as_posix()}: missing")
    elif not _manifest_matches(manifest.read_bytes(), expected_manifest):
        failures.append(f"{MANIFEST_PATH.as_posix()}: content does not match the current upstream diff")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="insert missing notices and regenerate MODIFICATIONS.md before auditing",
    )
    parser.add_argument(
        "--fetch-base",
        action="store_true",
        help=f"fetch the pinned upstream base from {UPSTREAM_REPOSITORY} when absent",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    try:
        paths = modified_paths(root, fetch_base=args.fetch_base)
        if args.apply:
            for relative in paths:
                apply_notice(root, relative)
            manifest_path = root / MANIFEST_PATH
            manifest_path.write_bytes(_manifest_for_write(manifest_path, manifest_content(paths)))
        failures = audit(root, paths)
    except (AuditError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"modification-notice audit failed: {exc}", file=sys.stderr)
        return 1

    if failures:
        print("modification-notice audit failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    counts: dict[str, int] = {}
    for path in paths:
        kind = notice_kind(path)
        counts[kind] = counts.get(kind, 0) + 1
    summary = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    print(f"modification_notices=PASS files={len(paths)} originated_files={len(YIQIAO_ORIGINATED_PATHS)} {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
