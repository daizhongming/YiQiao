import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCUMENT_SURFACES = (
    Path("README.md"),
    Path("README.zh-CN.md"),
    Path("server/README.md"),
    Path("server/README.zh-CN.md"),
    Path("docs/yiqiao/README.md"),
    Path("docs/yiqiao/README.zh-CN.md"),
    Path("docs/yiqiao/OPERATIONS.md"),
    Path("docs/yiqiao/OPERATIONS.zh-CN.md"),
    Path("docs/yiqiao/TROUBLESHOOTING.md"),
    Path("docs/yiqiao/TROUBLESHOOTING.zh-CN.md"),
    Path("docs/yiqiao/SECURITY_AUDIT.md"),
    Path("docs/yiqiao/SECURITY_AUDIT.zh-CN.md"),
    Path("docs/yiqiao/LEGAL.md"),
    Path("docs/yiqiao/LEGAL.zh-CN.md"),
    Path("CONTRIBUTING.md"),
    Path("CONTRIBUTING.zh-CN.md"),
    Path("SECURITY.md"),
    Path("SECURITY.zh-CN.md"),
    Path("CODE_OF_CONDUCT.md"),
    Path("CODE_OF_CONDUCT.zh-CN.md"),
)

DEPLOYMENT_SURFACES = (Path("server/.env.example"), Path("server/.env.example.zh-CN"))

GITHUB_TEMPLATE_ENGLISH_SURFACES = (
    Path(".github/ISSUE_TEMPLATE/bug_report.yml"),
    Path(".github/ISSUE_TEMPLATE/documentation_issue.yml"),
    Path(".github/ISSUE_TEMPLATE/feature_request.yml"),
    Path(".github/PULL_REQUEST_TEMPLATE.md"),
)
GITHUB_TEMPLATE_CHINESE_SURFACES = (
    Path(".github/ISSUE_TEMPLATE/bug_report.zh-CN.yml"),
    Path(".github/ISSUE_TEMPLATE/documentation_issue.zh-CN.yml"),
    Path(".github/ISSUE_TEMPLATE/feature_request.zh-CN.yml"),
    Path(".github/PULL_REQUEST_TEMPLATE.zh-CN.md"),
)
GITHUB_TEMPLATE_SURFACES = (
    *GITHUB_TEMPLATE_ENGLISH_SURFACES,
    *GITHUB_TEMPLATE_CHINESE_SURFACES,
    Path(".github/ISSUE_TEMPLATE/config.yml"),
)

LEGAL_AND_COMPATIBILITY_EXCLUSIONS = frozenset(
    {
        Path("LICENSE"),
        Path("NOTICE"),
        Path("THIRD_PARTY_NOTICES.md"),
        Path("MODIFICATIONS.md"),
        Path("BRANDING_EXCEPTIONS.md"),
        Path("BRANDING_EXCEPTIONS.zh-CN.md"),
        Path("OPEN_SOURCE_READINESS.md"),
        Path("OPEN_SOURCE_READINESS.zh-CN.md"),
        Path("docs/yiqiao/MIGRATION.md"),
        Path("docs/yiqiao/MIGRATION.zh-CN.md"),
        Path("server/dashboard/public/fonts/FONT_LICENSES.md"),
        Path("server/dashboard/public/fonts/OFL-1.1.txt"),
    }
)

CHINESE_PRIMARY_SURFACES = (
    Path("README.zh-CN.md"),
    Path("server/README.zh-CN.md"),
    Path("docs/yiqiao/README.zh-CN.md"),
    Path("docs/yiqiao/OPERATIONS.zh-CN.md"),
    Path("docs/yiqiao/MIGRATION.zh-CN.md"),
    Path("docs/yiqiao/TROUBLESHOOTING.zh-CN.md"),
    Path("docs/yiqiao/SECURITY_AUDIT.zh-CN.md"),
    Path("docs/yiqiao/LEGAL.zh-CN.md"),
    Path("CONTRIBUTING.zh-CN.md"),
    Path("SECURITY.zh-CN.md"),
    Path("CODE_OF_CONDUCT.zh-CN.md"),
    *GITHUB_TEMPLATE_CHINESE_SURFACES,
    Path(".github/ISSUE_TEMPLATE/config.yml"),
)

