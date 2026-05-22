"""Repository equivalence verification after translation."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import yaml

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
}

SYNTAX_CHECK_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".py", ".xml", ".svg", ".ui"}
AI_REVIEW_EXTENSIONS = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".svg",
    ".po",
    ".pot",
}

SEVERITY_RANK = {"info": 1, "warning": 2, "error": 3}


@dataclass
class EquivalenceIssue:
    """A deterministic or AI issue found while comparing source and target repos."""

    file: str
    issue_type: str
    severity: str
    message: str
    source: str = ""
    target: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "file": self.file,
            "type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
            "target": self.target,
        }


@dataclass
class EquivalenceReport:
    """Verification summary for source and translated repositories."""

    source_dir: str
    target_dir: str
    source_files: int = 0
    target_files: int = 0
    common_files: int = 0
    files_checked: int = 0
    ai_files_reviewed: int = 0
    issues: List[EquivalenceIssue] = field(default_factory=list)

    def add_issue(self, issue: EquivalenceIssue):
        self.issues.append(issue)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def worst_severity(self) -> str:
        if not self.issues:
            return "none"
        return max(self.issues, key=lambda i: SEVERITY_RANK.get(i.severity, 0)).severity

    def should_fail(self, fail_on: str) -> bool:
        if fail_on == "never":
            return False
        threshold = SEVERITY_RANK[fail_on]
        return any(SEVERITY_RANK.get(issue.severity, 0) >= threshold for issue in self.issues)

    def to_dict(self) -> Dict:
        return {
            "source_dir": self.source_dir,
            "target_dir": self.target_dir,
            "files": {
                "source": self.source_files,
                "target": self.target_files,
                "common": self.common_files,
                "checked": self.files_checked,
                "ai_reviewed": self.ai_files_reviewed,
            },
            "summary": {
                "issues": len(self.issues),
                "has_errors": self.has_errors,
                "worst_severity": self.worst_severity,
            },
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def summary(self) -> str:
        lines = [
            "🔎 Equivalence Verification Report",
            f"   Source files: {self.source_files}",
            f"   Target files: {self.target_files}",
            f"   Common files: {self.common_files}",
            f"   Files checked: {self.files_checked}",
            f"   AI files reviewed: {self.ai_files_reviewed}",
            f"   Issues found: {len(self.issues)}",
        ]
        if not self.issues:
            lines.append("   ✅ Source and target look equivalent by configured checks.")
            return "\n".join(lines)

        by_type: Dict[str, List[EquivalenceIssue]] = {}
        for issue in self.issues:
            by_type.setdefault(issue.issue_type, []).append(issue)

        for issue_type, issues in by_type.items():
            lines.append(f"   [{issue_type}] {len(issues)} issues")
            for issue in issues[:3]:
                lines.append(f"      • {issue.file}: {issue.message[:100]}")
            if len(issues) > 3:
                lines.append(f"      ... and {len(issues) - 3} more")

        return "\n".join(lines)


def verify_equivalence(
    source_dir: Path,
    target_dir: Path,
    ai_check: bool = False,
    ai_engine: str = "vercel-ai-gateway",
    ai_api_key: Optional[str] = None,
    ai_model: str = "openai/gpt-4o-mini",
    ai_base_url: Optional[str] = None,
    source_lang: str = "zh",
    target_lang: str = "en",
    sample_rate: float = 0.15,
    max_ai_files: int = 20,
) -> EquivalenceReport:
    """Compare a source repo and translated repo for technical equivalence."""

    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    report = EquivalenceReport(str(source_dir), str(target_dir))

    source_files = _file_manifest(source_dir)
    target_files = _file_manifest(target_dir)
    report.source_files = len(source_files)
    report.target_files = len(target_files)

    source_paths = set(source_files)
    target_paths = set(target_files)
    common_paths = sorted(source_paths & target_paths)
    report.common_files = len(common_paths)

    for rel_path in sorted(source_paths - target_paths):
        report.add_issue(
            EquivalenceIssue(
                file=rel_path,
                issue_type="missing_file",
                severity="error",
                message="File exists in source repo but is missing from translated repo.",
            )
        )

    for rel_path in sorted(target_paths - source_paths):
        report.add_issue(
            EquivalenceIssue(
                file=rel_path,
                issue_type="extra_file",
                severity="warning",
                message="File exists in translated repo but not in source repo.",
            )
        )

    for rel_path in common_paths:
        source_path = source_files[rel_path]
        target_path = target_files[rel_path]
        report.files_checked += 1
        _check_pair(source_path, target_path, rel_path, report)

    if ai_check:
        _run_ai_equivalence_review(
            report=report,
            source_dir=source_dir,
            target_dir=target_dir,
            common_paths=common_paths,
            ai_engine=ai_engine,
            ai_api_key=ai_api_key,
            ai_model=ai_model,
            ai_base_url=ai_base_url,
            source_lang=source_lang,
            target_lang=target_lang,
            sample_rate=sample_rate,
            max_ai_files=max_ai_files,
        )

    return report


def _file_manifest(root: Path) -> Dict[str, Path]:
    if not root.exists():
        return {}

    files: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        rel_path = path.relative_to(root).as_posix()
        files[rel_path] = path
    return files


def _check_pair(source_path: Path, target_path: Path, rel_path: str, report: EquivalenceReport):
    if source_path.is_symlink() or target_path.is_symlink():
        if not (source_path.is_symlink() and target_path.is_symlink()):
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type="symlink_changed",
                    severity="error",
                    message="Path changed between symlink and regular file during translation.",
                )
            )
            return
        if source_path.readlink() != target_path.readlink():
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type="symlink_changed",
                    severity="error",
                    message="Symlink target changed during translation.",
                    source=str(source_path.readlink()),
                    target=str(target_path.readlink()),
                )
            )
        return

    source_is_binary = _is_binary(source_path)
    target_is_binary = _is_binary(target_path)

    if source_is_binary or target_is_binary:
        if _sha256(source_path) != _sha256(target_path):
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type="binary_changed",
                    severity="error",
                    message="Binary file checksum changed during translation.",
                )
            )
        return

    source_text = source_path.read_text(encoding="utf-8", errors="ignore")
    target_text = target_path.read_text(encoding="utf-8", errors="ignore")

    _check_syntax(source_path, target_path, rel_path, report)
    _check_invariants(source_text, target_text, rel_path, source_path.suffix.lower(), report)


def _check_syntax(source_path: Path, target_path: Path, rel_path: str, report: EquivalenceReport):
    suffix = target_path.suffix.lower()
    if suffix not in SYNTAX_CHECK_EXTENSIONS:
        return

    parser = _parser_for_suffix(suffix)
    if parser is None:
        return

    try:
        parser(source_path)
    except Exception:
        return

    try:
        parser(target_path)
    except Exception as exc:
        report.add_issue(
            EquivalenceIssue(
                file=rel_path,
                issue_type="syntax_error",
                severity="error",
                message=f"Translated file is no longer valid {suffix} syntax: {exc}",
            )
        )


def _parser_for_suffix(suffix: str) -> Optional[Callable[[Path], object]]:
    if suffix == ".json":
        return lambda path: json.loads(path.read_text(encoding="utf-8"))
    if suffix in {".yaml", ".yml"}:
        return lambda path: yaml.safe_load(path.read_text(encoding="utf-8"))
    if suffix == ".toml" and tomllib is not None:
        toml_parser = tomllib

        def parse_toml(path: Path):
            return toml_parser.loads(path.read_text(encoding="utf-8"))

        return parse_toml
    if suffix == ".py":
        return lambda path: compile(path.read_text(encoding="utf-8"), str(path), "exec")
    if suffix in {".xml", ".svg", ".ui"}:
        return lambda path: ET.parse(path)
    return None


def _check_invariants(
    source_text: str,
    target_text: str,
    rel_path: str,
    suffix: str,
    report: EquivalenceReport,
):
    invariant_patterns = [
        ("url_changed", "URL set changed", r"https?://[^\s)>'\"]+"),
        (
            "email_changed",
            "Email address set changed",
            r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        ),
        (
            "placeholder_changed",
            "Placeholder token set changed",
            r"\{\{[^{}]+\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}|%\([^)]+\)[sd]|%[sd]|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*",
        ),
    ]

    for issue_type, message, pattern in invariant_patterns:
        source_tokens = Counter(re.findall(pattern, source_text))
        target_tokens = Counter(re.findall(pattern, target_text))
        if source_tokens != target_tokens:
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type=issue_type,
                    severity="error",
                    message=message,
                    source=str(sorted(source_tokens.elements())[:10]),
                    target=str(sorted(target_tokens.elements())[:10]),
                )
            )

    if suffix in {".md", ".markdown", ".rst"}:
        source_blocks = _markdown_code_blocks(source_text)
        target_blocks = _markdown_code_blocks(target_text)
        if source_blocks != target_blocks:
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type="code_block_changed",
                    severity="error",
                    message="Fenced code block content changed during translation.",
                )
            )

        source_inline = Counter(re.findall(r"`([^`\n]+)`", source_text))
        target_inline = Counter(re.findall(r"`([^`\n]+)`", target_text))
        if source_inline != target_inline:
            report.add_issue(
                EquivalenceIssue(
                    file=rel_path,
                    issue_type="inline_code_changed",
                    severity="error",
                    message="Inline code token set changed during translation.",
                    source=str(sorted(source_inline.elements())[:10]),
                    target=str(sorted(target_inline.elements())[:10]),
                )
            )


def _markdown_code_blocks(text: str) -> List[str]:
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)


def _run_ai_equivalence_review(
    report: EquivalenceReport,
    source_dir: Path,
    target_dir: Path,
    common_paths: Iterable[str],
    ai_engine: str,
    ai_api_key: Optional[str],
    ai_model: str,
    ai_base_url: Optional[str],
    source_lang: str,
    target_lang: str,
    sample_rate: float,
    max_ai_files: int,
):
    from .reviewers import AIReviewer

    candidates = [
        target_dir / rel_path
        for rel_path in common_paths
        if (target_dir / rel_path).suffix.lower() in AI_REVIEW_EXTENSIONS
        and not _is_binary(target_dir / rel_path)
    ]
    selected = _select_ai_files(candidates, sample_rate, max_ai_files)
    report.ai_files_reviewed = len(selected)
    if not selected:
        return

    reviewer = AIReviewer(
        source_lang=source_lang,
        target_lang=target_lang,
        engine=ai_engine,
        api_key=ai_api_key,
        model=ai_model,
        base_url=ai_base_url,
        sample_rate=sample_rate,
        max_files=max_ai_files,
    )
    ai_report = reviewer.review(target_dir, source_dir, files=selected)
    for issue in ai_report.issues:
        report.add_issue(
            EquivalenceIssue(
                file=issue.file,
                issue_type=f"ai_{issue.issue_type}",
                severity=issue.severity,
                message=issue.suggestion,
                source=issue.original,
                target=issue.translated,
            )
        )


def _select_ai_files(files: List[Path], sample_rate: float, max_files: int) -> List[Path]:
    if not files:
        return []
    if sample_rate <= 0:
        return []
    sample_size = min(max(1, int(len(files) * sample_rate)), max_files, len(files))
    return sorted(files)[:sample_size]


def _is_binary(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if b"\x00" in data[:4096]:
        return True
    try:
        data.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
