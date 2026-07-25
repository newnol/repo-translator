"""Manifest I/O and Translation Memory for the manifest pipeline."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Iterator

from repo_translator.segments import ManifestHeader, Segment


class ManifestError(Exception):
    """Raised on malformed manifest lines."""


class Manifest:
    """JSONL manifest: header line + segment lines, streamable."""

    def __init__(self, path: Path, header: ManifestHeader, *, _fh: IO[str] | None = None):
        self.path = path
        self.header = header
        self._fh = _fh
        self._count = 0

    # --- write API ---

    @classmethod
    def open_for_write(cls, path: Path, header: ManifestHeader) -> "Manifest":
        """Write header as first JSONL line, open for appending segments."""
        if not header.created_at:
            header.created_at = datetime.now(timezone.utc).isoformat()
        fh = open(path, "w", encoding="utf-8")
        fh.write(header.to_json_line() + "\n")
        return cls(path, header, _fh=fh)

    def append(self, segment: Segment) -> None:
        """Stream one segment as a JSONL line."""
        assert self._fh is not None, "Manifest not open for writing"
        self._fh.write(segment.to_json_line() + "\n")
        self._count += 1

    def finalize(self) -> None:
        """Close file, rewrite header line with actual segment_count."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None

        self.header.segment_count = self._count

        # Read all lines, replace line 1 (header), write back atomically.
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        lines[0] = self.header.to_json_line() + "\n"

        dir_path = self.path.parent
        fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(lines)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    # --- read API ---

    @classmethod
    def read(cls, path: Path) -> tuple[ManifestHeader, list[Segment]]:
        """Parse header + all segments into memory."""
        with open(path, encoding="utf-8") as fh:
            header_line = fh.readline()
            if not header_line:
                raise ManifestError("Empty manifest file")
            header = ManifestHeader.from_json_line(header_line)
            segments: list[Segment] = []
            for lineno, line in enumerate(fh, start=2):
                stripped = line.rstrip("\n")
                if not stripped:
                    continue
                try:
                    seg = Segment.from_json_line(stripped)
                except (json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
                    raise ManifestError(f"Line {lineno}: {e}") from e
                segments.append(seg)
        return header, segments

    @staticmethod
    def iter_segments(path: Path) -> Iterator[Segment]:
        """Streaming reader: yields one Segment at a time, skipping the header."""
        with open(path, encoding="utf-8") as fh:
            fh.readline()  # skip header
            for lineno, line in enumerate(fh, start=2):
                stripped = line.rstrip("\n")
                if not stripped:
                    continue
                try:
                    seg = Segment.from_json_line(stripped)
                except (json.JSONDecodeError, TypeError, KeyError, ValueError) as e:
                    raise ManifestError(f"Line {lineno}: {e}") from e
                yield seg


def rewrite_targets(path: Path, updated: dict[str, str]) -> None:
    """Atomically rewrite the manifest, filling target_text for segment ids in `updated`.

    Uses the same temp-file + os.replace pattern as RepoTranslator._write_text_atomic.
    """
    if not updated:
        return

    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines: list[str] = [lines[0]]  # header unchanged

    for line in lines[1:]:
        stripped = line.rstrip("\n")
        if not stripped:
            new_lines.append(line)
            continue
        obj = json.loads(stripped)
        if obj.get("id") in updated:
            obj["target_text"] = updated[obj["id"]]
            new_lines.append(json.dumps(obj, ensure_ascii=False) + "\n")
        else:
            new_lines.append(line)

    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


@dataclass
class MemoryEntry:
    translation_key: str
    source_text: str
    target_text: str
    kind: str
    hits: int = 0


@dataclass
class TranslationMemory:
    entries: dict[str, MemoryEntry]


def load_memory(path: Path | None) -> TranslationMemory:
    """Load translation memory from JSON. Missing/None path → empty memory.

    Raises on unparseable file (identifies the file in the message).
    """
    if path is None or not path.exists():
        return TranslationMemory(entries={})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {}
        for key, val in data.get("entries", {}).items():
            entries[key] = MemoryEntry(**val)
        return TranslationMemory(entries=entries)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise ValueError(f"Cannot parse translation memory file: {path}") from exc


def save_memory(path: Path, memory: TranslationMemory) -> None:
    """Atomically persist memory (temp file + os.replace). Only stores non-null target_text."""
    entries = {
        k: asdict(v)
        for k, v in memory.entries.items()
        if v.target_text is not None
    }
    content = json.dumps({"entries": entries}, ensure_ascii=False)

    fd, tmp = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# Reuse the PROTECTED_TOKEN_PATTERN from core for token-preserving translation.
from repo_translator.core import PROTECTED_TOKEN_PATTERN


@dataclass
class ManifestStats:
    """Statistics from a translate_manifest run."""

    segments_total: int = 0
    segments_translated: int = 0
    segments_from_memory: int = 0
    segments_from_provider: int = 0
    provider_calls: int = 0


def _translate_preserving_tokens(text: str, translate_fn) -> str:
    """Translate non-protected spans, leaving technical tokens unchanged.

    Mirrors RepoTranslator._translate_preserving_tokens but accepts a plain
    callable instead of requiring a class instance.
    """
    if not text.strip():
        return text
    pieces: list[str] = []
    last = 0
    for match in PROTECTED_TOKEN_PATTERN.finditer(text):
        if match.start() > last:
            pieces.append(translate_fn(text[last : match.start()]))
        pieces.append(match.group(0))
        last = match.end()
    if last < len(text):
        pieces.append(translate_fn(text[last:]))
    return "".join(pieces)


def translate_manifest(
    manifest_path: Path,
    translator,
    *,
    memory: TranslationMemory | None = None,
    memory_path: Path | None = None,
    batch_size: int = 40,
) -> ManifestStats:
    """TRANSLATE-MANIFEST stage.

    For every segment with target_text is None:
      1. If translation_key in memory → reuse, increment hits.
      2. Else batch by distinct translation_key (dedup within batch),
         translate, store into memory with hits=0.
    Writes target_text back into the manifest and persists memory.
    Idempotent: re-running only fills still-missing targets.
    """
    if memory is None:
        memory = TranslationMemory(entries={})

    _, segments = Manifest.read(manifest_path)
    stats = ManifestStats(segments_total=len(segments))

    # Partition: already translated vs needs work
    needs_translation: list[Segment] = []
    for seg in segments:
        if seg.target_text is not None:
            stats.segments_translated += 1
        else:
            needs_translation.append(seg)

    # Phase 1: memory hits
    still_missing: list[Segment] = []
    memory_resolved: dict[str, str] = {}  # segment id → target_text
    for seg in needs_translation:
        entry = memory.entries.get(seg.translation_key)
        if entry is not None:
            memory_resolved[seg.id] = entry.target_text
            entry.hits += 1
            stats.segments_from_memory += 1
            stats.segments_translated += 1
        else:
            still_missing.append(seg)

    # Phase 2: batch translate misses, dedup by translation_key
    # Collect unique keys to translate and track which segments share each key.
    key_to_source: dict[str, tuple[str, str]] = {}  # key → (source_text, kind)
    key_to_segments: dict[str, list[Segment]] = {}
    for seg in still_missing:
        if seg.translation_key not in key_to_source:
            key_to_source[seg.translation_key] = (seg.source_text, seg.kind)
            key_to_segments[seg.translation_key] = []
        key_to_segments[seg.translation_key].append(seg)

    provider_resolved: dict[str, str] = {}  # segment id → target_text
    unique_keys = list(key_to_source.keys())

    # Resolve translate_batch from the translator
    translate_batch = getattr(translator, "translate_batch", None)
    if translate_batch is None:
        def translate_batch(texts):
            return [translator.translate_text(t) for t in texts]

    for batch_start in range(0, len(unique_keys), batch_size):
        batch_keys = unique_keys[batch_start : batch_start + batch_size]

        # Split each source into protected/unprotected pieces, collect all
        # unprotected pieces across the batch, translate in one provider call,
        # then reassemble per-key.
        all_spans: list[str] = []  # unprotected spans to translate
        templates: list[list[str | int]] = []  # per-source template: str=protected, int=index

        for key in batch_keys:
            source_text, _ = key_to_source[key]
            template: list[str | int] = []
            last = 0
            for match in PROTECTED_TOKEN_PATTERN.finditer(source_text):
                if match.start() > last:
                    span = source_text[last : match.start()]
                    if span.strip():
                        template.append(len(all_spans))
                        all_spans.append(span)
                    else:
                        template.append(span)  # whitespace-only, keep as-is
                template.append(match.group(0))
                last = match.end()
            if last < len(source_text):
                span = source_text[last:]
                if span.strip():
                    template.append(len(all_spans))
                    all_spans.append(span)
                else:
                    template.append(span)
            templates.append(template)

        # One provider call for all unprotected spans in this batch
        translated_spans: list[str] = []
        if all_spans:
            try:
                translated_spans = translate_batch(all_spans)
            except Exception:
                # Retry once (Req 20.2)
                translated_spans = translate_batch(all_spans)
            if len(translated_spans) != len(all_spans):
                raise ValueError(
                    f"Translation provider returned {len(translated_spans)} results "
                    f"for {len(all_spans)} inputs"
                )
            stats.provider_calls += 1

        # Reassemble translations
        for key, template in zip(batch_keys, templates):
            parts: list[str] = []
            for piece in template:
                if isinstance(piece, int):
                    parts.append(translated_spans[piece])
                else:
                    parts.append(piece)
            target_text = "".join(parts)

            # Store in memory
            source_text, kind = key_to_source[key]
            memory.entries[key] = MemoryEntry(
                translation_key=key,
                source_text=source_text,
                target_text=target_text,
                kind=kind,
                hits=0,
            )

            # Fan out to all segments with this key
            for seg in key_to_segments[key]:
                provider_resolved[seg.id] = target_text
                stats.segments_from_provider += 1
                stats.segments_translated += 1

    # Write targets back into manifest
    all_resolved = {**memory_resolved, **provider_resolved}
    rewrite_targets(manifest_path, all_resolved)

    # Persist memory
    if memory_path is not None:
        save_memory(memory_path, memory)

    return stats
