"""Tests for repo-translator."""

import pytest

from repo_translator.detector import (
    count_cjk_chars,
    detect_language,
    extract_translatable_text,
    has_cjk,
)
from repo_translator.file_filter import TRANSLATABLE_EXTENSIONS, should_translate


class TestDetector:
    """Test language detection."""

    def test_has_cjk_chinese(self):
        assert has_cjk("这是中文文本") is True

    def test_has_cjk_english(self):
        assert has_cjk("This is English text") is False

    def test_has_cjk_mixed(self):
        assert has_cjk("Hello 你好 World") is True

    def test_count_cjk_chars(self):
        assert count_cjk_chars("这是中文") == 4
        assert count_cjk_chars("Hello World") == 0
        assert count_cjk_chars("Hello 你好") == 2

    def test_detect_language_chinese(self):
        text = "这是一段中文文本，用于测试语言检测功能。我们希望它能够正确识别中文。"
        lang = detect_language(text)
        assert lang in ("zh-cn", "zh", "zh-tw")

    def test_detect_language_english(self):
        text = "This is a sample English text for testing language detection functionality."
        lang = detect_language(text)
        assert lang == "en"

    def test_detect_language_empty(self):
        assert detect_language("") is None
        assert detect_language("   ") is None
        assert detect_language(None) is None

    def test_detect_language_short(self):
        assert detect_language("Hi") is None  # too short


class TestFileFilter:
    """Test file filtering."""

    def test_translatable_extensions(self):
        assert ".md" in TRANSLATABLE_EXTENSIONS
        assert ".py" in TRANSLATABLE_EXTENSIONS
        assert ".rs" in TRANSLATABLE_EXTENSIONS
        assert ".png" not in TRANSLATABLE_EXTENSIONS

    def test_should_translate_markdown(self, tmp_path):
        md_file = tmp_path / "README.md"
        md_file.write_text("# Hello")
        assert should_translate(md_file, tmp_path) is True

    def test_should_skip_binary(self, tmp_path):
        png_file = tmp_path / "image.png"
        png_file.write_bytes(b"\x89PNG")
        assert should_translate(png_file, tmp_path) is False

    def test_should_skip_node_modules(self, tmp_path):
        nm_file = tmp_path / "node_modules" / "package" / "index.js"
        nm_file.parent.mkdir(parents=True)
        nm_file.write_text("module.exports = {}")
        assert should_translate(nm_file, tmp_path) is False

    def test_should_skip_lock_files(self, tmp_path):
        lock_file = tmp_path / "package-lock.json"
        lock_file.write_text("{}")
        assert should_translate(lock_file, tmp_path) is False

    def test_docs_only_and_path_patterns(self, tmp_path):
        from repo_translator.file_filter import get_translatable_files

        docs = tmp_path / "docs"
        docs.mkdir()
        readme = docs / "README.md"
        readme.write_text("# 中文")
        draft = docs / "draft.md"
        draft.write_text("# 中文")
        source = tmp_path / "app.py"
        source.write_text('label = "中文"')

        files = get_translatable_files(
            tmp_path,
            include_patterns=["docs/**"],
            exclude_patterns=["**/draft.md"],
            translate_code=False,
        )

        assert files == [readme]


class TestExtractText:
    """Test text extraction from source code."""

    def test_extract_markdown(self):
        content = "# Hello World\n\nThis is a paragraph."
        result = extract_translatable_text(content, ".md")
        assert "Hello World" in result

    def test_extract_python_comments(self):
        content = "# This is a comment\ndef hello():\n    pass"
        result = extract_translatable_text(content, ".py")
        assert "This is a comment" in result

    def test_extract_html(self):
        content = "<h1>Hello</h1><p>World</p>"
        result = extract_translatable_text(content, ".html")
        assert "Hello" in result
        assert "World" in result


