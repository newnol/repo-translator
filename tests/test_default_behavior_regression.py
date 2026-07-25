"""Regression: default RepoTranslator (no manifest flags) behaves identically to pre-feature."""

import os

from repo_translator.core import RepoTranslator


class TestDefaultBehaviorRegression:
    """Validates: Requirements 19.1"""

    def test_default_instance_has_no_manifest_attributes(self):
        t = RepoTranslator(translator_engine="google-alt")
        assert t.export_manifest_path is None
        assert t.apply_manifest_path is None
        assert t.translation_memory_path is None
        assert t.fail_on_source_mismatch is True
        assert t.audit_untranslated is False

    def test_default_run_produces_no_manifest_or_audit(self, tmp_path):
        """Translate a small CJK repo with defaults — no manifest file, no audit output."""
        source = tmp_path / "source"
        source.mkdir()
        (source / "README.md").write_text("# 标题\n\n这是中文。\n", encoding="utf-8")

        output = tmp_path / "output"

        # Use a trivial mock translator to avoid network calls
        t = RepoTranslator(translator_engine="google-alt", dry_run=True)
        result = t.run(repo_dir=str(source), output_dir=str(output))

        assert result["success"] is True
        # No manifest file created anywhere in output
        for root, _dirs, files in os.walk(output):
            for f in files:
                assert not f.endswith(".jsonl"), f"Unexpected manifest file: {f}"
        # No audit key in result
        assert "audit" not in result or result.get("audit") is None
