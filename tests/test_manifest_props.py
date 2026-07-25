"""Property-based tests for Segment manifest serialize/deserialize round-trip.

**Validates: Requirements 2.2**
"""

from __future__ import annotations

import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from repo_translator.segments import Segment, SegmentKind, make_segment_id, make_translation_key


# --- Strategies ---

_kinds = st.sampled_from(list(SegmentKind.all_kinds()))

_repo_path = st.from_regex(r"[a-z][a-z0-9_/]*\.[a-z]{1,4}", fullmatch=True).filter(
    lambda p: ".." not in p.split("/") and not p.startswith("/")
)

_source_text = st.text(
    st.characters(min_codepoint=0x20, max_codepoint=0x9FFF),
    min_size=1,
    max_size=80,
).filter(lambda t: not t.isspace())

_target_text = st.one_of(st.none(), st.text(min_size=1, max_size=80))

_protected_context = st.lists(st.text(min_size=1, max_size=20), max_size=5)


@st.composite
def valid_segment(draw):
    """Generate a fully valid Segment with consistent hashes."""
    path = draw(_repo_path)
    kind = draw(_kinds)
    source_text = draw(_source_text)
    target_text = draw(_target_text)
    protected_context = draw(_protected_context)

    start_byte = draw(st.integers(min_value=0, max_value=10000))
    end_byte = start_byte + draw(st.integers(min_value=1, max_value=5000))

    line = draw(st.integers(min_value=1, max_value=500))
    column = draw(st.integers(min_value=0, max_value=200))

    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    file_sha256 = draw(st.from_regex(r"[0-9a-f]{64}", fullmatch=True))
    seg_id = make_segment_id(path, start_byte, end_byte)
    translation_key = make_translation_key(source_text, kind)

    context_before = draw(st.text(min_size=0, max_size=40))
    context_after = draw(st.text(min_size=0, max_size=40))

    return Segment(
        id=seg_id,
        path=path,
        kind=kind,
        start_byte=start_byte,
        end_byte=end_byte,
        line=line,
        column=column,
        source_text=source_text,
        target_text=target_text,
        file_sha256=file_sha256,
        source_sha256=source_sha256,
        translation_key=translation_key,
        context_before=context_before,
        context_after=context_after,
        protected_context=protected_context,
    )


# --- Property 3: Manifest round-trip fidelity ---


class TestManifestRoundTrip:
    """**Validates: Requirements 2.2**"""

    @given(seg=valid_segment())
    @settings(max_examples=300)
    def test_serialize_deserialize_roundtrip(self, seg: Segment):
        """A Segment serialized to JSON line and deserialized recovers every field."""
        line = seg.to_json_line()
        recovered = Segment.from_json_line(line)

        assert recovered.id == seg.id
        assert recovered.path == seg.path
        assert recovered.kind == seg.kind
        assert recovered.start_byte == seg.start_byte
        assert recovered.end_byte == seg.end_byte
        assert recovered.line == seg.line
        assert recovered.column == seg.column
        assert recovered.source_text == seg.source_text
        assert recovered.target_text == seg.target_text
        assert recovered.file_sha256 == seg.file_sha256
        assert recovered.source_sha256 == seg.source_sha256
        assert recovered.translation_key == seg.translation_key
        assert recovered.context_before == seg.context_before
        assert recovered.context_after == seg.context_after
        assert recovered.protected_context == seg.protected_context
