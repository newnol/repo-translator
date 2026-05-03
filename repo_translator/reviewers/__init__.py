"""AI-based translation quality review."""

import os
import re
import json
import time
import random
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ReviewIssue:
    """A single issue found during review."""
    file: str
    line: int
    issue_type: str  # 'untranslated', 'broken_format', 'wrong_term', 'other'
    severity: str     # 'error', 'warning', 'info'
    original: str
    translated: str
    suggestion: str


@dataclass
class ReviewReport:
    """Summary of AI review findings."""
    files_reviewed: int = 0
    total_issues: int = 0
    issues: List[ReviewIssue] = field(default_factory=list)
    score: float = 100.0  # 0-100 quality score

    def add_issue(self, issue: ReviewIssue):
        self.issues.append(issue)
        self.total_issues = len(self.issues)
        # Decrease score based on severity
        penalty = {'error': 5, 'warning': 2, 'info': 0.5}
        self.score = max(0, self.score - penalty.get(issue.severity, 1))

    def summary(self) -> str:
        lines = [
            f"📊 Review Report",
            f"   Files reviewed: {self.files_reviewed}",
            f"   Issues found: {self.total_issues}",
            f"   Quality score: {self.score:.1f}/100",
            f"",
        ]
        if self.issues:
            by_type = {}
            for issue in self.issues:
                by_type.setdefault(issue.issue_type, []).append(issue)

            for issue_type, issues in by_type.items():
                lines.append(f"   [{issue_type}] {len(issues)} issues")
                for issue in issues[:3]:  # show top 3
                    lines.append(f"      • {issue.file}:{issue.line} — {issue.suggestion[:80]}")
                if len(issues) > 3:
                    lines.append(f"      ... and {len(issues) - 3} more")
        else:
            lines.append("   ✅ No issues found!")

        return '\n'.join(lines)

    def to_dict(self) -> dict:
        return {
            'files_reviewed': self.files_reviewed,
            'total_issues': self.total_issues,
            'score': self.score,
            'issues': [
                {
                    'file': i.file,
                    'line': i.line,
                    'type': i.issue_type,
                    'severity': i.severity,
                    'original': i.original,
                    'translated': i.translated,
                    'suggestion': i.suggestion,
                }
                for i in self.issues
            ],
        }