class TestTranslators:
    """Test translator engines."""

    def test_google_translator_init(self):
        from repo_translator.translators.google import GoogleTranslator

        t = GoogleTranslator("zh", "en")
        assert t.name == "Google Translate"

    def test_translator_factory(self):
        from repo_translator.translators import get_translator

        t = get_translator("google", "zh", "en")
        assert t.name == "Google Translate"

    def test_google_alt_accepts_zh_alias(self):
        from repo_translator.translators import get_translator

        t = get_translator("google-alt", "zh", "en")
        assert t.name == "Google Translate (deep-translator)"

    def test_unknown_engine(self):
        from repo_translator.translators import get_translator

        with pytest.raises(ValueError, match="Unknown engine"):
            get_translator("unknown", "zh", "en")

    def test_libre_translator_init(self):
        from repo_translator.translators import get_translator

        t = get_translator("libre", "zh", "en")
        assert t.name == "LibreTranslate"

    def test_mymemory_translator_init(self):
        from repo_translator.translators import get_translator

        t = get_translator("mymemory", "zh", "en", api_key="test-key")
        assert t.name == "MyMemory"

    def test_multi_translator(self):
        from repo_translator.translators import get_translator

        t = get_translator("google,google-alt", "zh", "en")
        assert "+" in t.name
        assert "Google Translate" in t.name
        assert len(t.translators) == 2

    def test_multi_translator_distributes_native_batches_and_keeps_order(self):
        from repo_translator.translators.base import BaseTranslator
        from repo_translator.translators.multi import MultiTranslator

        class BatchTranslator(BaseTranslator):
            def __init__(self, label):
                super().__init__("zh", "en")
                self.label = label
                self.batches = []

            @property
            def name(self):
                return self.label

            def translate_text(self, text):
                return f"{self.label}:{text}"

            def translate_batch(self, texts):
                self.batches.append(texts)
                return [self.translate_text(text) for text in texts]

        first = BatchTranslator("first")
        second = BatchTranslator("second")
        translator = MultiTranslator([first, second])

        result = translator.translate_batch(["0", "1", "2", "3", "4"])

        assert result == ["first:0", "second:1", "first:2", "second:3", "first:4"]
        assert first.batches == [["0", "2", "4"]]
        assert second.batches == [["1", "3"]]

    def test_libre_uses_array_batch_api(self, monkeypatch):
        import requests

        from repo_translator.translators.libre import LibreTranslate

        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"translatedText": ["Hello", "World"]}

        def fake_post(url, json, timeout):
            calls.append((url, json, timeout))
            return Response()

        monkeypatch.setattr(requests, "post", fake_post)
        translator = LibreTranslate("zh", "en", base_url="http://libre.test")

        assert translator.translate_batch(["你好", "世界"]) == ["Hello", "World"]
        assert calls[0][0] == "http://libre.test/translate"
        assert calls[0][1]["q"] == ["你好", "世界"]

    def test_chunk_text(self):
        from repo_translator.translators.base import BaseTranslator

        class DummyTranslator(BaseTranslator):
            def translate_text(self, text):
                return text

            @property
            def name(self):
                return "dummy"

        t = DummyTranslator("zh", "en")
        chunks = t._chunk_text("a\n" * 1000, max_chars=100)
        assert len(chunks) > 1
        assert all(len(c) <= 120 for c in chunks)  # some margin


