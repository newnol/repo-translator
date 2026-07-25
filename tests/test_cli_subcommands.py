"""Unit tests for CLI subcommand argument handling (task 11.3).

Validates: Requirements 17.4, 17.8
"""

from click.testing import CliRunner

from repo_translator.cli import main


runner = CliRunner()


def test_extract_missing_repo():
    result = runner.invoke(main, ["extract"])
    assert result.exit_code != 0
    assert "repo" in result.output.lower() or "required" in result.output.lower()


def test_translate_manifest_missing_manifest():
    result = runner.invoke(main, ["translate-manifest"])
    assert result.exit_code != 0
    assert "manifest" in result.output.lower() or "required" in result.output.lower()


def test_translate_manifest_batch_size_zero():
    result = runner.invoke(main, ["translate-manifest", "--manifest", "x.jsonl", "--batch-size", "0"])
    assert result.exit_code != 0
    assert "range" in result.output.lower() or "invalid" in result.output.lower()


def test_translate_manifest_batch_size_101():
    result = runner.invoke(main, ["translate-manifest", "--manifest", "x.jsonl", "--batch-size", "101"])
    assert result.exit_code != 0
    assert "range" in result.output.lower() or "invalid" in result.output.lower()


def test_apply_missing_manifest():
    result = runner.invoke(main, ["apply"])
    assert result.exit_code != 0
    assert "manifest" in result.output.lower() or "required" in result.output.lower()


def test_audit_missing_dir():
    result = runner.invoke(main, ["audit"])
    assert result.exit_code != 0
    assert "dir" in result.output.lower() or "required" in result.output.lower()
