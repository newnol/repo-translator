"""Unit tests for TRANSLATE-MANIFEST reuse, dedup, and idempotency."""

import json
import tempfile
from pathlib import Path

import pytest

from repo_translator.manifest import (
    Manifest,
    ManifestStats,
    MemoryEntry,
    TranslationMemory,
    translate_manifest,
)
from repo_translator.segments import ManifestHeader, SegmentKind, build_segment


def _make_manifest(tmp_path: Path, segments_data: list[dict]) -> Path:
    """Write a minimal JSONL manifest and return its path."""
    manifest_path = tmp_path / "manifest.jsonl"
    header = ManifestHeader(segment_count=len(segments_data))
    lines = [header.to_json_line()]
    for seg in segments_data:
        lines.append(json.dumps(seg, ensure_ascii=False))
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def _segment_dict(source_text: str, *, target_text=None, idx=0) -> dict:
    """Build a valid segment dict using build_segment, then serialize."""
    content = f"prefix {source_text} suffix"
    file_bytes = content.encode("utf-8")
    start = content.index(source_text)
    end = start + len(source_text.encode("utf-8"))
    seg = build_segment(
        path=f"file{idx}.md",
        kind=SegmentKind.MARKDOWN_PARAGRAPH,
        start_byte=start,
        end_byte=end,
        file_bytes=file_bytes,
        source_text=source_text,
    )
    d = json.loads(seg.to_json_line())
    d["target_text"] = target_text
    return d


class FakeTranslator:
    """Translator that uppercases text. Tracks call count."""

    def __init__(self, *, fail_times=0, wrong_count=False):
        self.calls = 0
        self._fail_times = fail_times
        self._wrong_count = wrong_count

    def translate_batch(self, texts: list[str]) -> list[str]:
        self.calls += 1
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("provider error")
        if self._wrong_count:
            return []  # deliberately wrong length
        return [t.upper() for t in texts]


class TestMemoryHit:
    """Memory hit: reuse + increment hits, zero provider calls."""

    def test_memory_hit_reuses_and_increments(self, tmp_path):
        seg = _segment_dict("hello world", idx=0)
        manifest_path = _make_manifest(tmp_path, [seg])

        memory = TranslationMemory(entries={
            seg["translation_key"]: MemoryEntry(
                translation_key=seg["translation_key"],
                source_text="hello world",
                target_text="HELLO WORLD",
                kind=seg["kind"],
                hits=2,
            )
        })

        translator = FakeTranslator()
        stats = translate_manifest(manifest_path, translator, memory=memory)

        assert translator.calls == 0
        assert stats.segments_from_memory == 1
        assert stats.segments_from_provider == 0
        assert memory.entries[seg["translation_key"]].hits == 3


class TestMemoryMiss:
    """Memory miss: calls provider, stores entry with hits=0."""

    def test_miss_stores_entry(self, tmp_path):
        seg = _segment_dict("hello world", idx=0)
        manifest_path = _make_manifest(tmp_path, [seg])

        memory = TranslationMemory(entries={})
        translator = FakeTranslator()
        stats = translate_manifest(manifest_path, translator, memory=memory)

        assert translator.calls == 1
        assert stats.segments_from_provider == 1
        entry = memory.entries[seg["translation_key"]]
        assert entry.hits == 0
        assert entry.target_text == "HELLO WORLD"


class TestDedup:
    """Two segments with same translation_key → one provider call."""

    def test_dedup_single_call(self, tmp_path):
        # Two segments at different positions but same source_text and kind → same key
        content = "hello world hello world"
        file_bytes = content.encode("utf-8")
        # First occurrence
        seg1 = build_segment(
            path="a.md", kind=SegmentKind.MARKDOWN_PARAGRAPH,
            start_byte=0, end_byte=11, file_bytes=file_bytes, source_text="hello world",
        )
        # Second occurrence
        seg2 = build_segment(
            path="a.md", kind=SegmentKind.MARKDOWN_PARAGRAPH,
            start_byte=12, end_byte=23, file_bytes=file_bytes, source_text="hello world",
        )
        assert seg1.translation_key == seg2.translation_key

        d1 = json.loads(seg1.to_json_line())
        d2 = json.loads(seg2.to_json_line())
        manifest_path = _make_manifest(tmp_path, [d1, d2])

        translator = FakeTranslator()
        stats = translate_manifest(manifest_path, translator, memory=TranslationMemory(entries={}))

        assert translator.calls == 1
        assert stats.segments_from_provider == 2


class TestIdempotency:
    """All segments already translated → zero provider calls."""

    def test_no_calls_when_already_translated(self, tmp_path):
        seg = _segment_dict("hello world", target_text="HELLO WORLD", idx=0)
        manifest_path = _make_manifest(tmp_path, [seg])

        translator = FakeTranslator()
        stats = translate_manifest(manifest_path, translator, memory=TranslationMemory(entries={}))

        assert translator.calls == 0
        assert stats.segments_translated == 1
        assert stats.segments_from_provider == 0


class TestCountMismatch:
    """Provider returns wrong number of results → raises ValueError."""

    def test_raises_on_count_mismatch(self, tmp_path):
        # Use two segments with different keys so there are multiple spans in a batch
        s1 = _segment_dict("hello world", idx=0)
        s2 = _segment_dict("goodbye world", idx=1)
        manifest_path = _make_manifest(tmp_path, [s1, s2])

        translator = FakeTranslator(wrong_count=True)
        with pytest.raises(ValueError, match="returned .* results"):
            translate_manifest(manifest_path, translator, memory=TranslationMemory(entries={}))


class TestRetryExhaustion:
    """Provider fails twice → raises (first failure triggers retry, second propagates)."""

    def test_raises_after_retry_exhausted(self, tmp_path):
        seg = _segment_dict("hello world", idx=0)
        manifest_path = _make_manifest(tmp_path, [seg])

        translator = FakeTranslator(fail_times=2)
        with pytest.raises(RuntimeError, match="provider error"):
            translate_manifest(manifest_path, translator, memory=TranslationMemory(entries={}))
        # Two calls: original + one retry
        assert translator.calls == 2