class TestCLI:
    """Test CLI commands."""

    def test_cli_engines(self, runner):
        from repo_translator.cli import main

        result = runner.invoke(main, ["engines"])
        assert result.exit_code == 0
        assert "google" in result.output

    def test_cli_detect_local_repo_reports_cjk_percentage(self, runner, tmp_path):
        from repo_translator.cli import main

        readme = tmp_path / "README.md"
        readme.write_text("# 标题\n\n这是中文文本，用于测试语言检测。", encoding="utf-8")

        result = runner.invoke(main, ["detect", "--repo", str(tmp_path), "--sample", "1"])

        assert result.exit_code == 0
        assert "CJK %" in result.output
        assert "README.md" in result.output

    def test_cli_review_accepts_reviewer_alias(self, runner, tmp_path):
        from repo_translator.cli import main

        readme = tmp_path / "README.md"
        readme.write_text("# Hello\n\nTranslated content.", encoding="utf-8")

        result = runner.invoke(
            main, ["review", "--dir", str(tmp_path), "--reviewer", "openai", "--sample-rate", "0"]
        )
        assert result.exit_code == 0
        assert "Review Report" in result.output

    def test_translate_preserves_broken_symlinks_when_copying(self, tmp_path):
        from repo_translator.core import RepoTranslator

        source = tmp_path / "source"
        source.mkdir()
        (source / "README.md").write_text("# 标题\n\n这是中文文本。", encoding="utf-8")
        (source / "missing.pdf").symlink_to(tmp_path / "does-not-exist.pdf")

        output = tmp_path / "translated"
        output.mkdir()
        (output / "stale.txt").write_text("old", encoding="utf-8")

        translator = RepoTranslator(translator_engine="google-alt", dry_run=True)
        result = translator.run(repo_dir=str(source), output_dir=str(output))

        assert result["success"] is True
        assert not (output / "stale.txt").exists()
        copied_link = output / "missing.pdf"
        assert copied_link.is_symlink()
        assert not copied_link.exists()

    def test_cli_accepts_comments_only_and_batch_size(self, runner, tmp_path):
        from repo_translator.cli import main

        source = tmp_path / "app.py"
        source.write_text('label = "运行时文本"  # 中文注释', encoding="utf-8")

        result = runner.invoke(
            main,
            [
                "translate",
                "--repo",
                str(tmp_path),
                "--translator",
                "libre",
                "--translate-code",
                "--code-scope",
                "comments",
                "--batch-size",
                "25",
                "--dry-run",
            ],
        )

        assert result.exit_code == 0
        assert "Translation complete" in result.output


