"""Smoke test for applicator.py."""

import tempfile
from pathlib import Path

import pytest

from repo_translator.applicator import ApplyError, ApplyStats, apply_manifest
from repo_translator.manifest import Manifest, ManifestHeader
from repo_translator.segments import SegmentKind, build_segment


def _make_source_and_manifest(tmpdir: Path, content: str, segments_spec: list):
    """Helper: create source tree + manifest from segment specs.

    segments_spec: list of (start_byte, end_byte, source_text, target_text, kind)
    """
    src = tmpdir / "src"
    src.mkdir()
    test_file = src / "hello.py"
    test_file.write_text(content, encoding="utf-8")
    file_bytes = test_file.read_bytes()

    manifest_path = tmpdir / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root=str(src))
    m = Manifest.open_for_write(manifest_path, header)
    for start, end, src_text, tgt_text, kind in segments_spec:
        seg = build_segment(
            path="hello.py",
            kind=kind,
            start_byte=start,
            end_byte=end,
            file_bytes=file_bytes,
            source_text=src_text,
        )
        seg.target_text = tgt_text
        m.append(seg)
    m.finalize()
    return src, manifest_path


def test_basic_splice():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = "# \u8fd9\u662f\u6ce8\u91ca\nprint(\"hello\")\n"
        # "# 这是注释\n..." — CJK starts at byte 2, ends at byte 14
        file_bytes = content.encode("utf-8")
        source_text = "\u8fd9\u662f\u6ce8\u91ca"
        start = 2
        end = start + len(source_text.encode("utf-8"))
        assert file_bytes[start:end].decode("utf-8") == source_text

        src, manifest_path = _make_source_and_manifest(
            tmpdir,
            content,
            [(start, end, source_text, "This is a comment", SegmentKind.COMMENT)],
        )
        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src, out)

        result = (out / "hello.py").read_text(encoding="utf-8")
        assert "# This is a comment" in result
        assert stats.files_spliced == 1
        assert stats.segments_applied == 1


def test_null_target_leaves_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = "# \u8fd9\u662f\u6ce8\u91ca\n"
        file_bytes = content.encode("utf-8")
        source_text = "\u8fd9\u662f\u6ce8\u91ca"
        start = 2
        end = start + len(source_text.encode("utf-8"))

        src, manifest_path = _make_source_and_manifest(
            tmpdir,
            content,
            [(start, end, source_text, None, SegmentKind.COMMENT)],
        )
        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src, out)

        result = (out / "hello.py").read_text(encoding="utf-8")
        assert result == content
        assert stats.files_spliced == 1
        assert stats.segments_applied == 0


def test_reject_output_aliasing_source():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "repo"
        src.mkdir()
        (src / "f.txt").write_text("hi")
        manifest = Path(tmp) / "m.jsonl"
        header = ManifestHeader()
        m = Manifest.open_for_write(manifest, header)
        m.finalize()

        with pytest.raises(ApplyError, match="alias"):
            apply_manifest(manifest, src, src)


def test_reject_non_empty_output():
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "repo"
        src.mkdir()
        (src / "f.txt").write_text("hi")
        out = Path(tmp) / "out"
        out.mkdir()
        (out / "existing.txt").write_text("stuff")

        manifest = Path(tmp) / "m.jsonl"
        header = ManifestHeader()
        m = Manifest.open_for_write(manifest, header)
        m.finalize()

        with pytest.raises(ApplyError, match="not empty"):
            apply_manifest(manifest, src, out)


def test_hash_mismatch_raises():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = "# \u8fd9\u662f\u6ce8\u91ca\n"
        file_bytes = content.encode("utf-8")
        source_text = "\u8fd9\u662f\u6ce8\u91ca"
        start = 2
        end = start + len(source_text.encode("utf-8"))

        src, manifest_path = _make_source_and_manifest(
            tmpdir,
            content,
            [(start, end, source_text, "comment", SegmentKind.COMMENT)],
        )
        # Mutate source after manifest was built
        (src / "hello.py").write_text("# modified\n", encoding="utf-8")

        out = tmpdir / "out"
        with pytest.raises(ApplyError, match="changed since extract"):
            apply_manifest(manifest_path, src, out)


def test_hash_mismatch_skip_mode():
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        content = "# \u8fd9\u662f\u6ce8\u91ca\n"
        file_bytes = content.encode("utf-8")
        source_text = "\u8fd9\u662f\u6ce8\u91ca"
        start = 2
        end = start + len(source_text.encode("utf-8"))

        src, manifest_path = _make_source_and_manifest(
            tmpdir,
            content,
            [(start, end, source_text, "comment", SegmentKind.COMMENT)],
        )
        # Mutate source
        (src / "hello.py").write_text("# modified\n", encoding="utf-8")

        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src, out, fail_on_source_mismatch=False)
        assert stats.files_skipped_mismatch == 1
        # File should be the copied-through (modified) version
        assert (out / "hello.py").read_text() == "# modified\n"


def test_descending_splice_multi_segment():
    """Multiple segments per file, spliced in descending order."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        # Two CJK spans
        content = "a\u4e00b\u4e8cc"
        file_bytes = content.encode("utf-8")
        # 'a' = 1 byte, '一' = 3 bytes, 'b' = 1 byte, '二' = 3 bytes, 'c' = 1 byte
        # offsets: a[0:1] 一[1:4] b[4:5] 二[5:8] c[8:9]
        assert file_bytes[1:4].decode("utf-8") == "\u4e00"
        assert file_bytes[5:8].decode("utf-8") == "\u4e8c"

        src, manifest_path = _make_source_and_manifest(
            tmpdir,
            content,
            [
                (1, 4, "\u4e00", "ONE", SegmentKind.STRING),
                (5, 8, "\u4e8c", "TWO", SegmentKind.STRING),
            ],
        )
        out = tmpdir / "out"
        stats = apply_manifest(manifest_path, src, out)

        result = (out / "hello.py").read_text(encoding="utf-8")
        assert result == "aONEbTWOc"
        assert stats.segments_applied == 2
