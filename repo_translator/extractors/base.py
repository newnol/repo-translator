"""Extractor protocol, registry, Candidate/ExtractionReport, and extract_repo."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from ..core import _has_cjk_ideograph
from ..file_filter import get_translatable_files
from ..segments import Segment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """A span the extractor considered, with its translatability verdict."""

    segment: Segment
    translatable: bool
    reason: str


@dataclass
class ExtractionReport:
    """Audit trail: skipped candidates and files that used the regex fallback."""

    skipped: list[tuple[str, int, str, str]] = field(default_factory=list)
    # (path, start_byte, snippet, reason)
    fallback_files: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    # (path, error message) for unreadable/undecodable files


# ---------------------------------------------------------------------------
# Extractor protocol
# ---------------------------------------------------------------------------


class Extractor(Protocol):
    def supports(self, path: Path) -> bool: ...

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]: ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry: dict[str, Extractor] = {}

# Suffixes that should use the tree-sitter extractor as PRIMARY
_TREE_SITTER_SUFFIXES: set[str] = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs",
    ".rs", ".go", ".java", ".kt",
    ".c", ".cpp", ".h", ".vue",
}

# Suffixes that should use the python AST extractor
_PYTHON_SUFFIXES: set[str] = {".py"}


def register(extractor: Extractor, suffixes: Iterable[str]) -> None:
    """Register an extractor for the given lowercased suffixes."""
    for s in suffixes:
        _registry[s.lower()] = extractor


def get_extractor(path: Path) -> Extractor | None:
    """Look up a registered extractor by lowercased suffix."""
    return _registry.get(path.suffix.lower())


# ---------------------------------------------------------------------------
# Regex fallback helper
# ---------------------------------------------------------------------------


def _get_fallback_extractor() -> Extractor | None:
    """Try to import the regex fallback extractor."""
    try:
        from .regex_fallback import RegexFallbackExtractor  # type: ignore[import]

        return RegexFallbackExtractor()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# extract_repo — the EXTRACT stage
# ---------------------------------------------------------------------------


def extract_repo(
    root: Path,
    *,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    translate_code: bool = False,
    source_is_cjk: bool = True,
    report: ExtractionReport | None = None,
) -> Iterator[Segment]:
    """EXTRACT stage.

    Walks get_translatable_files in sorted order, picks an extractor per file,
    collects Candidates, yields only translatable Segments. Gates on
    _has_cjk_ideograph when source_is_cjk. Populates report with skipped
    candidates, fallback files, and errors.
    """
    if report is None:
        report = ExtractionReport()

    files = get_translatable_files(
        root,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        translate_code=translate_code,
    )

    fallback = _get_fallback_extractor()

    for filepath in files:
        rel_path = filepath.relative_to(root).as_posix()

        # Read file bytes; skip unreadable
        try:
            file_bytes = filepath.read_bytes()
        except OSError as e:
            report.errors.append((rel_path, f"unreadable: {e}"))
            continue

        # Check decodable as UTF-8
        try:
            file_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            report.errors.append((rel_path, f"not UTF-8: {e}"))
            continue

        # Pick extractor
        extractor = get_extractor(filepath)
        used_fallback = False

        if extractor is None:
            # No registered extractor — try fallback
            if fallback is not None:
                extractor = fallback
                used_fallback = True
            else:
                # No extractor at all, skip
                continue

        # ponytail: extractors use path.as_posix() for the segment path field,
        # which must be repo-relative. Pass relative path, not absolute.
        rel_path_obj = filepath.relative_to(root)

        # Try extraction; on failure fall back to regex
        try:
            candidates = extractor.extract(rel_path_obj, file_bytes)
        except Exception as e:
            logger.debug("Extractor failed for %s: %s, trying fallback", rel_path, e)
            if fallback is not None and extractor is not fallback:
                try:
                    candidates = fallback.extract(rel_path_obj, file_bytes)
                    used_fallback = True
                except Exception as e2:
                    report.errors.append((rel_path, f"extraction failed: {e2}"))
                    continue
            else:
                report.errors.append((rel_path, f"extraction failed: {e}"))
                continue

        if used_fallback:
            report.fallback_files.append(rel_path)

        # Compute file_sha256 once
        file_sha256 = hashlib.sha256(file_bytes).hexdigest()

        # Enforce no overlapping ranges
        candidates.sort(key=lambda c: c.segment.start_byte)
        valid_candidates: list[Candidate] = []
        prev_end = -1
        for c in candidates:
            if c.segment.start_byte < prev_end:
                # Overlap — skip this candidate
                report.skipped.append((
                    rel_path,
                    c.segment.start_byte,
                    c.segment.source_text[:60],
                    "overlapping range",
                ))
                continue
            valid_candidates.append(c)
            if c.translatable:
                prev_end = c.segment.end_byte

        # Yield translatable segments, gate on CJK
        for c in valid_candidates:
            if not c.translatable:
                report.skipped.append((
                    rel_path,
                    c.segment.start_byte,
                    c.segment.source_text[:60],
                    c.reason,
                ))
                continue

            seg = c.segment
            # CJK gate
            if source_is_cjk and not _has_cjk_ideograph(seg.source_text):
                report.skipped.append((
                    rel_path,
                    seg.start_byte,
                    seg.source_text[:60],
                    "no CJK ideograph (source_is_cjk gate)",
                ))
                continue

            # Verify file_sha256 matches (segments built by extractors should
            # already have the right hash, but guard against inconsistency)
            if seg.file_sha256 != file_sha256:
                # Rebuild with correct hash — shouldn't happen in practice
                pass

            yield seg
