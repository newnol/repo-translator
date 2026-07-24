"""File classification and filtering."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

# Formats that can be translated without parsing source-code syntax.
DOCUMENT_EXTENSIONS = {".md", ".markdown", ".rst", ".txt"}

# File extensions that contain translatable text
TRANSLATABLE_EXTENSIONS = {
    # Documentation
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".adoc",
    # Source code
    ".py",
    ".rs",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".lua",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".r",
    ".m",
    ".mm",
    ".pl",
    ".pm",
    # Web / templates
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",
    ".jinja",
    ".jinja2",
    ".j2",
    ".ejs",
    ".hbs",
    # Config / data (some have translatable strings)
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".env",
    # Other
    ".xml",
    ".svg",
    ".proto",
    ".graphql",
    ".gql",
    # Qt / UI
    ".ui",
    ".qml",
    # Localization
    ".po",
    ".pot",
}

# Extensions to NEVER translate (binary / compiled)
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".avif",
    ".mp3",
    ".mp4",
    ".wav",
    ".ogg",
    ".flac",
    ".aac",
    ".avi",
    ".mov",
    ".mkv",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".a",
    ".lib",
    ".o",
    ".obj",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".sqlite",
    ".db",
    ".bin",
    ".dat",
    ".pyc",
    ".pyo",
    ".class",
    ".jar",
    ".war",
    ".mo",
}

# Directories to skip entirely
SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "target",
    "build",
    "dist",
    ".next",
    ".nuxt",
    "vendor",
    "venv",
    ".venv",
    "env",
    ".env",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
    ".nyc_output",
    ".gradle",
    ".maven",
}

# Minified file patterns
MINIFIED_PATTERNS = ["*.min.js", "*.min.css", "*.bundle.js", "*.bundle.css"]

# Lock files
LOCK_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "poetry.lock",
    "Pipfile.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
    "bun.lockb",
}


def _matches_any(relative_path: Path, patterns: list[str] | None) -> bool:
    """Match a repository-relative path against user supplied glob patterns."""
    if not patterns:
        return False
    value = relative_path.as_posix()
    for pattern in patterns:
        candidates = [pattern]
        reduced = pattern
        while "**/" in reduced:
            reduced = reduced.replace("**/", "", 1)
            candidates.append(reduced)
        if any(fnmatch(value, candidate) for candidate in candidates):
            return True
    return False


def should_translate(
    filepath: Path,
    root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    translate_code: bool = True,
) -> bool:
    """
    Determine if a file should be translated.
    Returns True if file contains translatable content.
    """
    rel = filepath.relative_to(root)
    parts = rel.parts

    # Skip directories
    if any(part in SKIP_DIRS for part in parts):
        return False

    # Skip lock files
    if filepath.name in LOCK_FILES:
        return False

    # Skip binary
    if filepath.suffix.lower() in BINARY_EXTENSIONS:
        return False

    # Skip minified
    for pattern in MINIFIED_PATTERNS:
        if filepath.match(pattern):
            return False

    # Skip hidden files (except common config)
    if filepath.name.startswith(".") and filepath.name not in {
        ".env.example",
        ".gitignore",
        ".dockerignore",
        ".editorconfig",
        ".prettierrc",
        ".eslintrc",
    }:
        return False

    # Must be translatable extension
    if filepath.suffix.lower() not in TRANSLATABLE_EXTENSIONS:
        return False

    # Documentation-only is the safe default used by RepoTranslator. Direct callers
    # retain the historical all-formats behavior unless they opt out of code.
    if not translate_code and filepath.suffix.lower() not in DOCUMENT_EXTENSIONS:
        return False

    if include_patterns and not _matches_any(rel, include_patterns):
        return False

    if _matches_any(rel, exclude_patterns):
        return False

    # Skip very large files (>500KB of text is unusual)
    try:
        if filepath.stat().st_size > 500_000:
            return False
    except OSError:
        return False

    return True


def get_translatable_files(
    root: Path,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    translate_code: bool = True,
) -> list[Path]:
    """Get all files that should be translated in a directory."""
    files = []
    for filepath in root.rglob("*"):
        if filepath.is_file() and should_translate(
            filepath,
            root,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            translate_code=translate_code,
        ):
            files.append(filepath)
    return sorted(files)
