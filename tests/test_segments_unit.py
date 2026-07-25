"""Unit tests for Segment validation and JSON escaping."""

import hashlib
import json

import pytest

from repo_translator.segments import Segment, SegmentKind


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_segment(*, source_text: str = "hello", target_text=None, **overrides):
    """Build a valid Segment with sane defaults; override any field via kwargs."""
    defaults = dict(
        id="abc123",
        path="src/main.py",
        kind="comment",
        start_byte=0,
        end_byte=5,
        line=1,
        column=0,
        source_text=source_text,
        target_text=target_text,
        file_sha256="0" * 64,
        source_sha256=_sha256(source_text),
        translation_key="k",
    )
    defaults.update(overrides)
    return Segment(**defaults)


# --- Byte-range validation ---


def test_rejects_end_lte_start():
    with pytest.raises(ValueError, match="Invalid byte range"):
        _make_segment(start_byte=5, end_byte=5)


def test_rejects_negative_start_byte():
    with pytest.raises(ValueError, match="Invalid byte range"):
        _make_segment(start_byte=-1, end_byte=5)


# --- Path validation ---


def test_rejects_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        _make_segment(path="/etc/passwd")


def test_rejects_dotdot_traversal():
    with pytest.raises(ValueError, match="traversal"):
        _make_segment(path="src/../secret.py")


# --- Kind validation ---


def test_rejects_invalid_kind():
    with pytest.raises(ValueError, match="Invalid kind"):
        _make_segment(kind="bogus_kind")


# --- source_sha256 mismatch ---


def test_rejects_sha_mismatch():
    with pytest.raises(ValueError, match="source_sha256 mismatch"):
        _make_segment(source_sha256="0" * 64)


# --- is_translated property ---


def test_is_translated_false_when_target_none():
    seg = _make_segment(target_text=None)
    assert seg.is_translated is False


def test_is_translated_true_when_target_is_string():
    seg = _make_segment(target_text="")
    assert seg.is_translated is True


# --- JSON escaping ---


def test_to_json_line_no_unicode_escape():
    cjk = "你好世界"
    seg = _make_segment(source_text=cjk, end_byte=len(cjk.encode("utf-8")))
    line = seg.to_json_line()
    # Must contain the raw CJK chars, not \\uXXXX escapes
    assert cjk in line
    assert "\\u" not in line


# --- Roundtrip ---


def test_from_json_line_roundtrip():
    seg = _make_segment(target_text="world")
    recovered = Segment.from_json_line(seg.to_json_line())
    assert recovered == seg
