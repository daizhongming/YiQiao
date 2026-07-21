#!/usr/bin/env python3
"""Validate YiQiao's English and Simplified Chinese documentation contract."""

from __future__ import annotations

import argparse
import fnmatch
import html
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

PAIR_START = "<!-- docs-localization:pairs:start -->"
PAIR_END = "<!-- docs-localization:pairs:end -->"
EXCLUSION_START = "<!-- docs-localization:exclusions:start -->"
EXCLUSION_END = "<!-- docs-localization:exclusions:end -->"
DEFAULT_MANIFEST = Path("docs/yiqiao/DOCUMENTATION_COVERAGE.md")
LEGAL_DISCLAIMER = "非官方参考译文，发生歧义时以英文原文为准"

IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".next",
        ".nox",
        ".playwright-cli",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".turbo",
        ".venv",
        "__pycache__",
        "artifacts",
        "backups",
        "blob-report",
        "build",
        "coverage",
        "dist",
        "history",
        "htmlcov",
        "logs",
        "node_modules",
        "output",
        "outputs",
        "playwright-report",
        "qdrant_storage",
        "site",
        "test-results",
        "venv",
    }
)
SHELL_LANGUAGES = frozenset({"bash", "powershell", "pwsh", "sh", "shell"})
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
HTML_HREF_RE = re.compile(r"<a\b[^>]*?\s+href\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
HTML_ID_RE = re.compile(r"<[A-Za-z][^>]*?\s+(?:id|name)\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*([^\s]*)[^\r\n]*$")
GLOB_MAGIC_RE = re.compile(r"[*?\[]")
LANGUAGE_SUFFIX_RE = re.compile(r"\.zh-CN\.(?:md|mdx|ya?ml)$", re.IGNORECASE)
STALE_ENGLISH_REF_RE = re.compile(r"\.en\.(?:md|mdx)(?:\b|#)", re.IGNORECASE)


@dataclass(frozen=True)
class CoverageEntry:
    source: str
    target: str
    kind: str
    reciprocal: str
    shell_blocks: str
    translation: str
    validation: str


@dataclass(frozen=True)
class CoverageInventory:
    entries: tuple[CoverageEntry, ...]
    exclusions: tuple[str, ...]


@dataclass(frozen=True)
class FenceBlock:
    language: str
    content: str


class InventoryError(ValueError):
    """Raised when the coverage document cannot be parsed safely."""


def _strip_code(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value


def _table_rows(text: str, start: str, end: str, path: Path) -> list[list[str]]:
    try:
        body = text.split(start, 1)[1].split(end, 1)[0]
    except IndexError as exc:
        raise InventoryError(f"{path}: missing inventory markers {start!r} and {end!r}") from exc

    rows: list[list[str]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        raise InventoryError(f"{path}: inventory table is empty")
    return rows[1:]


def _validate_relative_path(value: str, path: Path) -> None:
    if not value or "\\" in value:
        raise InventoryError(f"{path}: inventory path must be a non-empty POSIX path: {value!r}")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise InventoryError(f"{path}: inventory path escapes the repository: {value!r}")


def parse_coverage(path: Path) -> CoverageInventory:
    text = path.read_text(encoding="utf-8")
    pair_rows = _table_rows(text, PAIR_START, PAIR_END, path)
    exclusion_rows = _table_rows(text, EXCLUSION_START, EXCLUSION_END, path)

    entries: list[CoverageEntry] = []
    for row in pair_rows:
        if len(row) != 7:
            raise InventoryError(f"{path}: expected seven pair columns, found {len(row)} in {row!r}")
        values = [_strip_code(value) for value in row]
        entry = CoverageEntry(*values)
        _validate_relative_path(entry.source, path)
        _validate_relative_path(entry.target, path)
        if entry.kind not in {"env", "issue-form", "legal", "markdown"}:
            raise InventoryError(f"{path}: unsupported documentation kind {entry.kind!r}")
        if entry.reciprocal not in {"legal-exception", "not-applicable", "required"}:
            raise InventoryError(f"{path}: unsupported reciprocal policy {entry.reciprocal!r}")
        if entry.shell_blocks not in {"not-applicable", "required"}:
            raise InventoryError(f"{path}: unsupported shell-block policy {entry.shell_blocks!r}")
        if entry.translation != "complete" or entry.validation != "pass":
            raise InventoryError(
                f"{path}: {entry.source} must be marked complete/pass, found {entry.translation}/{entry.validation}"
            )
        entries.append(entry)

    sources = [entry.source for entry in entries]
    targets = [entry.target for entry in entries]
    if len(sources) != len(set(sources)):
        raise InventoryError(f"{path}: duplicate English source in coverage table")
    if len(targets) != len(set(targets)):
        raise InventoryError(f"{path}: duplicate Chinese target in coverage table")

    exclusions: list[str] = []
    for row in exclusion_rows:
        if len(row) < 2:
            raise InventoryError(f"{path}: exclusion row must include a path and reason: {row!r}")
        pattern = _strip_code(row[0])
        if not pattern:
            raise InventoryError(f"{path}: exclusion pattern cannot be empty")
        exclusions.append(pattern)
    if len(exclusions) != len(set(exclusions)):
        raise InventoryError(f"{path}: duplicate exclusion in coverage table")

    return CoverageInventory(entries=tuple(entries), exclusions=tuple(exclusions))


def _iter_repository_files(root: Path):
    for directory, names, filenames in os.walk(root):
        names[:] = [name for name in names if name.lower() not in IGNORED_DIRECTORY_NAMES]
        base = Path(directory)
        for filename in filenames:
            yield base / filename


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_chinese_document(path: str) -> bool:
    return bool(LANGUAGE_SUFFIX_RE.search(path)) or path == "server/.env.example.zh-CN"


def discover_user_facing_sources(root: Path, exclusions: tuple[str, ...]) -> set[str]:
    sources: set[str] = set()
    for path in _iter_repository_files(root):
        relative = _relative(root, path)
        if _matches_any(relative, exclusions):
            continue
        lower = relative.lower()
        if lower.endswith((".md", ".mdx")) and not _is_chinese_document(relative):
            sources.add(relative)
            continue
        if relative.startswith(".github/ISSUE_TEMPLATE/") and lower.endswith((".yml", ".yaml")):
            if not _is_chinese_document(relative) and Path(relative).name != "config.yml":
                sources.add(relative)
    for relative in ("LICENSE", "NOTICE", "server/.env.example"):
        if (root / relative).is_file() and not _matches_any(relative, exclusions):
            sources.add(relative)
    return sources


def discover_chinese_targets(root: Path, exclusions: tuple[str, ...]) -> set[str]:
    targets: set[str] = set()
    for path in _iter_repository_files(root):
        relative = _relative(root, path)
        if _matches_any(relative, exclusions):
            continue
        if _is_chinese_document(relative):
            targets.add(relative)
    return targets


def extract_fenced_blocks(text: str, path: Path | None = None) -> tuple[FenceBlock, ...]:
    blocks: list[FenceBlock] = []
    opening: str | None = None
    language = ""
    content: list[str] = []
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if opening is None:
            if match:
                opening = match.group(1)
                language = match.group(2)
                content = []
            continue
        if match and match.group(1)[0] == opening[0] and len(match.group(1)) >= len(opening):
            blocks.append(FenceBlock(language=language, content="\n".join(content)))
            opening = None
            language = ""
            content = []
            continue
        content.append(line)
    if opening is not None:
        label = str(path) if path is not None else "Markdown text"
        raise ValueError(f"{label}: unclosed fenced code block")
    return tuple(blocks)


def shell_fenced_blocks(text: str, path: Path | None = None) -> tuple[FenceBlock, ...]:
    return tuple(block for block in extract_fenced_blocks(text, path) if block.language.casefold() in SHELL_LANGUAGES)


def _markdown_without_fences(text: str) -> str:
    lines: list[str] = []
    opening: str | None = None
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if opening is None:
            if match:
                opening = match.group(1)
                lines.append("")
            else:
                lines.append(line)
            continue
        if match and match.group(1)[0] == opening[0] and len(match.group(1)) >= len(opening):
            opening = None
        lines.append("")
    return "\n".join(lines)


def markdown_link_destinations(text: str) -> tuple[str, ...]:
    visible = _markdown_without_fences(text)
    destinations = [match.group(1).strip() for match in MARKDOWN_LINK_RE.finditer(visible)]
    destinations.extend(match.group(2).strip() for match in HTML_HREF_RE.finditer(visible))
    return tuple(destinations)


def _clean_destination(destination: str) -> str:
    destination = html.unescape(destination.strip())
    if destination.startswith("<") and ">" in destination:
        return destination[1 : destination.index(">")]
    return destination.split(maxsplit=1)[0]


def _local_link(root: Path, source: Path, destination: str) -> tuple[Path, str] | None:
    destination = _clean_destination(destination)
    if destination.startswith("//"):
        return None
    parsed = urlsplit(destination)
    if parsed.scheme or parsed.netloc:
        return None
    raw_path = unquote(parsed.path)
    if not raw_path:
        target = source
    elif raw_path.startswith("/"):
        target = root / raw_path.lstrip("/")
    else:
        target = source.parent / raw_path
    target = target.resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return target, unquote(parsed.fragment)
    return target, unquote(parsed.fragment)


def _link_resolves_to(root: Path, source: Path, text: str, expected: Path) -> bool:
    expected = expected.resolve()
    for destination in markdown_link_destinations(text):
        local = _local_link(root, source, destination)
        if local is not None and local[0] == expected:
            return True
    return False


def github_slug(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("`", "").replace("*", "").replace("~", "")
    output: list[str] = []
    pending_space = False
    for character in value.casefold().strip():
        if character.isspace():
            pending_space = True
            continue
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"} or character in {"-", "_"}:
            if pending_space and output and output[-1] != "-":
                output.append("-")
            output.append(character)
        pending_space = False
    return "".join(output).strip("-")


def markdown_anchors(text: str) -> set[str]:
    visible = _markdown_without_fences(text)
    anchors = {match.group(2) for match in HTML_ID_RE.finditer(visible)}
    counts: dict[str, int] = {}
    for line in visible.splitlines():
        match = ATX_HEADING_RE.match(line)
        if not match:
            continue
        base = github_slug(match.group(1))
        if not base:
            continue
        index = counts.get(base, 0)
        counts[base] = index + 1
        anchors.add(base if index == 0 else f"{base}-{index}")
    return anchors


def validate_local_links(root: Path, paths: list[Path]) -> list[str]:
    errors: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for source in paths:
        text = source.read_text(encoding="utf-8")
        for destination in markdown_link_destinations(text):
            local = _local_link(root, source, destination)
            if local is None:
                continue
            target, fragment = local
            label = _relative(root, source)
            try:
                target.relative_to(root.resolve())
            except ValueError:
                errors.append(f"{label}: local link escapes the repository: {destination}")
                continue
            if not target.exists():
                errors.append(f"{label}: local link target does not exist: {destination}")
                continue
            if fragment and target.suffix.casefold() in {".md", ".mdx"}:
                anchors = anchor_cache.setdefault(target, markdown_anchors(target.read_text(encoding="utf-8")))
                if fragment not in anchors:
                    errors.append(f"{label}: local link anchor does not exist: {destination}")
    return errors


def _top_level_yaml_value(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip(" \"'") if match else None


def _issue_form_shape(text: str) -> tuple[tuple[str, str | None, tuple[bool, ...]], ...]:
    items: list[tuple[str, str | None, tuple[bool, ...]]] = []
    matches = list(re.finditer(r"^\s+-\s+type:\s*([^\s#]+)", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.end() : end]
        identifier_match = re.search(r"^\s+id:\s*([^\s#]+)", block, re.MULTILINE)
        required = tuple(
            value.casefold() == "true"
            for value in re.findall(r"^\s+required:\s*(true|false)\s*$", block, re.MULTILINE | re.IGNORECASE)
        )
        items.append((match.group(1), identifier_match.group(1) if identifier_match else None, required))
    return tuple(items)


def validate_issue_form_pair(root: Path, source: Path, target: Path) -> list[str]:
    errors: list[str] = []
    source_text = source.read_text(encoding="utf-8")
    target_text = target.read_text(encoding="utf-8")
    for path, text in ((source, source_text), (target, target_text)):
        label = _relative(root, path)
        for key in ("name", "description"):
            if not _top_level_yaml_value(text, key):
                errors.append(f"{label}: Issue Form is missing top-level {key!r}")
        if not re.search(r"^body:\s*$", text, re.MULTILINE):
            errors.append(f"{label}: Issue Form is missing top-level 'body'")
        if not re.search(r"^\s+required:\s*true\s*$", text, re.MULTILINE | re.IGNORECASE):
            errors.append(f"{label}: Issue Form has no required field")
    source_shape = _issue_form_shape(source_text)
    target_shape = _issue_form_shape(target_text)
    if source_shape != target_shape:
        errors.append(
            f"{_relative(root, source)}: Issue Form type/id/required structure differs from {_relative(root, target)}"
        )
    if target.name not in source_text:
        errors.append(f"{_relative(root, source)}: Issue Form does not link to {target.name}")
    if source.name not in target_text:
        errors.append(f"{_relative(root, target)}: Issue Form does not link to {source.name}")
    return errors


def _env_assignments(text: str) -> tuple[str, ...]:
    return tuple(line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def _validate_entry(root: Path, entry: CoverageEntry) -> list[str]:
    errors: list[str] = []
    source = root / entry.source
    target = root / entry.target
    if not source.is_file():
        errors.append(f"coverage source does not exist: {entry.source}")
    if not target.is_file():
        errors.append(f"coverage target does not exist: {entry.target}")
    if errors:
        return errors

    source_text = source.read_text(encoding="utf-8")
    target_text = target.read_text(encoding="utf-8")
    if entry.kind in {"legal", "markdown"}:
        try:
            extract_fenced_blocks(source_text, source)
            extract_fenced_blocks(target_text, target)
        except ValueError as exc:
            errors.append(str(exc))
    if entry.kind == "markdown" and entry.reciprocal == "required":
        if not _link_resolves_to(root, source, source_text, target):
            errors.append(f"{entry.source}: missing language link to {entry.target}")
        if not _link_resolves_to(root, target, target_text, source):
            errors.append(f"{entry.target}: missing language link to {entry.source}")
    if entry.kind == "legal":
        if LEGAL_DISCLAIMER not in target_text:
            errors.append(f"{entry.target}: missing exact unofficial-reference disclaimer")
        if not _link_resolves_to(root, target, target_text, source):
            errors.append(f"{entry.target}: missing link to authoritative English source {entry.source}")
    if entry.shell_blocks == "required":
        try:
            source_blocks = shell_fenced_blocks(source_text, source)
            target_blocks = shell_fenced_blocks(target_text, target)
        except ValueError:
            pass
        else:
            if source_blocks != target_blocks:
                errors.append(
                    f"{entry.target}: shell/bash/PowerShell fenced blocks differ from {entry.source} "
                    f"({len(target_blocks)} != {len(source_blocks)} or content changed)"
                )
    if entry.kind == "issue-form":
        errors.extend(validate_issue_form_pair(root, source, target))
    if entry.kind == "env":
        if _env_assignments(source_text) != _env_assignments(target_text):
            errors.append(f"{entry.target}: executable assignments differ from {entry.source}")
        if target.name not in source_text:
            errors.append(f"{entry.source}: missing reference to {entry.target}")
        if source.name not in target_text:
            errors.append(f"{entry.target}: missing reference to {entry.source}")
    return errors


def _compare_manifests(english: CoverageInventory, chinese: CoverageInventory, chinese_path: str) -> list[str]:
    errors: list[str] = []
    if english.entries != chinese.entries:
        errors.append(f"{chinese_path}: pair table differs from the English coverage inventory")
    if english.exclusions != chinese.exclusions:
        errors.append(f"{chinese_path}: exclusion table differs from the English coverage inventory")
    return errors


def check_repository(root: Path, manifest: Path | None = None) -> list[str]:
    root = root.resolve()
    manifest = (manifest or (root / DEFAULT_MANIFEST)).resolve()
    errors: list[str] = []
    try:
        inventory = parse_coverage(manifest)
    except (InventoryError, OSError, UnicodeError) as exc:
        return [str(exc)]

    manifest_relative = _relative(root, manifest)
    manifest_entry = next((entry for entry in inventory.entries if entry.source == manifest_relative), None)
    if manifest_entry is None:
        errors.append(f"{manifest_relative}: coverage inventory does not include itself")
    else:
        chinese_manifest = root / manifest_entry.target
        try:
            chinese_inventory = parse_coverage(chinese_manifest)
        except (InventoryError, OSError, UnicodeError) as exc:
            errors.append(str(exc))
        else:
            errors.extend(_compare_manifests(inventory, chinese_inventory, manifest_entry.target))

    for pattern in inventory.exclusions:
        if not GLOB_MAGIC_RE.search(pattern) and not (root / pattern).exists():
            errors.append(f"stale exact exclusion does not exist: {pattern}")

    discovered_sources = discover_user_facing_sources(root, inventory.exclusions)
    inventory_sources = {entry.source for entry in inventory.entries}
    for path in sorted(discovered_sources - inventory_sources):
        errors.append(f"uncovered English user-facing document: {path}")
    for path in sorted(inventory_sources - discovered_sources):
        errors.append(f"stale coverage source is not discoverable: {path}")

    discovered_targets = discover_chinese_targets(root, inventory.exclusions)
    inventory_targets = {entry.target for entry in inventory.entries}
    for path in sorted(discovered_targets - inventory_targets):
        errors.append(f"unlisted Chinese documentation target: {path}")
    for path in sorted(inventory_targets - discovered_targets):
        errors.append(f"stale coverage target is not discoverable: {path}")

    for path in sorted(discovered_sources):
        if re.search(r"\.en\.(?:md|mdx)$", path, re.IGNORECASE):
            errors.append(f"legacy .en documentation filename is not allowed: {path}")

    for entry in inventory.entries:
        errors.extend(_validate_entry(root, entry))

    documentation_markdown = sorted(
        {
            root / relative
            for entry in inventory.entries
            for relative in (entry.source, entry.target)
            if relative.lower().endswith((".md", ".mdx")) and (root / relative).is_file()
        }
    )
    errors.extend(validate_local_links(root, documentation_markdown))

    for entry in inventory.entries:
        for relative in (entry.source, entry.target):
            path = root / relative
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeError as exc:
                    errors.append(f"{relative}: is not valid UTF-8: {exc}")
                    continue
                if STALE_ENGLISH_REF_RE.search(text):
                    errors.append(f"{relative}: contains a stale .en.md or .en.mdx reference")

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    manifest = args.manifest
    if manifest is not None and not manifest.is_absolute():
        manifest = root / manifest
    errors = check_repository(root, manifest)
    if errors:
        print("Documentation localization check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    inventory = parse_coverage(manifest or (root / DEFAULT_MANIFEST))
    print(
        f"Documentation localization check passed: {len(inventory.entries)} pairs, "
        f"{len(inventory.exclusions)} exclusions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
