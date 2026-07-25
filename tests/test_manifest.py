"""Tests for repo_translator.manifest — Manifest read/write/streaming."""

import json

from repo_translator.manifest import Manifest, ManifestError
from repo_translator.segments import ManifestHeader, Segment, build_segment


def _make_seg(path="src/a.py", text="你好世界", start=0) -> Segment:
    """Build a valid segment from minimal inputs."""
    file_bytes = text.encode("utf-8")
    return build_segment(
        path=path,
        kind="comment",
        start_byte=start,
        end_byte=len(file_bytes),
        file_bytes=file_bytes,
        source_text=text,
    )


def test_write_read_round_trip(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/repo")

    man = Manifest.open_for_write(manifest_path, header)
    seg = _make_seg()
    man.append(seg)
    man.append(seg)
    man.finalize()

    hdr, segments = Manifest.read(manifest_path)
    assert hdr.segment_count == 2
    assert hdr.source_lang == "zh"
    assert hdr.created_at  # non-empty ISO timestamp
    assert len(segments) == 2
    assert segments[0].source_text == "你好世界"


def test_iter_segments_streaming(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/repo")

    man = Manifest.open_for_write(manifest_path, header)
    for i in range(5):
        man.append(_make_seg())
    man.finalize()

    count = 0
    for seg in Manifest.iter_segments(manifest_path):
        assert seg.source_text == "你好世界"
        count += 1
    assert count == 5


def test_finalize_sets_segment_count(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")

    man = Manifest.open_for_write(manifest_path, header)
    man.append(_make_seg())
    man.append(_make_seg())
    man.append(_make_seg())
    man.finalize()

    # Read back raw first line and check segment_count
    first_line = manifest_path.read_text("utf-8").splitlines()[0]
    parsed = json.loads(first_line)
    assert parsed["segment_count"] == 3


def test_malformed_line_raises_with_lineno(tmp_path):
    import pytest

    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    man = Manifest.open_for_write(manifest_path, header)
    man.append(_make_seg())
    man.finalize()

    # Corrupt line 3 (append a bad line)
    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")

    with pytest.raises(ManifestError, match="Line 3"):
        Manifest.read(manifest_path)

    with pytest.raises(ManifestError, match="Line 3"):
        list(Manifest.iter_segments(manifest_path))


def test_missing_field_raises_with_lineno(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    # Write header manually, then a segment line missing required fields
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(header.to_json_line() + "\n")
        f.write(json.dumps({"id": "abc"}) + "\n")  # missing most fields

    import pytest

    with pytest.raises(ManifestError, match="Line 2"):
        Manifest.read(manifest_path)


def test_created_at_is_iso_utc(tmp_path):
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    man = Manifest.open_for_write(manifest_path, header)
    man.finalize()

    hdr, _ = Manifest.read(manifest_path)
    # Should parse as ISO-8601 with timezone info
    from datetime import datetime

    dt = datetime.fromisoformat(hdr.created_at)
    assert dt.tzinfo is not None  # UTC


def test_ensure_ascii_false(tmp_path):
    """Non-ASCII should appear verbatim in the file, not as \\uXXXX."""
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    man = Manifest.open_for_write(manifest_path, header)
    man.append(_make_seg(text="你好世界"))
    man.finalize()

    raw = manifest_path.read_text("utf-8")
    assert "你好世界" in raw
    assert "\\u" not in raw


def test_rewrite_targets_fills_target_text(tmp_path):
    """rewrite_targets atomically updates target_text for matching segment ids."""
    from repo_translator.manifest import rewrite_targets

    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    man = Manifest.open_for_write(manifest_path, header)

    seg1 = _make_seg(path="src/a.py", text="你好世界")
    seg2 = _make_seg(path="src/b.py", text="测试数据")
    man.append(seg1)
    man.append(seg2)
    man.finalize()

    # Rewrite only the first segment's target
    rewrite_targets(manifest_path, {seg1.id: "hello world"})

    _, segs = Manifest.read(manifest_path)
    assert segs[0].target_text == "hello world"
    assert segs[1].target_text is None


def test_rewrite_targets_noop_on_empty(tmp_path):
    """Empty dict means no disk I/O."""
    from repo_translator.manifest import rewrite_targets

    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(source_lang="zh", target_lang="en", repo_root="/r")
    man = Manifest.open_for_write(manifest_path, header)
    man.append(_make_seg())
    man.finalize()

    mtime_before = manifest_path.stat().st_mtime
    rewrite_targets(manifest_path, {})
    assert manifest_path.stat().st_mtime == mtime_before
