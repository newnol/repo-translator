"""Property-based tests for repo_translator.applicator using hypothesis.

**Validates: Requirements 13.7**
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from repo_translator.applicator import apply_manifest
from repo_translator.manifest import Manifest, ManifestHeader
from repo_translator.segments import SegmentKind, build_segment


# --- Strategies ---

_CJK = st.text(
    st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF), min_size=1, max_size=8
)
_ASCII_PAD = st.text(
    st.characters(min_codepoint=0x61, max_codepoint=0x7A), min_size=1, max_size=6
)
# Replacement: may be shorter, same, or longer than source (mix of ASCII + CJK)
_REPLACEMENT = st.text(
    st.characters(min_codepoint=0x20, max_codepoint=0x9FFF),
    min_size=0,
    max_size=20,
).filter(lambda s: s.isprintable())


@st.composite
def multi_cjk_line(draw):
    """Generate a single line with 2-4 non-overlapping CJK spans separated by ASCII.

    Returns (line_bytes, spans) where spans is [(start, end, source_text, target_text), ...].
    """
    n_spans = draw(st.integers(min_value=2, max_value=4))
    parts: list[str] = []
    spans: list[tuple[int, int, str, str]] = []

    for i in range(n_spans):
        pad = draw(_ASCII_PAD)
        cjk = draw(_CJK)
        target = draw(_REPLACEMENT)
        parts.append(pad)
        # Compute byte offset of this CJK span within the assembled line
        prefix = "".join(parts[:-1])  # everything before this pad
        prefix_bytes_len = len((prefix + pad).encode("utf-8"))
        # But we also need to account for all prior parts
        # Easier: build incrementally
        start = len("".join(parts).encode("utf-8"))  # after pad
        parts.append(cjk)
        end = len("".join(parts).encode("utf-8"))
        spans.append((start, end, cjk, target))

    # trailing pad so it looks like a line
    parts.append(draw(_ASCII_PAD))
    line = "".join(parts)
    line_bytes = line.encode("utf-8")

    # verify spans are correct
    for s, e, src, _tgt in spans:
        assert line_bytes[s:e].decode("utf-8") == src

    return line_bytes, spans


# --- Property 6: Descending splice correctness ---


class TestDescendingSpliceEquivalence:
    """**Validates: Requirements 13.7**

    With length-changing targets, descending-order splicing equals an independently
    computed reference splice; multiple CJK segments per line stay correct.
    """

    @given(data=multi_cjk_line())
    @settings(max_examples=200)
    def test_splice_matches_reference(self, data):
        """apply_manifest output == independent right-to-left byte splice."""
        line_bytes, spans = data

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            src = tmpdir / "src"
            src.mkdir()
            (src / "f.txt").write_bytes(line_bytes)

            # Build manifest
            manifest_path = tmpdir / "manifest.jsonl"
            header = ManifestHeader(source_lang="zh", target_lang="en", repo_root=str(src))
            m = Manifest.open_for_write(manifest_path, header)
            for start, end, src_text, tgt_text in spans:
                seg = build_segment(
                    path="f.txt",
                    kind=SegmentKind.STRING,
                    start_byte=start,
                    end_byte=end,
                    file_bytes=line_bytes,
                    source_text=src_text,
                )
                seg.target_text = tgt_text
                m.append(seg)
            m.finalize()

            # Run apply_manifest
            out = tmpdir / "out"
            apply_manifest(manifest_path, src, out)
            actual = (out / "f.txt").read_bytes()

            # Reference: independent right-to-left splice on original bytes
            buf = bytearray(line_bytes)
            for start, end, _src_text, tgt_text in sorted(
                spans, key=lambda x: x[0], reverse=True
            ):
                buf[start:end] = tgt_text.encode("utf-8")
            reference = bytes(buf)

            assert actual == reference


# --- Property 5: Identity round-trip ---


@st.composite
def file_with_cjk_spans(draw):
    """Generate file bytes with 1-3 CJK spans at known positions amid ASCII."""
    parts: list[str] = []
    num_spans = draw(st.integers(min_value=1, max_value=3))
    for _ in range(num_spans):
        parts.append(draw(_ASCII_PAD))
        parts.append(draw(_CJK))
    parts.append(draw(_ASCII_PAD))

    file_content = "".join(parts)
    file_bytes = file_content.encode("utf-8")

    # Compute CJK span byte positions
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for i in range(num_spans):
        offset += len(parts[i * 2].encode("utf-8"))
        cjk_src = parts[i * 2 + 1]
        cjk_len = len(cjk_src.encode("utf-8"))
        spans.append((offset, offset + cjk_len, cjk_src))
        offset += cjk_len
    return file_bytes, spans


class TestIdentityRoundTrip:
    """**Validates: Requirements 13.10, 13.12**"""

    @given(data=file_with_cjk_spans())
    @settings(max_examples=200, deadline=None)
    def test_identity_translation_preserves_file(self, data, tmp_path_factory):
        """EXTRACT then APPLY with target_text == source_text reproduces file byte-for-byte."""
        file_bytes, spans = data

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            source_root = tmpdir / "src"
            source_root.mkdir()
            output_root = tmpdir / "out"

            rel_path = "hello.txt"
            (source_root / rel_path).write_bytes(file_bytes)

            # Build segments with identity translation (target == source)
            segments = []
            for start_byte, end_byte, source_text in spans:
                seg = build_segment(
                    path=rel_path,
                    kind=SegmentKind.STRING,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    file_bytes=file_bytes,
                    source_text=source_text,
                )
                seg.target_text = seg.source_text
                segments.append(seg)

            # Write manifest
            manifest_path = tmpdir / "manifest.jsonl"
            header = ManifestHeader(source_lang="zh", target_lang="en")
            m = Manifest.open_for_write(manifest_path, header)
            for seg in segments:
                m.append(seg)
            m.finalize()

            # Apply
            stats = apply_manifest(manifest_path, source_root, output_root)

            # Output must be byte-identical to input
            assert (output_root / rel_path).read_bytes() == file_bytes
            assert stats.segments_applied == len(segments)
