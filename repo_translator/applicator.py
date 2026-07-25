"""APPLY stage: hash-guarded, descending-order byte splicing."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from repo_translator.manifest import Manifest, TranslationMemory
from repo_translator.segments import Segment


class ApplyError(Exception):
    """Raised on unrecoverable apply failures (drift, overlap, mismatch when strict)."""


@dataclass
class ApplyStats:
    files_processed: int = 0
    files_spliced: int = 0
    files_skipped_mismatch: int = 0
    files_skipped_validation: int = 0
    segments_applied: int = 0


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _has_overlap(segments: list[Segment]) -> bool:
    """Check if any two segments have overlapping [start_byte, end_byte) ranges."""
    sorted_segs = sorted(segments, key=lambda s: s.start_byte)
    for i in range(len(sorted_segs) - 1):
        if sorted_segs[i].end_byte > sorted_segs[i + 1].start_byte:
            return True
    return False


def _validate_translated_content(filepath: Path, content: str) -> None:
    """Reject translated structured files that no longer parse.

    Replicates RepoTranslator._validate_translated_content logic.
    """
    import json

    suffix = filepath.suffix.lower()
    if suffix == ".json":
        json.loads(content)
    elif suffix in {".yaml", ".yml"}:
        import yaml

        yaml.safe_load(content)
    elif suffix == ".toml":
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
            return
        tomllib.loads(content)
    elif suffix == ".py":
        compile(content, str(filepath), "exec")


def _write_text_atomic(filepath: Path, content: str) -> None:
    """Replace a text file atomically (temp-file + os.replace)."""
    fd, tmp = tempfile.mkstemp(
        dir=filepath.parent,
        prefix=f".{filepath.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


_STRUCTURED_EXTS = {".json", ".yaml", ".yml", ".toml", ".py"}


def apply_manifest(
    manifest_path: Path,
    source_root: Path,
    output_root: Path,
    *,
    memory: TranslationMemory | None = None,
    fail_on_source_mismatch: bool = True,
) -> ApplyStats:
    """APPLY stage. Copy source → output, then splice translations per-file.

    Guards:
      - Reject output aliasing source.
      - Reject non-empty existing output root.
      - Per-file sha256 hash guard.
      - Per-segment source-text drift guard.
      - Overlapping ranges rejected.
    Segments spliced in descending start_byte order. Structured files validated
    before atomic commit. All-or-nothing per file.
    """
    # --- Preconditions ---
    if output_root.resolve() == source_root.resolve():
        raise ApplyError("output_root must not alias source_root")

    if output_root.exists() and any(output_root.iterdir()):
        raise ApplyError(f"output_root is not empty: {output_root}")

    # --- Copy source → output ---
    shutil.copytree(source_root, output_root, symlinks=True)

    # --- Read manifest ---
    _header, segments = Manifest.read(manifest_path)

    by_path: dict[str, list[Segment]] = defaultdict(list)
    for seg in segments:
        by_path[seg.path].append(seg)

    stats = ApplyStats()

    for rel_path, segs in by_path.items():
        stats.files_processed += 1
        target_file = output_root / rel_path
        file_bytes = target_file.read_bytes()

        # --- Hash guard ---
        current_hash = _sha256(file_bytes)
        expected_hash = segs[0].file_sha256
        if current_hash != expected_hash:
            if fail_on_source_mismatch:
                raise ApplyError(
                    f"{rel_path} changed since extract "
                    f"(have {current_hash[:12]}, manifest {expected_hash[:12]})"
                )
            stats.files_skipped_mismatch += 1
            continue

        # --- Overlap check ---
        if _has_overlap(segs):
            raise ApplyError(f"{rel_path}: overlapping segment ranges")

        # --- Splice in descending start_byte order (all-or-nothing) ---
        buffer = bytearray(file_bytes)
        applied_count = 0

        for seg in sorted(segs, key=lambda s: s.start_byte, reverse=True):
            if seg.target_text is None:
                continue

            # Source-text drift guard
            actual_bytes = bytes(buffer[seg.start_byte : seg.end_byte])
            try:
                actual_text = actual_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise ApplyError(
                    f"{rel_path}@{seg.start_byte}: cannot decode source bytes as UTF-8"
                )

            if actual_text != seg.source_text:
                raise ApplyError(
                    f"{rel_path}@{seg.start_byte}: source_text drift; refusing to splice"
                )

            buffer[seg.start_byte : seg.end_byte] = seg.target_text.encode("utf-8")
            applied_count += 1

        # --- Structured-file validation ---
        new_text = bytes(buffer).decode("utf-8")
        if target_file.suffix.lower() in _STRUCTURED_EXTS:
            try:
                _validate_translated_content(target_file, new_text)
            except Exception:
                # Validation failed: leave copied-through original, record failure
                stats.files_skipped_validation += 1
                continue

        # --- Atomic write ---
        _write_text_atomic(target_file, new_text)
        stats.files_spliced += 1
        stats.segments_applied += applied_count

    return stats