class AIReviewer:
    """
    AI-powered translation quality reviewer.
    Reviews a sample of translated files and reports issues.
    """

    def __init__(
        self,
        source_lang: str = 'zh',
        engine: str = 'openai',
        api_key: str = None,
        model: str = 'gpt-4o-mini',
        base_url: str = None,
        sample_rate: float = 0.15,
        max_files: int = 50,
    ):
        self.source_lang = source_lang
        self.engine = engine
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY', '')
        self.model = model
        self.base_url = base_url or 'https://api.openai.com/v1'
        self.sample_rate = sample_rate
        self.max_files = max_files

    def review(
        self,
        translated_dir: Path,
        original_dir: Path = None,
        files: List[Path] = None,
    ) -> ReviewReport:
        """
        Review translated files for quality issues.

        Args:
            translated_dir: Directory with translated files
            original_dir: Directory with original files (optional, for comparison)
            files: Specific files to review (optional, auto-samples if not set)
        """
        report = ReviewReport()

        # Select files to review
        if files is None:
            files = self._select_sample(translated_dir)

        report.files_reviewed = len(files)
        logger.info(f"🔍 AI Review: checking {len(files)} files (sample rate: {self.sample_rate:.0%})")

        for filepath in files:
            try:
                content = filepath.read_text(encoding='utf-8', errors='ignore')

                # Load original if available
                original_content = None
                if original_dir:
                    orig_path = original_dir / filepath.relative_to(translated_dir)
                    if orig_path.exists():
                        original_content = orig_path.read_text(encoding='utf-8', errors='ignore')

                # Run checks
                issues = self._check_file(filepath, content, original_content, translated_dir)
                for issue in issues:
                    report.add_issue(issue)

            except Exception as e:
                logger.warning(f"Review failed for {filepath}: {e}")

        return report

    def _select_sample(self, directory: Path) -> List[Path]:
        """Select a random sample of files for review."""
        from ..file_filter import get_translatable_files

        all_files = get_translatable_files(directory)
        sample_size = min(
            max(1, int(len(all_files) * self.sample_rate)),
            self.max_files,
        )
        return random.sample(all_files, min(sample_size, len(all_files)))

    def _check_file(
        self,
        filepath: Path,
        translated: str,
        original: str = None,
        root: Path = None,
    ) -> List[ReviewIssue]:
        """Check a single file for translation issues."""
        issues = []
        rel_path = str(filepath.relative_to(root)) if root else str(filepath)

        # 1. Check for remaining untranslated CJK characters
        from .detector import count_cjk_chars
        for line_num, line in enumerate(translated.split('\n'), 1):
            cjk_count = count_cjk_chars(line)
            if cjk_count > 3:  # more than 3 CJK chars = likely untranslated
                issues.append(ReviewIssue(
                    file=rel_path,
                    line=line_num,
                    issue_type='untranslated',
                    severity='error',
                    original=line,
                    translated=line,
                    suggestion=f"Line contains {cjk_count} untranslated characters",
                ))

        # 2. Check for broken markdown/code blocks
        if filepath.suffix == '.md':
            orig_blocks = re.findall(r'```(\w+)?', translated)
            if len(orig_blocks) % 2 != 0:
                issues.append(ReviewIssue(
                    file=rel_path,
                    line=0,
                    issue_type='broken_format',
                    severity='warning',
                    original='',
                    translated='',
                    suggestion='Unclosed code block detected',
                ))

        # 3. AI-powered deep review (if API key available)
        if self.api_key and original:
            ai_issues = self._ai_deep_review(filepath, original, translated, rel_path)
            issues.extend(ai_issues)

        return issues

    def _ai_deep_review(
        self,
        filepath: Path,
        original: str,
        translated: str,
        rel_path: str,
    ) -> List[ReviewIssue]:
        """Use LLM for deep quality review of a translation."""
        import requests

        # Only review files with enough content
        if len(translated.strip()) < 50:
            return []

        # Truncate to avoid huge requests
        max_len = 3000
        orig_short = original[:max_len] + ('...' if len(original) > max_len else '')
        trans_short = translated[:max_len] + ('...' if len(translated) > max_len else '')

        prompt = f"""Review this translation from {self.source_lang} to English.

ORIGINAL ({self.source_lang}):
```
{orig_short}
```

TRANSLATED (English):
```
{trans_short}
```

Check for:
1. Untranslated {self.source_lang} text remaining
2. Broken markdown/formatting
3. Incorrect technical terms
4. Code/variable names that were incorrectly translated
5. Missing content or added content

Respond in JSON format:
{{"issues": [{{"line": <line_number>, "type": "<untranslated|broken_format|wrong_term|other>", "severity": "<error|warning|info>", "original": "<original text>", "translated": "<translated text>", "suggestion": "<fix suggestion>"}}], "overall_quality": <0-100>}}

If no issues: {{"issues": [], "overall_quality": <score>}}"""

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a translation quality reviewer. Respond only in JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1000,
                    "response_format": {"type": "json_object"},
                },
                timeout=30,
            )
            resp.raise_for_status()
            result = resp.json()
            content = result['choices'][0]['message']['content']
            data = json.loads(content)

            issues = []
            for item in data.get('issues', []):
                issues.append(ReviewIssue(
                    file=rel_path,
                    line=item.get('line', 0),
                    issue_type=item.get('type', 'other'),
                    severity=item.get('severity', 'info'),
                    original=item.get('original', ''),
                    translated=item.get('translated', ''),
                    suggestion=item.get('suggestion', ''),
                ))
            return issues

        except Exception as e:
            logger.warning(f"AI deep review failed for {rel_path}: {e}")
            return []

        finally:
            time.sleep(1)  # rate limit