class TestProtectedTranslation:
    """Regression tests for preserving repository structure while translating."""

    class MangleProtectedTranslator:
        name = "mangle-protected"

        def translate_text(self, text):
            return (
                text.replace("使用说明", "Usage")
                .replace("这是中文文本", "This is Chinese text")
                .replace("你好", "Hello")
                .replace("$x_t", "$release")
                .replace("${expected}", "${value}")
                .replace("https://example.com/docs", "https://example.com/translated-docs")
                .replace("./docs/guide.md", "./translated/guide.md")
                .replace("retain-pdf --help", "retain-pdf translated help")
                .replace("## 1. Upload PDF", "```## 1. Upload PDF`")
                .replace("。！？；", ".!?;")
            )

    def _translator(self):
        from repo_translator.core import RepoTranslator

        translator = RepoTranslator.__new__(RepoTranslator)
        translator.translator = self.MangleProtectedTranslator()
        return translator

    def test_markdown_translation_preserves_code_inline_urls_and_placeholders(self):
        translator = self._translator()
        content = """# 使用说明

Run `retain-pdf --help` and keep `${expected}`.
Visit https://example.com/docs.
Read [文档](./docs/guide.md).

```bash
retain-pdf --help
```

这是中文文本。
"""

        translated = translator._translate_markdown(content)

        assert "# Usage" in translated
        assert "This is Chinese text" in translated
        assert "`retain-pdf --help`" in translated
        assert "${expected}" in translated
        assert "https://example.com/docs" in translated
        assert "(./docs/guide.md)" in translated
        assert "retain-pdf translated help" not in translated
        assert "https://example.com/translated-docs" not in translated
        assert "./translated/guide.md" not in translated

    def test_json_translation_preserves_syntax_and_keys(self):
        import json

        translator = self._translator()
        content = '{"message": "你好", "nested": {"keep_key": "你好 ${expected}"}}'

        translated = translator._translate_json_content(content)
        data = json.loads(translated)

        assert sorted(data) == ["message", "nested"]
        assert data["message"] == "Hello"
        assert data["nested"]["keep_key"] == "Hello ${expected}"

    def test_source_line_translation_preserves_placeholders_math_and_urls(self):
        translator = self._translator()
        lines = [
            'assert render("$x_t") == f"${expected}"  # 这是中文文本 https://example.com/docs',
        ]

        translated = translator._translate_source_lines(lines)

        assert "$x_t" in translated[0]
        assert "${expected}" in translated[0]
        assert "https://example.com/docs" in translated[0]
        assert "$release" not in translated[0]
        assert "${value}" not in translated[0]
        assert "translated-docs" not in translated[0]
        assert "This is Chinese text" in translated[0]

    def test_markdown_translation_does_not_mutate_english_lines_in_cjk_file(self):
        translator = self._translator()
        content = """# 使用说明

## 1. Upload PDF

这是中文文本。
"""

        translated = translator._translate_markdown(content)

        assert "# Usage" in translated
        assert "## 1. Upload PDF" in translated
        assert "```## 1. Upload PDF`" not in translated
        assert "This is Chinese text" in translated

    def test_source_line_translation_skips_cjk_punctuation_only_regex(self):
        translator = self._translator()
        line = r'SOURCE_TERMINAL_RE = re.compile(r"[.!?。！？；;:：)\]）】”’\"\']\s*$")'

        translated = translator._translate_source_lines([line])

        assert translated == [line]

    def test_source_translation_only_changes_string_and_comment_bodies(self):
        translator = self._translator()
        translator.translator.translate_text = lambda text: (
            text.replace("上传 PDF", "Upload PDF").replace("按钮说明", "Button help")
        )
        line = 'const label = "上传 PDF"; // 按钮说明'

        translated = translator._translate_source_lines([line], ".ts")

        assert translated == ['const label = "Upload PDF"; // Button help']

    def test_source_comments_are_batched_and_strings_can_be_excluded(self):
        translator = self._translator()
        translator.code_scope = "comments"
        translator.batch_size = 40
        batches = []

        def translate_batch(texts):
            batches.append(texts)
            return [
                text.replace("第一条说明", "First note").replace("第二条说明", "Second note")
                for text in texts
            ]

        translator.translator.translate_batch = translate_batch
        lines = [
            'const label = "上传 PDF"; // 第一条说明',
            "// 第二条说明",
        ]

        translated = translator._translate_source_lines(lines, ".ts")

        assert translated == ['const label = "上传 PDF"; // First note', "// Second note"]
        assert len(batches) == 1
        assert batches[0] == ["第一条说明", "第二条说明"]

    def test_batched_string_translation_escapes_new_quotes(self):
        translator = self._translator()
        translator.code_scope = "comments-and-strings"
        translator.batch_size = 40
        translator.translator.translate_batch = lambda texts: [
            'Click "Upload"' if text == "上传" else text for text in texts
        ]

        translated = translator._translate_source_lines(['label = "上传"'], ".py")

        assert translated == ['label = "Click \\"Upload\\""']

    def test_comments_only_translates_multiline_block_comment(self):
        translator = self._translator()
        translator.code_scope = "comments"
        translator.translator.translate_batch = lambda texts: [
            text.replace("开始说明", "Start note")
            .replace("中间说明", "Middle note")
            .replace("结束说明", "End note")
            for text in texts
        ]

        translated = translator._translate_source_lines(
            [
                'const value = "中文不应翻译"; /* 开始说明',
                " * 中间说明",
                " 结束说明 */",
            ],
            ".ts",
        )

        assert translated == [
            'const value = "中文不应翻译"; /* Start note',
            " * Middle note",
            " End note */",
        ]

    def test_comments_only_translates_python_docstrings_not_runtime_strings(self):
        translator = self._translator()
        translator.code_scope = "comments"
        translator.translator.translate_batch = lambda texts: [
            text.replace("模块说明", "Module docs")
            .replace("函数说明", "Function docs")
            .replace("第二行", "Second line")
            for text in texts
        ]

        translated = translator._translate_source_lines(
            [
                '"""模块说明"""',
                "",
                "def run():",
                '    """函数说明',
                "    第二行",
                '    """',
                '    return "运行时中文"',
            ],
            ".py",
        )

        assert translated == [
            '"""Module docs"""',
            "",
            "def run():",
            '    """Function docs',
            "    Second line",
            '    """',
            '    return "运行时中文"',
        ]

    def test_source_batches_collapse_provider_newlines_and_escape_docstring_delimiter(self):
        translator = self._translator()
        translator.code_scope = "comments"
        translator.translator.translate_batch = lambda texts: [
            'First line\nsecond line with """ quotes' for _ in texts
        ]

        translated = translator._translate_source_lines(
            ['"""模块说明"""', "# 行注释"],
            ".py",
        )

        assert translated == [
            '"""First line second line with \\""" quotes"""',
            '# First line second line with """ quotes',
        ]
        compile("\n".join(translated), "translated.py", "exec")

    def test_markdown_keeps_structural_prefix_and_final_newline_state(self):
        translator = self._translator()
        translator.translator.translate_text = lambda text: text.replace("使用说明", "Usage")

        assert translator._translate_markdown("# 使用说明") == "# Usage"
        assert translator._translate_markdown("- 使用说明\n") == "- Usage\n"


