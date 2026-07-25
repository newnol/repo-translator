"""Property-based tests for repo_translator.segments using hypothesis.

**Validates: Requirements 4.1, 4.5, 4.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from repo_translator.segments import SegmentKind, build_segment


# --- Strategies ---

# CJK ranges: common CJK Unified Ideographs
_cjk_chars = st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF)
_ascii_text = st.text(st.characters(min_codepoint=0x20, max_codepoint=0x7E), min_size=1, max_size=20)
_cjk_text = st.text(_cjk_chars, min_size=1, max_size=20)

# Mixed ASCII + CJK text (at least one char)
_mixed_text = st.builds(
    lambda a, c: a + c,
    _ascii_text,
    _cjk_text,
)

# File content: optional prefix (with possible newlines) + source span + optional suffix
_PREFIX = st.text(
    st.characters(min_codepoint=0x0A, max_codepoint=0x7E),  # includes \n
    min_size=0,
    max_size=60,
)
_SUFFIX = st.text(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=30,
)


@st.composite
def valid_file_and_span(draw):
    """Generate (file_bytes, start_byte, end_byte, source_text) with the round-trip property."""
    prefix = draw(_PREFIX)
    source_text = draw(_mixed_text)
    suffix = draw(_SUFFIX)

    file_content = prefix + source_text + suffix
    file_bytes = file_content.encode("utf-8")
    start_byte = len(prefix.encode("utf-8"))
    end_byte = start_byte + len(source_text.encode("utf-8"))

    # Sanity: the round-trip must hold by construction
    assert file_bytes[start_byte:end_byte].decode("utf-8") == source_text
    return file_bytes, start_byte, end_byte, source_text


# --- Property 2: Segment construction round-trip ---


class TestBuildSegmentRoundTrip:
    """**Validates: Requirements 4.1, 4.5, 4.6**"""

    @given(data=valid_file_and_span())
    @settings(max_examples=200)
    def test_source_text_matches_slice(self, data):
        """build_segment succeeds and returned segment.source_text == file_bytes[start:end].decode()."""
        file_bytes, start_byte, end_byte, source_text = data

        seg = build_segment(
            path="src/file.py",
            kind=SegmentKind.STRING,
            start_byte=start_byte,
            end_byte=end_byte,
            file_bytes=file_bytes,
            source_text=source_text,
        )
        assert seg.source_text == source_text
        assert seg.source_text == file_bytes[start_byte:end_byte].decode("utf-8")

    @given(data=valid_file_and_span())
    @settings(max_examples=200)
    def test_line_computed_correctly(self, data):
        """Line is 1-based count of newlines before start_byte + 1."""
        file_bytes, start_byte, end_byte, source_text = data

        seg = build_segment(
            path="src/file.py",
            kind=SegmentKind.STRING,
            start_byte=start_byte,
            end_byte=end_byte,
            file_bytes=file_bytes,
            source_text=source_text,
        )
        # Count newlines in bytes before start_byte
        expected_line = file_bytes[:start_byte].count(b"\n") + 1
        assert seg.line == expected_line

    @given(data=valid_file_and_span())
    @settings(max_examples=200)
    def test_column_computed_correctly(self, data):
        """Column is 0-based number of UTF-8 chars between last newline before start_byte and start_byte."""
        file_bytes, start_byte, end_byte, source_text = data

        seg = build_segment(
            path="src/file.py",
            kind=SegmentKind.STRING,
            start_byte=start_byte,
            end_byte=end_byte,
            file_bytes=file_bytes,
            source_text=source_text,
        )
        # Find last newline byte offset before start_byte
        prefix_bytes = file_bytes[:start_byte]
        last_nl = prefix_bytes.rfind(b"\n")
        if last_nl == -1:
            line_start = 0
        else:
            line_start = last_nl + 1
        expected_col = len(file_bytes[line_start:start_byte].decode("utf-8"))
        assert seg.column == expected_col

    @given(
        file_bytes=st.binary(min_size=1, max_size=100),
        start_byte=st.integers(min_value=-10, max_value=-1),
    )
    def test_negative_start_raises(self, file_bytes, start_byte):
        """start_byte < 0 raises ValueError."""
        with pytest.raises(ValueError):
            build_segment(
                path="src/file.py",
                kind=SegmentKind.STRING,
                start_byte=start_byte,
                end_byte=len(file_bytes),
                file_bytes=file_bytes,
                source_text="x",
            )

    @given(file_bytes=st.binary(min_size=2, max_size=100))
    def test_end_less_than_start_raises(self, file_bytes):
        """end_byte < start_byte raises ValueError."""
        # Pick a valid start, then set end before it
        start = len(file_bytes) // 2
        assume(start > 0)
        with pytest.raises(ValueError):
            build_segment(
                path="src/file.py",
                kind=SegmentKind.STRING,
                start_byte=start,
                end_byte=start - 1,
                file_bytes=file_bytes,
                source_text="x",
            )

    @given(file_bytes=st.binary(min_size=1, max_size=50))
    def test_end_beyond_file_raises(self, file_bytes):
        """end_byte > len(file_bytes) raises ValueError."""
        with pytest.raises(ValueError):
            build_segment(
                path="src/file.py",
                kind=SegmentKind.STRING,
                start_byte=0,
                end_byte=len(file_bytes) + 1,
                file_bytes=file_bytes,
                source_text="x",
            )

    @given(data=valid_file_and_span())
    @settings(max_examples=50)
    def test_mismatch_source_text_raises(self, data):
        """source_text != file_bytes[start:end].decode() raises ValueError."""
        file_bytes, start_byte, end_byte, source_text = data

        wrong_text = source_text + "WRONG"
        with pytest.raises(ValueError, match="Round-trip mismatch"):
            build_segment(
                path="src/file.py",
                kind=SegmentKind.STRING,
                start_byte=start_byte,
                end_byte=end_byte,
                file_bytes=file_bytes,
                source_text=wrong_text,
            )
