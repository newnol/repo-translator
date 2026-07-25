"""Markdown extractor — headings, paragraphs, table cells, link labels."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..core import PROTECTED_TOKEN_PATTERN
from ..segments import SegmentKind, build_segment
from .base import Candidate, register

logger = logging.getLogger(__name__)

_LINK_LABEL_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://[^\s)>'\"`]+")


def _is_translatable_text(text: str) -> bool:
    """Return True if text has non-whitespace content after stripping protected tokens."""
    cleaned = PROTECTED_TOKEN_PATTERN.sub("", text)
    cleaned = _INLINE_CODE_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("", cleaned)
    return bool(cleaned.strip())


class MarkdownExtractor:
    """Emit Segments for translatable Markdown prose."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in (".md", ".markdown")

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            logger.warning("Markdown decode failed for %s: %s", path, e)
            return []

        # ponytail: extractor receives absolute path but build_segment rejects it.
        # Use filename only; extract_repo's ExtractionReport uses its own rel_path.
        seg_path = path.name

        candidates: list[Candidate] = []
        lines = text.splitlines(keepends=True)
        fence_marker: str | None = None

        for line_idx, line in enumerate(lines):
            stripped = line.lstrip()

            # --- fenced code block toggle ---
            marker_match = re.match(r"(`{3,}|~{3,})", stripped)
            if marker_match:
                marker_char = marker_match.group(1)[0]
                if fence_marker is None:
                    fence_marker = marker_char
                elif fence_marker == marker_char:
                    fence_marker = None
                continue

            if fence_marker:
                continue

            line_body = line.rstrip("\r\n")

            # --- table row ---
            if "|" in line_body and line_body.strip().startswith("|"):
                if re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", line_body):
                    continue
                self._extract_table_cells(line_idx, file_bytes, seg_path, candidates)
                continue

            # --- heading or paragraph line ---
            prefix_match = re.match(
                r"^(?P<prefix>\s*(?:#{1,6}\s+|>\s+|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)?)",
                line_body,
            )
            prefix = prefix_match.group("prefix") if prefix_match else ""
            body = line_body[len(prefix):]

            if not body.strip():
                continue

            kind = (
                SegmentKind.MARKDOWN_HEADING
                if re.match(r"\s*#{1,6}\s+", prefix)
                else SegmentKind.MARKDOWN_PARAGRAPH
            )

            # Extract link labels from this line
            self._extract_link_labels(line, line_idx, file_bytes, seg_path, candidates)

            # Strip link syntax [text](url) and URLs before deciding if there's
            # remaining translatable prose worth emitting as a paragraph/heading.
            body_no_links = _LINK_LABEL_RE.sub("", body)
            body_no_links = _URL_RE.sub("", body_no_links)
            if not _is_translatable_text(body_no_links):
                continue

            # Emit the body as a segment
            self._emit_run(body, kind, line_idx, prefix, file_bytes, seg_path, candidates)

        return candidates

    def _emit_run(
        self,
        body: str,
        kind: str,
        line_idx: int,
        prefix: str,
        file_bytes: bytes,
        seg_path: str,
        candidates: list[Candidate],
    ) -> None:
        """Locate body in file_bytes and emit a Candidate."""
        body_bytes = body.encode("utf-8")
        line_byte_offset = _line_byte_offset(file_bytes, line_idx)
        if line_byte_offset is None:
            return

        prefix_byte_len = len(prefix.encode("utf-8"))
        start_byte = line_byte_offset + prefix_byte_len
        end_byte = start_byte + len(body_bytes)

        # Round-trip check
        if end_byte <= len(file_bytes) and file_bytes[start_byte:end_byte] == body_bytes:
            pass  # fast path
        else:
            # Fallback: search within the line
            line_end = file_bytes.find(b"\n", line_byte_offset)
            if line_end == -1:
                line_end = len(file_bytes)
            idx = file_bytes.find(body_bytes, line_byte_offset, line_end)
            if idx == -1:
                logger.debug("Cannot locate run in bytes at line %d", line_idx + 1)
                return
            start_byte = idx
            end_byte = start_byte + len(body_bytes)

        try:
            seg = build_segment(
                path=seg_path,
                kind=kind,
                start_byte=start_byte,
                end_byte=end_byte,
                file_bytes=file_bytes,
                source_text=body,
            )
            candidates.append(Candidate(segment=seg, translatable=True, reason=kind))
        except ValueError as e:
            logger.debug("build_segment failed: %s", e)

    def _extract_table_cells(
        self,
        line_idx: int,
        file_bytes: bytes,
        seg_path: str,
        candidates: list[Candidate],
    ) -> None:
        """Split table row into cells and emit translatable ones."""
        line_byte_offset = _line_byte_offset(file_bytes, line_idx)
        if line_byte_offset is None:
            return

        line_end = file_bytes.find(b"\n", line_byte_offset)
        if line_end == -1:
            line_end = len(file_bytes)
        line_bytes = file_bytes[line_byte_offset:line_end]
        line_str = line_bytes.decode("utf-8")

        cells = line_str.split("|")
        cursor = 0  # byte position within line_bytes
        for cell in cells:
            cell_bytes = cell.encode("utf-8")
            cell_stripped = cell.strip()
            if not cell_stripped or not _is_translatable_text(cell_stripped):
                cursor += len(cell_bytes) + 1  # +1 for |
                continue

            stripped_bytes = cell_stripped.encode("utf-8")
            idx = line_bytes.find(stripped_bytes, cursor)
            if idx == -1:
                cursor += len(cell_bytes) + 1
                continue

            start_byte = line_byte_offset + idx
            end_byte = start_byte + len(stripped_bytes)

            try:
                seg = build_segment(
                    path=seg_path,
                    kind=SegmentKind.MARKDOWN_TABLE_CELL,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    file_bytes=file_bytes,
                    source_text=cell_stripped,
                )
                candidates.append(
                    Candidate(segment=seg, translatable=True, reason="table_cell")
                )
            except ValueError as e:
                logger.debug("build_segment failed for table cell: %s", e)

            cursor += len(cell_bytes) + 1

    def _extract_link_labels(
        self,
        line: str,
        line_idx: int,
        file_bytes: bytes,
        seg_path: str,
        candidates: list[Candidate],
    ) -> None:
        """Extract [label](url) — emit label as MARKDOWN_LINK_LABEL."""
        line_byte_offset = _line_byte_offset(file_bytes, line_idx)
        if line_byte_offset is None:
            return

        for m in _LINK_LABEL_RE.finditer(line):
            label = m.group(1)
            if not label.strip() or not _is_translatable_text(label):
                continue

            label_bytes = label.encode("utf-8")
            prefix_str = line[: m.start(1)]
            prefix_byte_len = len(prefix_str.encode("utf-8"))
            start_byte = line_byte_offset + prefix_byte_len
            end_byte = start_byte + len(label_bytes)

            if end_byte > len(file_bytes) or file_bytes[start_byte:end_byte] != label_bytes:
                # Fallback search
                line_end = file_bytes.find(b"\n", line_byte_offset)
                if line_end == -1:
                    line_end = len(file_bytes)
                idx = file_bytes.find(label_bytes, line_byte_offset, line_end)
                if idx == -1:
                    continue
                start_byte = idx
                end_byte = start_byte + len(label_bytes)

            if file_bytes[start_byte:end_byte] != label_bytes:
                continue

            try:
                seg = build_segment(
                    path=seg_path,
                    kind=SegmentKind.MARKDOWN_LINK_LABEL,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    file_bytes=file_bytes,
                    source_text=label,
                )
                candidates.append(
                    Candidate(segment=seg, translatable=True, reason="link_label")
                )
            except ValueError as e:
                logger.debug("build_segment failed for link label: %s", e)


def _line_byte_offset(file_bytes: bytes, line_idx: int) -> int | None:
    """Return byte offset of the start of line `line_idx` (0-based)."""
    offset = 0
    for _ in range(line_idx):
        nl = file_bytes.find(b"\n", offset)
        if nl == -1:
            return None
        offset = nl + 1
    return offset


# --- Register for .md and .markdown suffixes ---
_extractor = MarkdownExtractor()
register(_extractor, [".md", ".markdown"])
