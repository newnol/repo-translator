"""Regex fallback extractor — line-based CJK detection wrapping historical logic."""

# ponytail: inherits the historical template-literal $ limitation

from __future__ import annotations

from pathlib import Path

from ..core import _has_cjk_ideograph
from ..segments import SegmentKind, build_segment
from .base import Candidate


class RegexFallbackExtractor:
    """Fallback extractor: emits each CJK-containing line as a translatable segment."""

    def supports(self, path: Path) -> bool:  # noqa: ARG002
        return True

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        text = file_bytes.decode("utf-8")
        path_str = path.as_posix()
        candidates: list[Candidate] = []
        offset = 0

        for line in text.split("\n"):
            line_bytes = line.encode("utf-8")
            stripped = line.strip()
            if stripped and _has_cjk_ideograph(stripped):
                # Use the stripped content but locate it within the line
                strip_start = line.index(stripped)
                start_byte = offset + len(line[:strip_start].encode("utf-8"))
                end_byte = start_byte + len(stripped.encode("utf-8"))
                seg = build_segment(
                    path=path_str,
                    kind=SegmentKind.STRING,
                    start_byte=start_byte,
                    end_byte=end_byte,
                    file_bytes=file_bytes,
                    source_text=stripped,
                )
                candidates.append(Candidate(segment=seg, translatable=True, reason="regex_fallback"))
            # +1 for the \n separator (except possibly the last line, but offset
            # arithmetic still works since we won't index past file_bytes)
            offset += len(line_bytes) + 1

        return candidates
