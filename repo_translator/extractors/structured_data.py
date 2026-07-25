"""Structured-data extractor — JSON, YAML, TOML string values."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from ..segments import SegmentKind, build_segment
from .base import Candidate, register

logger = logging.getLogger(__name__)

# ponytail: enum-like = no spaces, only letters/digits/underscores/hyphens/dots
_ENUM_LIKE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _should_skip(value: str) -> bool:
    """Return True if the string value should NOT be emitted."""
    if not value or value.isspace():
        return True
    if "://" in value:
        return True
    if "/" in value or "\\" in value:
        return True
    if _ENUM_LIKE_RE.match(value):
        return True
    return False


def _walk_values(obj, out: list[str]) -> None:
    """Recursively collect all string scalar values from a parsed structure."""
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _walk_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_values(v, out)


def _locate_value(value: str, file_bytes: bytes, search_start: int = 0) -> tuple[int, int] | None:
    """Find the value's bytes in the file, returning (start, end) or None.

    Searches for both quoted and unquoted occurrences.
    """
    val_bytes = value.encode("utf-8")

    # Try quoted variants first (JSON style, YAML quoted)
    for quote in (b'"', b"'"):
        needle = quote + val_bytes + quote
        idx = file_bytes.find(needle, search_start)
        if idx != -1:
            # Return the bytes of the value itself (inside the quotes)
            start = idx + 1
            end = start + len(val_bytes)
            if file_bytes[start:end] == val_bytes:
                return (start, end)

    # Unquoted (common in YAML) — find the raw bytes
    idx = file_bytes.find(val_bytes, search_start)
    if idx != -1:
        return (idx, idx + len(val_bytes))

    return None


class StructuredDataExtractor:
    """Emit Segments for non-empty string scalar values in JSON/YAML/TOML."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in (".json", ".yaml", ".yml", ".toml")

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return []

        suffix = path.suffix.lower()
        kind = {
            ".json": SegmentKind.JSON_VALUE,
            ".yaml": SegmentKind.YAML_VALUE,
            ".yml": SegmentKind.YAML_VALUE,
            ".toml": SegmentKind.TOML_VALUE,
        }[suffix]

        # Parse
        parsed = self._parse(suffix, text)
        if parsed is None:
            return []

        # Collect all string values
        values: list[str] = []
        _walk_values(parsed, values)

        seg_path = path.name
        candidates: list[Candidate] = []
        used_starts: set[int] = set()

        for value in values:
            if _should_skip(value):
                continue

            # Find location in raw bytes, skipping already-used positions
            search_start = 0
            loc = None
            while True:
                loc = _locate_value(value, file_bytes, search_start)
                if loc is None:
                    break
                if loc[0] not in used_starts:
                    break
                search_start = loc[1]
                loc = None

            if loc is None:
                continue

            start, end = loc

            # Confirm round-trip
            if file_bytes[start:end].decode("utf-8", errors="replace") != value:
                continue

            try:
                seg = build_segment(
                    path=seg_path,
                    kind=kind,
                    start_byte=start,
                    end_byte=end,
                    file_bytes=file_bytes,
                    source_text=value,
                )
                candidates.append(Candidate(segment=seg, translatable=True, reason=kind))
                used_starts.add(start)
            except ValueError as e:
                logger.debug("build_segment failed for structured value: %s", e)

        return candidates

    def _parse(self, suffix: str, text: str):
        """Parse structured content, returning the parsed object or None on error."""
        try:
            if suffix == ".json":
                return json.loads(text)
            elif suffix in (".yaml", ".yml"):
                import yaml
                return yaml.safe_load(text)
            elif suffix == ".toml":
                try:
                    import tomllib
                except ModuleNotFoundError:
                    return None
                return tomllib.loads(text)
        except Exception as e:
            logger.debug("Parse failed for %s file: %s", suffix, e)
            return None


_extractor = StructuredDataExtractor()
register(_extractor, [".json", ".yaml", ".yml", ".toml"])
