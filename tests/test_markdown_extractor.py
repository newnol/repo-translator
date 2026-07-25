"""Unit tests for Markdown extraction (task 6.4)."""

from pathlib import Path

from repo_translator.extractors.markdown import MarkdownExtractor
from repo_translator.segments import SegmentKind

ext = MarkdownExtractor()
P = Path("test.md")


def _segments(md_text: str):
    """Helper: extract candidates from a UTF-8 markdown string, return translatable segments."""
    file_bytes = md_text.encode("utf-8")
    candidates = ext.extract(P, file_bytes)
    return [c.segment for c in candidates if c.translatable], file_bytes


class TestPositiveCases:
    def test_heading_cjk(self):
        segs, fb = _segments("# 标题文本\n")
        assert any(s.kind == SegmentKind.MARKDOWN_HEADING for s in segs)
        heading = next(s for s in segs if s.kind == SegmentKind.MARKDOWN_HEADING)
        assert "标题文本" in heading.source_text

    def test_paragraph_cjk(self):
        segs, fb = _segments("这是一个段落\n")
        assert any(s.kind == SegmentKind.MARKDOWN_PARAGRAPH for s in segs)

    def test_table_row_cjk(self):
        md = "| 名称 | 描述 |\n| --- | --- |\n| 值一 | 值二 |\n"
        segs, fb = _segments(md)
        table_segs = [s for s in segs if s.kind == SegmentKind.MARKDOWN_TABLE_CELL]
        assert len(table_segs) >= 2

    def test_link_label_cjk(self):
        segs, fb = _segments("[链接文本](https://example.com)\n")
        link_segs = [s for s in segs if s.kind == SegmentKind.MARKDOWN_LINK_LABEL]
        assert len(link_segs) == 1
        assert link_segs[0].source_text == "链接文本"


class TestNegativeCases:
    def test_fenced_code_not_extracted(self):
        md = "```\n这不应该被提取\n```\n"
        segs, fb = _segments(md)
        assert segs == []

    def test_inline_code_not_extracted(self):
        # Line is purely inline code — no translatable prose remains
        md = "`代码内容`\n"
        segs, fb = _segments(md)
        # The inline code itself should not produce a segment
        for s in segs:
            assert "代码内容" not in s.source_text

    def test_url_line_not_extracted(self):
        md = "https://example.com/路径\n"
        segs, fb = _segments(md)
        assert segs == []


class TestByteRoundTrip:
    def test_byte_offsets_match_source_text(self):
        md = "# 大标题\n\n普通段落文本\n"
        segs, fb = _segments(md)
        for s in segs:
            assert fb[s.start_byte : s.end_byte].decode("utf-8") == s.source_text
