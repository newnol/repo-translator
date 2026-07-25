"""Unit tests for APPLY stage guards: drift, overlap, structured validation."""

import tempfile
from pathlib import Path

import pytest

from repo_translator.applicator import ApplyError, apply_manifest
from repo_translator.manifest import Manifest, ManifestHeader
from repo_translator.segments import SegmentKind, build_segment


def _write_manifest(tmpdir, src, filename, content, segments_spec):
    """Build source tree + manifest. segments_spec: list of (start, end, src_text, tgt_text, kind).

    Returns (src_dir, manifest_path).
    """
    src_dir = tmpdir / "src"
    src_dir.mkdir(exist_ok=True)
    f = src_dir / filename
    f.write_text(content, encoding="utf-8")
    file_bytes = f.read_bytes()

    manifest_path = tmpdir / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root=str(src_dir))
    m = Manifest.open_for_write(manifest_path, header)
    for start, end, src_text, tgt_text, kind in segments_spec:
        seg = build_segment(
            path=filename,
            kind=kind,
            start_byte=start,
            end_byte=end,
            file_bytes=file_bytes,
            source_text=src_text,
        )
        seg.target_text = tgt_text
        m.append(seg)
    m.finalize()
    return src_dir, manifest_path


def test_source_text_drift_raises():
    """Segment source_text doesn't match file bytes at [start:end] even though hash matches.

    We achieve drift by having two segments where the first splice changes byte lengths,
    but we simulate it more directly: we write the manifest with a corrupted source_text
    (and matching source_sha256 so it deserializes) that doesn't match what's actually at
    those bytes in the file.
    """
    import hashlib
    import json

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = "# 这是注释\nprint('hi')\n"
        file_bytes = content.encode("utf-8")
        source_text = "这是注释"
        start = 2
        end = start + len(source_text.encode("utf-8"))

        src_dir = tmpdir / "src"
        src_dir.mkdir()
        (src_dir / "hello.py").write_text(content, encoding="utf-8")

        # Build a valid segment, then manually write a tampered manifest line
        seg = build_segment(
            path="hello.py",
            kind=SegmentKind.COMMENT,
            start_byte=start,
            end_byte=end,
            file_bytes=file_bytes,
            source_text=source_text,
        )
        seg.target_text = "This is a comment"

        # Corrupt source_text + fix source_sha256 so manifest loads
        fake_text = "完全不同的文本"
        seg.source_text = fake_text
        seg.source_sha256 = hashlib.sha256(fake_text.encode("utf-8")).hexdigest()

        manifest_path = tmpdir / "manifest.jsonl"
        header = ManifestHeader(source_lang="zh", target_lang="en", repo_root=str(src_dir))
        m = Manifest.open_for_write(manifest_path, header)
        m.append(seg)
        m.finalize()

        out = tmpdir / "out"
        with pytest.raises(ApplyError, match="source_text drift"):
            apply_manifest(manifest_path, src_dir, out)


def test_overlapping_ranges_raises():
    """Two segments with overlapping byte ranges raise ApplyError."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        # "AABBCC" — we'll create two overlapping segments: [0:4) and [2:6)
        content = "这是一段话语"  # 6 CJK chars = 18 bytes
        file_bytes = content.encode("utf-8")
        # seg1: bytes [0:9) = "这是一" (3 chars × 3 bytes)
        # seg2: bytes [6:18) = "一段话语" (overlaps at [6:9))
        seg1_text = "这是一"
        seg2_text = "一段话语"
        assert file_bytes[0:9].decode("utf-8") == seg1_text
        assert file_bytes[6:18].decode("utf-8") == seg2_text

        src_dir = tmpdir / "src"
        src_dir.mkdir()
        (src_dir / "hello.py").write_text(content, encoding="utf-8")

        seg1 = build_segment(
            path="hello.py", kind=SegmentKind.STRING,
            start_byte=0, end_byte=9, file_bytes=file_bytes, source_text=seg1_text,
        )
        seg1.target_text = "ABC"
        seg2 = build_segment(
            path="hello.py", kind=SegmentKind.STRING,
            start_byte=6, end_byte=18, file_bytes=file_bytes, source_text=seg2_text,
        )
        seg2.target_text = "DEFG"

        manifest_path = tmpdir / "manifest.jsonl"
        header = ManifestHeader(source_lang="zh", target_lang="en", repo_root=str(src_dir))
        m = Manifest.open_for_write(manifest_path, header)
        m.append(seg1)
        m.append(seg2)
        m.finalize()

        out = tmpdir / "out"
        with pytest.raises(ApplyError, match="overlapping"):
            apply_manifest(manifest_path, src_dir, out)


def test_broken_structured_output_leaves_original():
    """Invalid JSON after splice → original file kept, failure recorded in stats."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        # Valid JSON with a CJK value we'll replace with content that breaks the JSON
        content = '{"name": "测试值"}\n'
        file_bytes = content.encode("utf-8")
        # Locate "测试值" inside the JSON
        src_text = "测试值"
        start = file_bytes.index(src_text.encode("utf-8"))
        end = start + len(src_text.encode("utf-8"))

        # Inject an unescaped quote that makes the whole file unparseable JSON
        bad_target = 'broken"json'

        src_dir, manifest_path = _write_manifest(
            tmpdir, None, "data.json", content,
            [(start, end, src_text, bad_target, SegmentKind.JSON_VALUE)],
        )

        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src_dir, out)

        # Original file left in place (copied-through)
        assert (out / "data.json").read_text(encoding="utf-8") == content
        assert stats.files_skipped_validation == 1
        assert stats.files_spliced == 0


def test_structured_validation_success():
    """Valid JSON after splice → file is written with translated content."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = '{"name": "测试值"}\n'
        file_bytes = content.encode("utf-8")
        src_text = "测试值"
        start = file_bytes.index(src_text.encode("utf-8"))
        end = start + len(src_text.encode("utf-8"))

        src_dir, manifest_path = _write_manifest(
            tmpdir, None, "data.json", content,
            [(start, end, src_text, "test value", SegmentKind.JSON_VALUE)],
        )

        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src_dir, out)

        result = (out / "data.json").read_text(encoding="utf-8")
        assert '"test value"' in result
        assert stats.files_spliced == 1
        assert stats.files_skipped_validation == 0
