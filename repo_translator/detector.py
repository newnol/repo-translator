"""Language detection utilities."""

import re
from pathlib import Path
from typing import Optional

from langdetect import detect, detect_langs, LangDetectException


# Unicode ranges for CJK detection
CJK_RANGES = [
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Extension A
    (0xF900, 0xFAFF),  # CJK Compatibility
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x3000, 0x303F),  # CJK Symbols
    (0xFF00, 0xFFEF),  # Fullwidth Forms
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0xAC00, 0xD7AF),  # Hangul
]

# Minimum ratio of target-language chars to consider "needs translation"
MIN_FOREIGN_RATIO = 0.05


def has_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    for char in text:
        code = ord(char)
        for start, end in CJK_RANGES:
            if start <= code <= end:
                return True
    return False


def count_cjk_chars(text: str) -> int:
    """Count CJK characters in text."""
    count = 0
    for char in text:
        code = ord(char)
        for start, end in CJK_RANGES:
            if start <= code <= end:
                count += 1
                break
    return count


def detect_language(text: str) -> Optional[str]:
    """
    Detect the primary language of text.
    Returns ISO 639-1 code (e.g., 'zh', 'ja', 'ko', 'en') or None.
    """
    if not text or len(text.strip()) < 10:
        return None

    try:
        # Quick CJK check first (more reliable for short strings)
        if has_cjk(text):
            cjk_count = count_cjk_chars(text)
            total_chars = len(text.strip())
            if total_chars > 0 and cjk_count / total_chars > 0.1:
                # Use langdetect to distinguish zh/ja/ko
                langs = detect_langs(text[:1000])
                for lang in langs:
                    if lang.lang in ("zh-cn", "zh-tw", "zh", "ja", "ko"):
                        return lang.lang.split("-")[0]  # normalize to 'zh', 'ja', 'ko'

        lang = detect(text[:1000])
        return lang

    except LangDetectException:
        return None


def detect_file_language(filepath: Path) -> Optional[str]:
    """
    Detect language of translatable content in a file.
    Extracts text content, skips code/comments markers, then detects.
    """
    try:
        text = filepath.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError):
        return None

    if not text.strip():
        return None

    # Extract translatable text based on file type
    extracted = extract_translatable_text(text, filepath.suffix)

    if not extracted or len(extracted.strip()) < 20:
        return None

    return detect_language(extracted)


def extract_translatable_text(content: str, suffix: str) -> str:
    """
    Extract human-readable text from source code / markup.
    Strips code logic, keeps comments, strings, and prose.
    """
    lines = []
    in_comment_block = False

    for line in content.split("\n"):
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            continue

        # Markdown: keep most content
        if suffix in (".md", ".markdown", ".rst"):
            lines.append(stripped)
            continue

        # HTML/XML: extract text between tags
        if suffix in (".html", ".htm", ".jinja2", ".vue", ".jsx", ".tsx", ".ui", ".qml", ".xml"):
            text = re.sub(r"<[^>]+>", " ", stripped)
            if text.strip():
                lines.append(text.strip())
            continue

        # JSON: extract string values
        if suffix == ".json":
            strings = re.findall(r'"([^"]+)"', stripped)
            for s in strings:
                if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.-]*$", s) and len(s) > 2:
                    lines.append(s)
            continue

        # Python / JS / Rust / Go / Java comments
        if stripped.startswith("#") and not stripped.startswith("#!"):
            lines.append(stripped.lstrip("# "))
        elif stripped.startswith("//"):
            lines.append(stripped.lstrip("/ "))
        elif stripped.startswith("/*"):
            in_comment_block = True
            lines.append(stripped.lstrip("/* "))
        elif in_comment_block:
            if "*/" in stripped:
                in_comment_block = False
                lines.append(stripped.rstrip("*/ "))
            else:
                lines.append(stripped.lstrip("* "))
        elif '"""' in stripped or "'''" in stripped:
            # Python docstring
            lines.append(re.sub(r'["\']{3}', "", stripped).strip())
        # String literals
        elif re.match(r"""^['"].*['"]""", stripped) and len(stripped) > 4:
            lines.append(stripped.strip("'\""))

    return "\n".join(lines)
