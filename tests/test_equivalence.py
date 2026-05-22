"""Tests for source/translated repository equivalence verification."""

import json

import pytest


class TestEquivalenceVerifier:
    """Test deterministic equivalence checks."""

    def test_verify_equivalence_detects_missing_extra_and_binary_changes(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        (source / "README.md").write_text("# 标题\n", encoding="utf-8")
        (source / "only-source.md").write_text("source only", encoding="utf-8")
        (target / "README.md").write_text("# Title\n", encoding="utf-8")
        (target / "only-target.md").write_text("target only", encoding="utf-8")
        (source / "image.bin").write_bytes(b"\x00\x01\x02")
        (target / "image.bin").write_bytes(b"\x00\x01\x03")

        report = verify_equivalence(source, target)

        assert report.has_errors is True
        assert any(issue.issue_type == "missing_file" for issue in report.issues)
        assert any(issue.issue_type == "extra_file" for issue in report.issues)
        assert any(issue.issue_type == "binary_changed" for issue in report.issues)

    def test_verify_equivalence_preserves_markdown_code_and_urls(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        original = """# 使用说明

Visit https://example.com/docs.

```bash
pip install repo-translator
```
"""
        translated = """# Usage

Visit https://example.com/translated-docs.

```bash
pip cài đặt repo-translator
```
"""
        (source / "README.md").write_text(original, encoding="utf-8")
        (target / "README.md").write_text(translated, encoding="utf-8")

        report = verify_equivalence(source, target)

        issue_types = {issue.issue_type for issue in report.issues}
        assert "url_changed" in issue_types
        assert "code_block_changed" in issue_types

    def test_verify_equivalence_detects_invalid_translated_json(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        (source / "config.json").write_text('{"name": "测试"}', encoding="utf-8")
        (target / "config.json").write_text('{"name": "test",}', encoding="utf-8")

        report = verify_equivalence(source, target)

        assert any(issue.issue_type == "syntax_error" for issue in report.issues)

    def test_verify_equivalence_handles_broken_symlinks(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        missing = tmp_path / "missing.pdf"
        (source / "fixture.pdf").symlink_to(missing)
        (target / "fixture.pdf").symlink_to(missing)

        report = verify_equivalence(source, target)

        assert report.has_errors is False
        assert report.files_checked == 1

    def test_utf8_multibyte_at_probe_boundary_is_not_binary(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        text = "a" * 4094 + "中" + "\n"
        (source / "README.md").write_text(text, encoding="utf-8")
        (target / "README.md").write_text(text.replace("中", "C"), encoding="utf-8")

        report = verify_equivalence(source, target)

        assert not any(issue.issue_type == "binary_changed" for issue in report.issues)

    def test_env_var_check_ignores_common_acronyms_in_prose(self, tmp_path):
        from repo_translator.equivalence import verify_equivalence

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        (source / "README.md").write_text("# API 文档\n\n支持 OCR 和 JSON。", encoding="utf-8")
        (target / "README.md").write_text(
            "# API Docs\n\nSupports OCR, JSON and HTTP.", encoding="utf-8"
        )

        report = verify_equivalence(source, target)

        assert not any(issue.issue_type == "env_var_changed" for issue in report.issues)

    def test_verify_cli_writes_json_report_and_fails_on_error(self, runner, tmp_path):
        from repo_translator.cli import main

        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        output = tmp_path / "report.json"

        (source / "README.md").write_text("# 标题\n", encoding="utf-8")

        result = runner.invoke(
            main,
            [
                "verify",
                "--source",
                str(source),
                "--target",
                str(target),
                "--json-output",
                str(output),
                "--fail-on",
                "error",
            ],
        )

        assert result.exit_code == 1
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["files"]["source"] == 1
        assert data["issues"][0]["type"] == "missing_file"

    def test_vercel_ai_gateway_reviewer_defaults(self):
        from repo_translator.reviewers import AIReviewer

        reviewer = AIReviewer(engine="vercel-ai-gateway", api_key="test-key")

        assert reviewer.base_url == "https://ai-gateway.vercel.sh/v1"
        assert reviewer.model == "openai/gpt-4o-mini"


@pytest.fixture
def runner():
    from click.testing import CliRunner

    return CliRunner()
