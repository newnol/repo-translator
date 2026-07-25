"""Segment data model, SegmentKind, ManifestHeader, and ID/key helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Optional


class SegmentKind:
    """Enumerated category of a Segment (string constants)."""

    COMMENT = "comment"
    DOCSTRING = "docstring"
    STRING = "string"
    TEMPLATE_STRING = "template_string"
    TEMPLATE_STRING_FRAGMENT = "template_string_fragment"
    JSX_TEXT = "jsx_text"
    UI_ATTRIBUTE = "ui_attribute"
    JSON_VALUE = "json_value"
    YAML_VALUE = "yaml_value"
    TOML_VALUE = "toml_value"
    MARKDOWN_HEADING = "markdown_heading"
    MARKDOWN_PARAGRAPH = "markdown_paragraph"
    MARKDOWN_TABLE_CELL = "markdown_table_cell"
    MARKDOWN_LINK_LABEL = "markdown_link_label"
    HTML_TEXT = "html_text"
    LINE_COMMENT = "line_comment"
    BLOCK_COMMENT = "block_comment"
    DOC_COMMENT = "doc_comment"

    _ALL: set[str] | None = None

    @classmethod
    def all_kinds(cls) -> set[str]:
        if cls._ALL is None:
            cls._ALL = {
                v
                for k, v in vars(cls).items()
                if not k.startswith("_") and isinstance(v, str) and k == k.upper()
            }
        return cls._ALL


@dataclass
class ManifestHeader:
    """First line of the translation-manifest.jsonl file."""

    type: str = "header"
    version: int = 1
    source_lang: str = "zh"
    target_lang: str = "en"
    repo_root: str = ""
    created_at: str = ""
    segment_count: int = 0

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> ManifestHeader:
        return cls(**json.loads(line))


@dataclass
class Segment:
    """One translatable span located precisely in one source file."""

    id: str
    path: str
    kind: str
    start_byte: int
    end_byte: int
    line: int
    column: int
    source_text: str
    target_text: Optional[str]
    file_sha256: str
    source_sha256: str
    translation_key: str
    context_before: str = ""
    context_after: str = ""
    protected_context: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Byte-range validation (Req 1.2, 1.3)
        if self.start_byte < 0 or self.end_byte <= self.start_byte:
            raise ValueError(
                f"Invalid byte range: start_byte={self.start_byte}, end_byte={self.end_byte}"
            )
        # Path validation (Req 1.5, 1.6)
        if not self.path or self.path.startswith("/") or self.path.startswith("\\"):
            raise ValueError(f"Path must be repo-relative, not absolute: {self.path!r}")
        if ".." in self.path.split("/"):
            raise ValueError(f"Path must not contain '..' traversal: {self.path!r}")
        # Kind validation (Req 1.7, 1.12)
        if self.kind not in SegmentKind.all_kinds():
            raise ValueError(f"Invalid kind: {self.kind!r}")
        # source_sha256 validation (Req 1.4)
        expected_sha = hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()
        if self.source_sha256 != expected_sha:
            raise ValueError(
                f"source_sha256 mismatch: expected {expected_sha}, got {self.source_sha256}"
            )

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> Segment:
        return cls(**json.loads(line))

    @property
    def is_translated(self) -> bool:
        return self.target_text is not None


def _line_starts(file_bytes: bytes) -> list[int]:
    """Return byte offsets where each line begins (index 0 = line 1)."""
    starts = [0]
    for i, b in enumerate(file_bytes):
        if b == ord("\n"):
            starts.append(i + 1)
    return starts


def build_segment(
    *,
    path: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    file_bytes: bytes,
    source_text: str,
    context_chars: int = 40,
    context_sensitive: bool = False,
    protected_context: list[str] | None = None,
) -> Segment:
    """Assemble a fully-validated Segment from a located span.

    Computes line/column from file_bytes[:start_byte], source_sha256, file_sha256,
    context_before/after, translation_key, and id. Asserts
    file_bytes[start_byte:end_byte].decode('utf-8') == source_text.
    """
    # --- range validation ---
    if start_byte < 0 or end_byte < start_byte or end_byte > len(file_bytes):
        raise ValueError(
            f"Invalid byte range: start_byte={start_byte}, end_byte={end_byte}, "
            f"file_len={len(file_bytes)}"
        )

    # --- byte round-trip enforcement ---
    actual = file_bytes[start_byte:end_byte].decode("utf-8")
    if actual != source_text:
        raise ValueError(
            f"Round-trip mismatch at [{start_byte}:{end_byte}]: "
            f"decoded {actual!r} != source_text {source_text!r}"
        )

    # --- line / column via line-start table ---
    starts = _line_starts(file_bytes)
    # Binary search for the line containing start_byte
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= start_byte:
            lo = mid
        else:
            hi = mid - 1
    line = lo + 1  # 1-based
    line_start = starts[lo]
    # Column = number of decoded chars between line_start and start_byte
    column = len(file_bytes[line_start:start_byte].decode("utf-8"))

    # --- context_before / context_after (char-clamped) ---
    before_bytes = file_bytes[:start_byte]
    context_before = before_bytes.decode("utf-8")[-context_chars:] if before_bytes else ""

    after_bytes = file_bytes[end_byte:]
    context_after = after_bytes.decode("utf-8")[:context_chars] if after_bytes else ""

    # --- hashes ---
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    # --- translation key ---
    ctx = context_before if context_sensitive else ""
    translation_key = make_translation_key(source_text, kind, context=ctx)

    # --- id ---
    seg_id = make_segment_id(path, start_byte, end_byte)

    return Segment(
        id=seg_id,
        path=path,
        kind=kind,
        start_byte=start_byte,
        end_byte=end_byte,
        line=line,
        column=column,
        source_text=source_text,
        target_text=None,
        file_sha256=file_sha256,
        source_sha256=source_sha256,
        translation_key=translation_key,
        context_before=context_before,
        context_after=context_after,
        protected_context=protected_context or [],
    )


def make_translation_key(source_text: str, kind: str, *, context: str = "") -> str:
    """Context-aware reuse key: sha256(source_text \x1f kind [\x1f context])."""
    if not source_text or source_text.isspace():
        raise ValueError("source_text must not be empty or whitespace-only")
    parts = [source_text, kind]
    if context:
        parts.append(context)
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def make_segment_id(path: str, start_byte: int, end_byte: int) -> str:
    """Stable position-based id: first 16 hex chars of sha256(path \x1f start \x1f end)."""
    if start_byte < 0 or end_byte <= start_byte:
        raise ValueError(
            f"Invalid byte range: start_byte={start_byte}, end_byte={end_byte}"
        )
    raw = f"{path}\x1f{start_byte}\x1f{end_byte}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
