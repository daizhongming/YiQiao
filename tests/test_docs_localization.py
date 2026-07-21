import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_docs_localization as docs

ROOT = Path(__file__).resolve().parents[1]


class DocumentationLocalizationTests(unittest.TestCase):
    def test_repository_contract_passes(self):
        self.assertEqual(docs.check_repository(ROOT), [])

    def test_coverage_manifest_is_complete_and_self_describing(self):
        inventory = docs.parse_coverage(ROOT / docs.DEFAULT_MANIFEST)
        self.assertEqual(len(inventory.entries), 24)
        by_source = {entry.source: entry for entry in inventory.entries}
        self.assertEqual(by_source["README.md"].target, "README.zh-CN.md")
        self.assertEqual(
            by_source["docs/yiqiao/DOCUMENTATION_COVERAGE.md"].target,
            "docs/yiqiao/DOCUMENTATION_COVERAGE.zh-CN.md",
        )
        self.assertEqual(by_source["LICENSE"].reciprocal, "legal-exception")

    def test_discovery_finds_public_sources_and_ignores_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide.md").write_text("# Guide\n", encoding="utf-8")
            (root / "guide.zh-CN.md").write_text("# 指南\n", encoding="utf-8")
            (root / "LICENSE").write_text("license\n", encoding="utf-8")
            (root / "NOTICE").write_text("notice\n", encoding="utf-8")
            (root / "server").mkdir()
            (root / "server" / ".env.example").write_text("VALUE=1\n", encoding="utf-8")
            issue_directory = root / ".github" / "ISSUE_TEMPLATE"
            issue_directory.mkdir(parents=True)
            (issue_directory / "bug.yml").write_text("name: Bug\n", encoding="utf-8")
            (issue_directory / "bug.zh-CN.yml").write_text("name: 缺陷\n", encoding="utf-8")
            for ignored in ("node_modules", "history", "dist"):
                path = root / ignored
                path.mkdir()
                (path / "README.md").write_text("# Generated\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "fixture.md").write_text("# Fixture\n", encoding="utf-8")

            sources = docs.discover_user_facing_sources(root, ("tests/**",))
            targets = docs.discover_chinese_targets(root, ("tests/**",))

            self.assertEqual(
                sources,
                {
                    ".github/ISSUE_TEMPLATE/bug.yml",
                    "LICENSE",
                    "NOTICE",
                    "guide.md",
                    "server/.env.example",
                },
            )
            self.assertEqual(targets, {".github/ISSUE_TEMPLATE/bug.zh-CN.yml", "guide.zh-CN.md"})

    def test_local_links_support_github_slugs_and_explicit_html_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guide.zh-CN.md"
            target = root / "target.zh-CN.md"
            target.write_text('# 恢复\n\n<a id="stable-anchor"></a>\n', encoding="utf-8")
            source.write_text(
                "[恢复](target.zh-CN.md#恢复)\n[稳定入口](target.zh-CN.md#stable-anchor)\n",
                encoding="utf-8",
            )
            self.assertEqual(docs.validate_local_links(root, [source]), [])

            source.write_text("[错误入口](target.zh-CN.md#restore)\n", encoding="utf-8")
            errors = docs.validate_local_links(root, [source])
            self.assertEqual(len(errors), 1)
            self.assertIn("anchor does not exist", errors[0])

    def test_repository_link_validation_includes_authoritative_english_sources(self):
        with mock.patch.object(docs, "validate_local_links", wraps=docs.validate_local_links) as validator:
            self.assertEqual(docs.check_repository(ROOT), [])

        validated_paths = set(validator.call_args.args[1])
        self.assertIn(ROOT / "README.md", validated_paths)
        self.assertIn(ROOT / "README.zh-CN.md", validated_paths)

    def test_shell_fenced_blocks_are_compared_exactly(self):
        english = "# Install\n\n```bash\nmake init\nmake start\n```\n"
        same = "# 安装\n\n```bash\nmake init\nmake start\n```\n"
        changed = "# 安装\n\n```bash\nmake init\nmake up\n```\n"

        self.assertEqual(docs.shell_fenced_blocks(english), docs.shell_fenced_blocks(same))
        self.assertNotEqual(docs.shell_fenced_blocks(english), docs.shell_fenced_blocks(changed))

    def test_issue_form_validation_requires_matching_structure_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            english = root / "bug.yml"
            chinese = root / "bug.zh-CN.yml"
            english.write_text(
                """name: Bug
description: Report a bug
body:
  - type: markdown
    attributes:
      value: bug.zh-CN.yml
  - type: textarea
    id: details
    validations:
      required: true
""",
                encoding="utf-8",
            )
            chinese.write_text(
                """name: 缺陷
description: 报告缺陷
body:
  - type: markdown
    attributes:
      value: bug.yml
  - type: textarea
    id: details
    validations:
      required: true
""",
                encoding="utf-8",
            )
            self.assertEqual(docs.validate_issue_form_pair(root, english, chinese), [])

            chinese.write_text(
                chinese.read_text(encoding="utf-8").replace("id: details", "id: detail"), encoding="utf-8"
            )
            errors = docs.validate_issue_form_pair(root, english, chinese)
            self.assertEqual(len(errors), 1)
            self.assertIn("structure differs", errors[0])

            chinese.write_text(
                chinese.read_text(encoding="utf-8")
                .replace("id: detail", "id: details")
                .replace("required: true", "required: false"),
                encoding="utf-8",
            )
            errors = docs.validate_issue_form_pair(root, english, chinese)
            self.assertEqual(len(errors), 2)
            self.assertTrue(any("structure differs" in error for error in errors))
            self.assertTrue(any("no required field" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
