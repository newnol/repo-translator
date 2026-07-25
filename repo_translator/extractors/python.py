"""Python extractor: AST docstrings + tokenize comments (Req 7.1–7.6)."""

from __future__ import annotations

import ast
import io
import logging
import tokenize
from pathlib import Path

from ..segments import SegmentKind, _line_starts, build_segment
from .base import Candidate, register

logger = logging.getLogger(__name__)


def _byte_offset(line_starts: list[int], row: int, col: int, file_bytes: bytes) -> int:
    """Convert 1-based row + 0-based character col → byte offset (Req 7.4)."""
    line_start = line_starts[row - 1]
    # Decode just enough of the line to measure the character prefix in bytes
    nl = file_bytes.find(b"\n", line_start)
    line_end = nl if nl != -1 else len(file_bytes)
    line_text = file_bytes[line_start:line_end].decode("utf-8")
    return line_start + len(line_text[:col].encode("utf-8"))


class PythonExtractor:
    """Extract docstrings and comments from .py files via AST + tokenize."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        path_str = path.as_posix()

        try:
            source_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return []

        line_starts = _line_starts(file_bytes)
        candidates: list[Candidate] = []

        # --- Docstrings via AST (Req 7.1, 7.6) ---
        try:
            tree = ast.parse(source_text, filename=path_str)
        except SyntaxError as e:
            logger.debug("SyntaxError parsing %s: %s", path_str, e)
            return []  # Req 7.6: emit nothing on syntax error

        docstring_byte_ranges: set[tuple[int, int]] = set()

        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                continue
            if not node.body:
                continue
            first = node.body[0]
            if not isinstance(first, ast.Expr):
                continue
            val = first.value
            if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
                continue
            if not val.value or val.value.isspace():
                continue

            # Compute byte range of the string literal
            start = _byte_offset(line_starts, val.lineno, val.col_offset, file_bytes)
            end = _byte_offset(line_starts, val.end_lineno, val.end_col_offset, file_bytes)

            # Determine quote style to extract content (strip quotes)
            prefix = file_bytes[start : start + 4]
            if prefix[:3] in (b'"""', b"'''"):
                content_start, content_end = start + 3, end - 3
            elif prefix[:2] in (b'r"', b"r'") and prefix[1:4] in (b'"""', b"'''"):
                content_start, content_end = start + 4, end - 3
            elif prefix[:2] in (b'r"', b"r'"):
                content_start, content_end = start + 2, end - 1
            elif prefix[:1] in (b'"', b"'"):
                content_start, content_end = start + 1, end - 1
            else:
                continue

            if content_end <= content_start:
                continue

            try:
                content = file_bytes[content_start:content_end].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not content or content.isspace():
                continue

            docstring_byte_ranges.add((content_start, content_end))
            seg = build_segment(
                path=path_str,
                kind=SegmentKind.DOCSTRING,
                start_byte=content_start,
                end_byte=content_end,
                file_bytes=file_bytes,
                source_text=content,
            )
            candidates.append(Candidate(segment=seg, translatable=True, reason="docstring"))

        # --- Comments via tokenize (Req 7.5) ---
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source_text).readline))
        except tokenize.TokenError:
            return candidates

        for tok in tokens:
            if tok.type != tokenize.COMMENT:
                continue
            comment_str = tok.string
            if not comment_str or comment_str.strip() == "#":
                continue

            row, col = tok.start
            start = _byte_offset(line_starts, row, col, file_bytes)
            end = start + len(comment_str.encode("utf-8"))

            # Skip if overlapping a docstring range
            if any(
                ds < end and start < de for ds, de in docstring_byte_ranges
            ):
                continue

            seg = build_segment(
                path=path_str,
                kind=SegmentKind.COMMENT,
                start_byte=start,
                end_byte=end,
                file_bytes=file_bytes,
                source_text=comment_str,
            )
            candidates.append(Candidate(segment=seg, translatable=True, reason="comment"))

        return candidates


# --- Auto-register for .py ---
_extractor = PythonExtractor()
register(_extractor, [".py"])