class TestPipelineSafety:
    def test_dry_run_does_not_write_resume_state(self, tmp_path):
        from repo_translator.core import STATE_FILE, RepoTranslator

        (tmp_path / "README.md").write_text("# 中文内容", encoding="utf-8")
        translator = RepoTranslator(translator_engine="google-alt", dry_run=True)

        translator.translate_in_place(tmp_path)

        assert not (tmp_path / STATE_FILE).exists()

    def test_parallel_failures_are_not_saved_as_success(self, tmp_path, monkeypatch):
        from repo_translator.core import STATE_FILE, RepoTranslator

        for name in ("a.md", "b.md"):
            (tmp_path / name).write_text("# 中文内容", encoding="utf-8")

        translator = RepoTranslator(translator_engine="google-alt", max_workers=2)
        monkeypatch.setattr(
            translator,
            "_translate_one_file",
            lambda filepath, root: {
                "ok": False,
                "rel": str(filepath.relative_to(root)),
                "error": "boom",
            },
        )

        stats = translator.translate_in_place(tmp_path)
        state = __import__("json").loads((tmp_path / STATE_FILE).read_text(encoding="utf-8"))

        assert stats.translated_files == 0
        assert stats.failed_files == 2
        assert state["translated"] == []

    def test_remote_run_keeps_source_snapshot_separate(self, tmp_path, monkeypatch):
        from repo_translator.core import RepoTranslator

        translator = RepoTranslator(
            translator_engine="google-alt",
            dry_run=True,
            verify=True,
        )

        def fake_clone(_url, output_dir):
            output_dir = __import__("pathlib").Path(output_dir)
            output_dir.mkdir(parents=True)
            (output_dir / "README.md").write_text("# 中文内容", encoding="utf-8")
            return output_dir

        monkeypatch.setattr(translator, "clone", fake_clone)
        output = tmp_path / "translated"
        result = translator.run(repo_url="https://example.test/repo.git", output_dir=str(output))

        assert result["success"] is True
        assert result["verification"].source_dir != result["verification"].target_dir


class TestReviewers:
    """Test AI reviewer lightweight checks."""

    def test_check_file_detects_untranslated_cjk(self, tmp_path):
        from repo_translator.reviewers import AIReviewer

        translated = tmp_path / "README.md"
        translated.write_text("# Title\n\n这里还有未翻译的中文。", encoding="utf-8")

        reviewer = AIReviewer(source_lang="zh", sample_rate=0, api_key="")
        issues = reviewer._check_file(
            translated,
            translated.read_text(encoding="utf-8"),
            root=tmp_path,
        )

        assert any(issue.issue_type == "untranslated" for issue in issues)


@pytest.fixture
def runner():
    from click.testing import CliRunner

    return CliRunner()
