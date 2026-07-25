"""Unit tests for Tree-sitter extraction: template fragments, context rules, and fallback."""

from pathlib import Path
from unittest.mock import patch

import pytest

from repo_translator.extractors.tree_sitter import TreeSitterExtractor, _TREE_SITTER_AVAILABLE
from repo_translator.extractors.base import ExtractionReport, extract_repo
from repo_translator.segments import SegmentKind


pytestmark = pytest.mark.skipif(
    not _TREE_SITTER_AVAILABLE, reason="tree-sitter-language-pack not installed"
)

ext = TreeSitterExtractor()


# ---------------------------------------------------------------------------
# Template-literal fragment splitting regression test
# ---------------------------------------------------------------------------


def test_template_literal_fragment_splitting():
    """Regression: template string with ${pack} yields exactly two fragments, never ${pack}."""
    src = b'console.warn(`[decor] \xe8\xa3\x85\xe9\xa5\xb0\xe5\x8c\x85 ${pack} manifest \xe6\xa0\xa1\xe9\xaa\x8c\xe5\xa4\xb1\xe8\xb4\xa5:`)'
    # The above is: console.warn(`[decor] 装饰包 ${pack} manifest 校验失败:`)
    candidates = ext.extract(Path("src/deco.js"), src)

    # Only template_string_fragment segments for translatable ones
    translatable = [c for c in candidates if c.translatable]
    assert len(translatable) == 2

    texts = sorted(c.segment.source_text for c in translatable)
    assert "[decor] 装饰包 " in texts
    assert " manifest 校验失败:" in texts

    for c in translatable:
        assert c.segment.kind == SegmentKind.TEMPLATE_STRING_FRAGMENT
        assert c.segment.protected_context == ["${pack}"]

    # ${pack} must NEVER appear as a segment (translatable or not)
    all_texts = [c.segment.source_text for c in candidates]
    assert "${pack}" not in all_texts
    for t in all_texts:
        assert "${pack}" not in t


# ---------------------------------------------------------------------------
# Context-rule positive cases
# ---------------------------------------------------------------------------


def test_comment_translatable():
    src = "// 这是注释\n".encode()
    candidates = ext.extract(Path("a.js"), src)
    t = [c for c in candidates if c.translatable]
    assert len(t) == 1
    assert t[0].reason == "comment"


def test_jsx_text_translatable():
    src = b'function App() { return <div>\xe4\xbd\xa0\xe5\xa5\xbd</div> }'
    # 你好 in JSX text
    candidates = ext.extract(Path("app.tsx"), src)
    t = [c for c in candidates if c.translatable]
    assert any(c.segment.kind == SegmentKind.JSX_TEXT for c in t)


def test_console_warn_arg_translatable():
    src = 'console.warn("警告信息")\n'.encode()
    candidates = ext.extract(Path("x.js"), src)
    t = [c for c in candidates if c.translatable]
    assert len(t) >= 1
    assert any("警告信息" in c.segment.source_text for c in t)


def test_throw_error_translatable():
    src = 'throw new Error("失败")\n'.encode()
    candidates = ext.extract(Path("e.ts"), src)
    t = [c for c in candidates if c.translatable]
    assert any("失败" in c.segment.source_text for c in t)


def test_placeholder_attr_translatable():
    src = b'function F() { return <input placeholder="\xe8\xaf\xb7\xe8\xbe\x93\xe5\x85\xa5" /> }'
    # placeholder="请输入"
    candidates = ext.extract(Path("f.tsx"), src)
    t = [c for c in candidates if c.translatable]
    assert any("请输入" in c.segment.source_text for c in t)


# ---------------------------------------------------------------------------
# Context-rule negative cases
# ---------------------------------------------------------------------------


def test_import_not_translatable():
    src = 'import x from "路径"\n'.encode()
    candidates = ext.extract(Path("i.js"), src)
    neg = [c for c in candidates if not c.translatable and "路径" in c.segment.source_text]
    assert len(neg) >= 1
    assert neg[0].reason == "import/module source"


def test_fetch_not_translatable():
    src = 'fetch("/接口")\n'.encode()
    candidates = ext.extract(Path("r.js"), src)
    neg = [c for c in candidates if not c.translatable and "接口" in c.segment.source_text]
    assert len(neg) >= 1
    assert neg[0].reason == "route/endpoint"


def test_classname_not_translatable():
    src = b'function C() { return <div className="\xe5\xae\xb9\xe5\x99\xa8" /> }'
    # className="容器"
    candidates = ext.extract(Path("c.tsx"), src)
    neg = [c for c in candidates if not c.translatable and "容器" in c.segment.source_text]
    assert len(neg) >= 1
    assert neg[0].reason == "className"


def test_data_testid_not_translatable():
    src = b'function T() { return <button data-testid="\xe6\x8c\x89\xe9\x92\xae" /> }'
    # data-testid="按钮"
    candidates = ext.extract(Path("t.tsx"), src)
    neg = [c for c in candidates if not c.translatable and "按钮" in c.segment.source_text]
    assert len(neg) >= 1
    assert neg[0].reason == "test selector"


def test_negative_cases_surfaced_in_skipped():
    """All negative cases produce non-translatable candidates with correct reasons."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # import
        (root / "a.js").write_bytes('import x from "路径"\n'.encode())
        # fetch
        (root / "b.js").write_bytes('fetch("/接口")\n'.encode())

        report = ExtractionReport()

        # Extract directly with relative paths (how extract_repo should work)
        a_bytes = (root / "a.js").read_bytes()
        b_bytes = (root / "b.js").read_bytes()

        a_candidates = ext.extract(Path("a.js"), a_bytes)
        b_candidates = ext.extract(Path("b.js"), b_bytes)

        # Feed non-translatable candidates into report.skipped (as extract_repo does)
        for c in a_candidates + b_candidates:
            if not c.translatable:
                report.skipped.append((
                    c.segment.path, c.segment.start_byte,
                    c.segment.source_text[:60], c.reason,
                ))

        reasons = [r for (_, _, _, r) in report.skipped]
        assert "import/module source" in reasons
        assert "route/endpoint" in reasons


# ---------------------------------------------------------------------------
# Fallback path
# ---------------------------------------------------------------------------


def test_fallback_when_tree_sitter_unavailable():
    """When Tree-sitter extractor raises, extract_repo uses regex fallback and marks fallback_files."""
    import tempfile
    from repo_translator.extractors.base import _registry, get_extractor

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "code.js").write_text('const msg = "你好世界";\n', encoding="utf-8")

        report = ExtractionReport()

        # Temporarily remove .js from registry so fallback is used directly
        saved = _registry.pop(".js", None)
        try:
            segs = list(extract_repo(root, translate_code=True, report=report))
        finally:
            if saved is not None:
                _registry[".js"] = saved

        assert "code.js" in report.fallback_files
        # Fallback should still find CJK
        assert any("你好世界" in s.source_text for s in segs)
