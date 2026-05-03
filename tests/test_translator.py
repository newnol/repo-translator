"""Tests for repo-translator."""

import pytest
from pathlib import Path

from repo_translator.detector import detect_language, has_cjk, count_cjk_chars, extract_translatable_text
from repo_translator.file_filter import should_translate, get_translatable_files, TRANSLATABLE_EXTENSIONS


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
        assert lang in ('zh-cn', 'zh', 'zh-tw')

    def test_detect_language_english(self):
        text = "This is a sample English text for testing language detection functionality."
        lang = detect_language(text)
        assert lang == 'en'

    def test_detect_language_empty(self):
        assert detect_language("") is None
        assert detect_language("   ") is None
        assert detect_language(None) is None

    def test_detect_language_short(self):
        assert detect_language("Hi") is None  # too short


class TestFileFilter:
    """Test file filtering."""

    def test_translatable_extensions(self):
        assert '.md' in TRANSLATABLE_EXTENSIONS
        assert '.py' in TRANSLATABLE_EXTENSIONS
        assert '.rs' in TRANSLATABLE_EXTENSIONS
        assert '.png' not in TRANSLATABLE_EXTENSIONS

    def test_should_translate_markdown(self, tmp_path):
        md_file = tmp_path / "README.md"
        md_file.write_text("# Hello")
        assert should_translate(md_file, tmp_path) is True

    def test_should_skip_binary(self, tmp_path):
        png_file = tmp_path / "image.png"
        png_file.write_bytes(b'\x89PNG')
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


class TestExtractText:
    """Test text extraction from source code."""

    def test_extract_markdown(self):
        content = "# Hello World\n\nThis is a paragraph."
        result = extract_translatable_text(content, '.md')
        assert "Hello World" in result

    def test_extract_python_comments(self):
        content = '# This is a comment\ndef hello():\n    pass'
        result = extract_translatable_text(content, '.py')
        assert "This is a comment" in result

    def test_extract_html(self):
        content = '<h1>Hello</h1><p>World</p>'
        result = extract_translatable_text(content, '.html')
        assert "Hello" in result
        assert "World" in result


class TestTranslators:
    """Test translator engines."""

    def test_google_translator_init(self):
        from repo_translator.translators.google import GoogleTranslator
        t = GoogleTranslator('zh', 'en')
        assert t.name == 'Google Translate'

    def test_translator_factory(self):
        from repo_translator.translators import get_translator
        t = get_translator('google', 'zh', 'en')
        assert t.name == 'Google Translate'

    def test_unknown_engine(self):
        from repo_translator.translators import get_translator
        with pytest.raises(ValueError, match="Unknown engine"):
            get_translator('unknown', 'zh', 'en')

    def test_chunk_text(self):
        from repo_translator.translators.base import BaseTranslator

        class DummyTranslator(BaseTranslator):
            def translate_text(self, text): return text
            @property
            def name(self): return "dummy"

        t = DummyTranslator('zh', 'en')
        chunks = t._chunk_text("a\n" * 1000, max_chars=100)
        assert len(chunks) > 1
        assert all(len(c) <= 120 for c in chunks)  # some margin


class TestCLI:
    """Test CLI commands."""

    def test_cli_engines(self, runner):
        from repo_translator.cli import main
        result = runner.invoke(main, ['engines'])
        assert result.exit_code == 0
        assert 'google' in result.output


@pytest.fixture
def runner():
    from click.testing import CliRunner
    return CliRunner()
