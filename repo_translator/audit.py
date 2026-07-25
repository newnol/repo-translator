"""AUDIT stage – flag residual Han ideographs in translated output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .core import _has_cjk_ideograph
from .file_filter import get_translatable_files


@dataclass
class AuditFinding:
    path: str  # repo-relative POSIX path
    line: int  # 1-based
    snippet: str  # ≤200 chars


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)
    unaudited_files: list[str] = field(default_factory=list)
    files_with_residual: int = 0
    total_findings: int = 0


def audit_repo(
    root: Path,
    *,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> AuditReport:
    """Walk translatable files and flag any line still containing Han ideographs."""
    files = get_translatable_files(root, include_patterns=include_patterns, exclude_patterns=exclude_patterns)
    report = AuditReport()
    files_flagged: set[str] = set()

    for filepath in files:
        rel = filepath.relative_to(root).as_posix()
        try:
            text = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            report.unaudited_files.append(rel)
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            if _has_cjk_ideograph(line):
                snippet = line.strip()[:200]
                report.findings.append(AuditFinding(path=rel, line=lineno, snippet=snippet))
                files_flagged.add(rel)

    report.files_with_residual = len(files_flagged)
    report.total_findings = len(report.findings)
    return report
