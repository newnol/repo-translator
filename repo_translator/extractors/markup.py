"""HTML/XML/SVG extractor via xml.etree.ElementTree."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from ..segments import SegmentKind, build_segment
from .base import Candidate, register

logger = logging.getLogger(__name__)

_UI_ATTRS = {"title", "alt", "aria-label", "placeholder"}
_SKIP_TAGS = {"script", "style"}
# ponytail: SVG id-ref pattern covers url(#...) and bare # values
_SVG_ID_REF_RE = re.compile(r"(^#|url\(#)")


def _local_tag(tag: str) -> str:
    """Strip namespace prefix from ElementTree tag like {ns}local."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


class MarkupExtractor:
    """Extract translatable text/attributes from HTML/XML/SVG."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in (".html", ".htm", ".xml", ".svg")

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return []

        # ElementTree needs a single root; HTML may not have one.
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            # Wrap in synthetic root for fragment HTML
            try:
                root = ET.fromstring(f"<_root_>{text}</_root_>")
            except ET.ParseError as e:
                logger.debug("Markup parse failed for %s: %s", path, e)
                return []

        seg_path = path.name
        candidates: list[Candidate] = []
        search_start = 0

        self._walk(root, file_bytes, seg_path, candidates, search_start)
        return candidates

    def _walk(
        self,
        elem: ET.Element,
        file_bytes: bytes,
        seg_path: str,
        candidates: list[Candidate],
        search_start: int,
    ) -> int:
        """Recursively walk element tree, return updated search_start."""
        local = _local_tag(elem.tag)

        # Skip script/style entirely
        if local.lower() in _SKIP_TAGS:
            return search_start

        # --- element .text ---
        if elem.text and elem.text.strip():
            search_start = self._emit_text(
                elem.text, SegmentKind.HTML_TEXT, file_bytes, seg_path, candidates, search_start
            )

        # --- UI attributes ---
        for attr in _UI_ATTRS:
            val = elem.get(attr)
            if val and val.strip():
                if _SVG_ID_REF_RE.search(val):
                    continue
                search_start = self._emit_text(
                    val, SegmentKind.UI_ATTRIBUTE, file_bytes, seg_path, candidates, search_start
                )

        # --- SVG exclusions for xlink:href starting with # ---
        # (id attrs are simply not in _UI_ATTRS so already excluded)
        # xlink:href with # is also excluded; check explicitly
        for attr_key, attr_val in elem.attrib.items():
            attr_local = _local_tag(attr_key)
            if attr_local == "href" and attr_val.startswith("#"):
                continue  # already not in _UI_ATTRS, no-op

        # --- recurse children ---
        for child in elem:
            search_start = self._walk(child, file_bytes, seg_path, candidates, search_start)
            # --- child .tail ---
            if child.tail and child.tail.strip():
                search_start = self._emit_text(
                    child.tail, SegmentKind.HTML_TEXT, file_bytes, seg_path, candidates, search_start
                )

        return search_start

    def _emit_text(
        self,
        text: str,
        kind: str,
        file_bytes: bytes,
        seg_path: str,
        candidates: list[Candidate],
        search_start: int,
    ) -> int:
        """Locate text in file_bytes and emit a Candidate. Returns updated search_start."""
        text_bytes = text.encode("utf-8")
        idx = file_bytes.find(text_bytes, search_start)
        if idx == -1:
            # Try from beginning as fallback
            idx = file_bytes.find(text_bytes)
        if idx == -1:
            logger.debug("Cannot locate markup text %r in file bytes", text[:40])
            return search_start

        start_byte = idx
        end_byte = start_byte + len(text_bytes)

        try:
            seg = build_segment(
                path=seg_path,
                kind=kind,
                start_byte=start_byte,
                end_byte=end_byte,
                file_bytes=file_bytes,
                source_text=text,
            )
            candidates.append(Candidate(segment=seg, translatable=True, reason=kind))
        except ValueError as e:
            logger.debug("build_segment failed for markup text: %s", e)

        return end_byte


# --- Register ---
_extractor = MarkupExtractor()
register(_extractor, [".html", ".htm", ".xml", ".svg"])
