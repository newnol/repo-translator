"""Unit tests for the AUDIT stage."""

import os
import sys

import pytest

from repo_translator.audit import audit_repo


def test_multiple_cjk_lines_multiple_findings(tmp_path):
    """Multiple lines with CJK → one finding per line with correct 1-based line numbers."""
    (tmp_path / "a.py").write_text("ok\n这是第一行\nfine\n第二行在这里\n", encoding="utf-8")
    report = audit_repo(tmp_path)
    assert report.total_findings == 2
    assert report.findings[0].line == 2
    assert report.findings[1].line == 4
    assert report.files_with_residual == 1


def test_snippet_capped_at_200(tmp_path):
    """Snippet is capped at 200 characters even when the line is longer."""
    long_line = "中" * 300
    (tmp_path / "b.md").write_text(long_line + "\n", encoding="utf-8")
    report = audit_repo(tmp_path)
    assert len(report.findings[0].snippet) == 200


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 000 not enforced on Windows")
def test_unreadable_file_recorded(tmp_path):
    """Unreadable file → recorded in unaudited_files."""
    f = tmp_path / "c.py"
    f.write_text("hello\n", encoding="utf-8")
    f.chmod(0o000)
    try:
        report = audit_repo(tmp_path)
        assert "c.py" in report.unaudited_files
    finally:
        f.chmod(0o644)


def test_clean_repo_zero_findings(tmp_path):
    """Clean repo (no CJK) → zero findings."""
    (tmp_path / "d.py").write_text("print('hello')\n", encoding="utf-8")
    report = audit_repo(tmp_path)
    assert report.total_findings == 0
    assert report.files_with_residual == 0
    assert report.findings == []


def test_mixed_files_only_cjk_reported(tmp_path):
    """Only files containing CJK count toward files_with_residual."""
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "dirty.md").write_text("这里有中文\n", encoding="utf-8")
    (tmp_path / "also_clean.py").write_text("# nothing here\n", encoding="utf-8")
    report = audit_repo(tmp_path)
    assert report.files_with_residual == 1
    assert report.total_findings == 1
    assert report.findings[0].path == "dirty.md"
