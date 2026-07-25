"""Tests for TranslationMemory load/save."""

import os
from pathlib import Path

import pytest

from repo_translator.manifest import (
    MemoryEntry,
    TranslationMemory,
    load_memory,
    save_memory,
)


def test_load_none_path():
    assert load_memory(None).entries == {}


def test_load_missing_file(tmp_path):
    assert load_memory(tmp_path / "nope.json").entries == {}


def test_round_trip(tmp_path):
    mem = TranslationMemory(entries={
        "k1": MemoryEntry("k1", "你好", "hello", "comment", hits=3),
    })
    p = tmp_path / "mem.json"
    save_memory(p, mem)
    loaded = load_memory(p)
    e = loaded.entries["k1"]
    assert e.translation_key == "k1"
    assert e.source_text == "你好"
    assert e.target_text == "hello"
    assert e.kind == "comment"
    assert e.hits == 3


def test_save_excludes_null_target(tmp_path):
    """Entries with target_text=None are not persisted (shouldn't normally exist, but guard)."""
    mem = TranslationMemory(entries={
        "k1": MemoryEntry("k1", "src", None, "comment"),  # type: ignore[arg-type]
        "k2": MemoryEntry("k2", "src2", "tgt2", "string"),
    })
    p = tmp_path / "mem.json"
    save_memory(p, mem)
    loaded = load_memory(p)
    assert "k1" not in loaded.entries
    assert "k2" in loaded.entries


def test_load_unparseable_halts(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError, match=str(p)):
        load_memory(p)


def test_atomic_save_leaves_prior_on_failure(tmp_path, monkeypatch):
    """If save fails mid-write, the prior file stays intact."""
    p = tmp_path / "mem.json"
    mem = TranslationMemory(entries={
        "k1": MemoryEntry("k1", "a", "b", "comment"),
    })
    save_memory(p, mem)
    original = p.read_text()

    # Monkey-patch os.replace to simulate failure after tmp written
    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    mem2 = TranslationMemory(entries={
        "k2": MemoryEntry("k2", "x", "y", "string"),
    })
    with pytest.raises(OSError):
        save_memory(p, mem2)

    # Original file unchanged
    assert p.read_text() == original


# --- Property-based test ---

from hypothesis import given, settings
from hypothesis import strategies as st

from repo_translator.segments import SegmentKind

_KINDS = sorted(SegmentKind.all_kinds())


@st.composite
def memory_entries(draw):
    """Generate a dict of MemoryEntry keyed by translation_key."""
    n = draw(st.integers(min_value=0, max_value=20))
    entries = {}
    for _ in range(n):
        key = draw(st.text(min_size=1, max_size=40))
        source = draw(st.text(min_size=1, max_size=60))
        target = draw(st.text(min_size=1, max_size=60))
        kind = draw(st.sampled_from(_KINDS))
        hits = draw(st.integers(min_value=0, max_value=10000))
        entries[key] = MemoryEntry(
            translation_key=key,
            source_text=source,
            target_text=target,
            kind=kind,
            hits=hits,
        )
    return entries


class TestMemoryPersistenceRoundTrip:
    """**Validates: Requirements 12.4**

    Property 4: Memory persistence round-trip — saving then reloading recovers
    translation_key, source_text, target_text, kind, and hits for every entry.
    """

    @given(entries=memory_entries())
    @settings(max_examples=200)
    def test_save_load_recovers_all_fields(self, tmp_path_factory, entries):
        p = tmp_path_factory.mktemp("mem") / "memory.json"
        mem = TranslationMemory(entries=entries)
        save_memory(p, mem)
        loaded = load_memory(p)

        assert loaded.entries.keys() == entries.keys()
        for key, original in entries.items():
            recovered = loaded.entries[key]
            assert recovered.translation_key == original.translation_key
            assert recovered.source_text == original.source_text
            assert recovered.target_text == original.target_text
            assert recovered.kind == original.kind
            assert recovered.hits == original.hits
