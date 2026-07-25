"""Smoke tests for the markup extractor."""

from pathlib import Path

from repo_translator.extractors.markup import MarkupExtractor
from repo_translator.segments import SegmentKind


def test_basic_html_text_and_attrs():
    ext = MarkupExtractor()
    html = b'<div title="Click me"><p>Hello world</p><script>var x=1;</script></div>'
    candidates = ext.extract(Path("test.html"), html)
    texts = [(c.segment.kind, c.segment.source_text) for c in candidates]
    assert (SegmentKind.HTML_TEXT, "Hello world") in texts
    assert (SegmentKind.UI_ATTRIBUTE, "Click me") in texts
    # script content excluded
    assert all(c.segment.source_text != "var x=1;" for c in candidates)


def test_script_and_style_excluded():
    ext = MarkupExtractor()
    html = b"<div><script>alert('hi')</script><style>body{}</style><p>Keep</p></div>"
    candidates = ext.extract(Path("test.html"), html)
    texts = [c.segment.source_text for c in candidates]
    assert "Keep" in texts
    assert "alert('hi')" not in texts
    assert "body{}" not in texts


def test_svg_id_ref_excluded():
    ext = MarkupExtractor()
    svg = b'<svg><text title="#icon">Visible</text></svg>'
    candidates = ext.extract(Path("test.svg"), svg)
    texts = [(c.segment.kind, c.segment.source_text) for c in candidates]
    assert (SegmentKind.HTML_TEXT, "Visible") in texts
    # title="#icon" should be excluded (starts with #)
    assert (SegmentKind.UI_ATTRIBUTE, "#icon") not in texts


def test_svg_url_hash_excluded():
    ext = MarkupExtractor()
    svg = b'<svg><text alt="url(#gradient)">Hi</text></svg>'
    candidates = ext.extract(Path("test.svg"), svg)
    texts = [c.segment.source_text for c in candidates]
    assert "Hi" in texts
    assert "url(#gradient)" not in texts


def test_tail_text():
    ext = MarkupExtractor()
    html = b"<p><b>Bold</b> and normal</p>"
    candidates = ext.extract(Path("test.html"), html)
    texts = [c.segment.source_text for c in candidates]
    assert "Bold" in texts
    assert " and normal" in texts


def test_supports():
    ext = MarkupExtractor()
    assert ext.supports(Path("foo.html"))
    assert ext.supports(Path("foo.htm"))
    assert ext.supports(Path("foo.xml"))
    assert ext.supports(Path("foo.svg"))
    assert not ext.supports(Path("foo.py"))


def test_whitespace_only_skipped():
    ext = MarkupExtractor()
    html = b"<div>   </div><p>Real text</p>"
    candidates = ext.extract(Path("test.html"), html)
    texts = [c.segment.source_text for c in candidates]
    assert "Real text" in texts
    assert "   " not in texts


def test_all_ui_attrs_emitted():
    """alt, aria-label, placeholder all emitted as UI_ATTRIBUTE."""
    ext = MarkupExtractor()
    html = (
        b'<input placeholder="Enter name" title="Name field" '
        b'alt="icon" aria-label="Username input" />'
    )
    candidates = ext.extract(Path("test.html"), html)
    attrs = [(c.segment.kind, c.segment.source_text) for c in candidates if c.segment.kind == SegmentKind.UI_ATTRIBUTE]
    assert (SegmentKind.UI_ATTRIBUTE, "Enter name") in attrs
    assert (SegmentKind.UI_ATTRIBUTE, "Name field") in attrs
    assert (SegmentKind.UI_ATTRIBUTE, "icon") in attrs
    assert (SegmentKind.UI_ATTRIBUTE, "Username input") in attrs


def test_svg_id_attr_not_extracted():
    """SVG id attributes are never extracted (not in _UI_ATTRS)."""
    ext = MarkupExtractor()
    svg = b'<svg><circle id="main-circle" /><text>Draw</text></svg>'
    candidates = ext.extract(Path("test.svg"), svg)
    texts = [c.segment.source_text for c in candidates]
    assert "Draw" in texts
    assert "main-circle" not in texts


def test_byte_offsets_roundtrip():
    """file_bytes[start_byte:end_byte].decode('utf-8') == source_text for all candidates."""
    ext = MarkupExtractor()
    # Include multi-byte chars to stress byte offsets
    html = '<div title="日本語"><p>こんにちは世界</p></div>'.encode("utf-8")
    candidates = ext.extract(Path("test.html"), html)
    assert len(candidates) >= 2
    for c in candidates:
        seg = c.segment
        assert html[seg.start_byte:seg.end_byte].decode("utf-8") == seg.source_text