DASHBOARD_TEXT_SUFFIXES = frozenset(
    {".css", ".js", ".json", ".jsx", ".md", ".mjs", ".svg", ".ts", ".tsx", ".txt", ".webmanifest"}
)
DASHBOARD_FONTS_DIR = Path("server/dashboard/public/fonts")
UPSTREAM_BRAND = re.compile(r"mem0", re.IGNORECASE)
CHINESE_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
PUBLIC_PYTHON_EXAMPLE = re.compile(
    r"```python\s*\r?\nfrom yiqiao import Memory, AsyncMemory\s*\r?\n```",
    re.MULTILINE,
)


def _is_dashboard_test_source(path: Path) -> bool:
    return ".test." in path.name or ".spec." in path.name or "__tests__" in path.parts


def _dashboard_surfaces() -> tuple[Path, ...]:
    files = [Path("server/dashboard/package.json")]
    for base in (Path("server/dashboard/src"), Path("server/dashboard/public")):
        absolute_base = ROOT / base
        for path in absolute_base.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if DASHBOARD_FONTS_DIR in (relative, *relative.parents):
                continue
            if base.name == "src" and _is_dashboard_test_source(relative):
                continue
            if path.suffix.lower() in DASHBOARD_TEXT_SUFFIXES:
                files.append(relative)
    return tuple(sorted(set(files), key=lambda path: path.as_posix()))


DASHBOARD_SURFACES = _dashboard_surfaces()
PUBLIC_BRAND_SURFACES = tuple(
    dict.fromkeys((*DOCUMENT_SURFACES, *DEPLOYMENT_SURFACES, *GITHUB_TEMPLATE_SURFACES, *DASHBOARD_SURFACES))
)


def _read_surface(relative_path: Path) -> str:
    path = ROOT / relative_path
    assert path.is_file(), f"Declared public surface is missing: {relative_path.as_posix()}"
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        pytest.fail(f"Public surface is not valid UTF-8: {relative_path.as_posix()}: {exc}")


@pytest.mark.parametrize("relative_path", PUBLIC_BRAND_SURFACES, ids=lambda path: path.as_posix())
def test_public_user_surfaces_do_not_expose_upstream_brand(relative_path: Path):
    text = _read_surface(relative_path)
    hits = [
        f"{relative_path.as_posix()}:{line_number}: {line.strip()}"
        for line_number, line in enumerate(text.splitlines(), start=1)
        if UPSTREAM_BRAND.search(line)
    ]

    assert not hits, "Upstream brand leaked into a public user surface:\n" + "\n".join(hits)


@pytest.mark.parametrize("relative_path", CHINESE_PRIMARY_SURFACES, ids=lambda path: path.as_posix())
def test_chinese_primary_surfaces_contain_chinese(relative_path: Path):
    text = _read_surface(relative_path)
    assert CHINESE_CHARACTER.search(text), f"Chinese primary surface contains no Chinese: {relative_path.as_posix()}"


@pytest.mark.parametrize(
    "relative_path", (Path("README.md"), Path("README.zh-CN.md")), ids=lambda path: path.as_posix()
)
def test_root_readmes_use_the_yiqiao_python_entry_point(relative_path: Path):
    text = _read_surface(relative_path)
    assert PUBLIC_PYTHON_EXAMPLE.search(text), (
        f"Public YiQiao Python example is missing or changed: {relative_path.as_posix()}"
    )


def test_legal_and_compatibility_material_is_outside_public_brand_scan():
    scanned = set(PUBLIC_BRAND_SURFACES)
    assert scanned.isdisjoint(LEGAL_AND_COMPATIBILITY_EXCLUSIONS)
    assert all(DASHBOARD_FONTS_DIR not in (path, *path.parents) for path in DASHBOARD_SURFACES)
