"""Tests for the Python extractor (Req 7.1–7.6)."""

from pathlib import Path

from repo_translator.extractors.python import PythonExtractor
from repo_translator.segments import SegmentKind


def _extract(source: str):
    """Helper: extract candidates from a Python source string."""
    file_bytes = source.encode("utf-8")
    ext = PythonExtractor()
    return ext.extract(Path("test.py"), file_bytes), file_bytes


def test_docstrings_extracted():
    """Req 7.1: emit one Segment for each docstring."""
    src = '"""Module docstring."""\n\ndef foo():\n    """函数文档"""\n    pass\n'
    candidates, fb = _extract(src)
    docstrings = [c for c in candidates if c.segment.kind == SegmentKind.DOCSTRING]
    assert len(docstrings) == 2
    assert docstrings[0].segment.source_text == "Module docstring."
    assert docstrings[1].segment.source_text == "函数文档"
    # Round-trip check
    for c in docstrings:
        actual = fb[c.segment.start_byte : c.segment.end_byte].decode("utf-8")
        assert actual == c.segment.source_text


def test_comments_extracted():
    """Req 7.5: emit one Segment for each comment token."""
    src = "# 这是注释\nx = 1  # inline\n"
    candidates, fb = _extract(src)
    comments = [c for c in candidates if c.segment.kind == SegmentKind.COMMENT]
    assert len(comments) == 2
    assert comments[0].segment.source_text == "# 这是注释"
    assert comments[1].segment.source_text == "# inline"
    for c in comments:
        actual = fb[c.segment.start_byte : c.segment.end_byte].decode("utf-8")
        assert actual == c.segment.source_text


def test_syntax_error_returns_empty():
    """Req 7.6: on SyntaxError emit nothing."""
    src = "def foo(\n"  # invalid
    candidates, _ = _extract(src)
    assert candidates == []


def test_class_docstring():
    """Req 7.1: class docstrings are extracted."""
    src = "class Foo:\n    '''类文档'''\n    pass\n"
    candidates, fb = _extract(src)
    docstrings = [c for c in candidates if c.segment.kind == SegmentKind.DOCSTRING]
    assert len(docstrings) == 1
    assert docstrings[0].segment.source_text == "类文档"
    actual = fb[docstrings[0].segment.start_byte : docstrings[0].segment.end_byte].decode("utf-8")
    assert actual == "类文档"


def test_async_function_docstring():
    """Req 7.1: async function docstrings are extracted."""
    src = "async def bar():\n    \"\"\"异步函数\"\"\"\n    pass\n"
    candidates, fb = _extract(src)
    docstrings = [c for c in candidates if c.segment.kind == SegmentKind.DOCSTRING]
    assert len(docstrings) == 1
    assert docstrings[0].segment.source_text == "异步函数"


def test_byte_offset_multibyte():
    """Req 7.4: byte offsets correct for multibyte characters."""
    src = "# 你好世界\n"
    candidates, fb = _extract(src)
    assert len(candidates) == 1
    c = candidates[0]
    # '# 你好世界' is 2 + 1 + 4*3 = 15 bytes
    assert c.segment.start_byte == 0
    assert c.segment.end_byte == len("# 你好世界".encode("utf-8"))
    actual = fb[c.segment.start_byte : c.segment.end_byte].decode("utf-8")
    assert actual == "# 你好世界"


def test_empty_comment_skipped():
    """Comments that are just '#' should be skipped."""
    src = "#\nx = 1\n"
    candidates, _ = _extract(src)
    assert candidates == []


def test_identifiers_not_extracted():
    """Req 7.3: identifiers are never emitted as segments."""
    src = '"""doc"""\n\ndef 函数名():\n    """doc"""\n    变量 = 1\n    return 变量\n'
    candidates, _ = _extract(src)
    texts = [c.segment.source_text for c in candidates]
    assert "函数名" not in texts
    assert "变量" not in texts


def test_imports_not_extracted():
    """Req 7.3: import paths are never emitted as segments."""
    src = 'import os\nfrom pathlib import Path\nimport 中文模块\n# 注释\n'
    candidates, _ = _extract(src)
    texts = [c.segment.source_text for c in candidates]
    assert "os" not in texts
    assert "pathlib" not in texts
    assert "Path" not in texts
    assert "中文模块" not in texts
    # Only the comment should be extracted
    assert len(candidates) == 1
    assert candidates[0].segment.kind == SegmentKind.COMMENT
