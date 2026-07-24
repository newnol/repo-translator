"""Core translation pipeline."""

import os
import re
import json
import shutil
import logging
from pathlib import Path
from typing import Any, Dict, List
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from .detector import has_cjk
from .file_filter import get_translatable_files
from .translators import get_translator
from .reviewers import AIReviewer, ReviewReport

logger = logging.getLogger(__name__)
console = Console()

PROTECTED_TOKEN_PATTERN = re.compile(
    r"```[\s\S]*?```"  # fenced markdown code blocks
    r"|`[^`\n]+`"  # inline markdown code
    r"|\|\|\|SEP\|\|\|"  # internal batch separator
    r"|https?://[^\s)>'\"]+"  # URLs
    r"|\{\{[^{}]+\}\}"  # template placeholders
    r"|\$\{[A-Za-z_][A-Za-z0-9_]*\}"  # ${VAR}
    r"|\$[A-Za-z_][A-Za-z0-9_]*(?:_[A-Za-z0-9]+)*"  # $x_t / $API_KEY
    r"|%\([^)]+\)[sd]|%[sd]"  # printf placeholders
    r"|\{[A-Za-z_][A-Za-z0-9_]*\}"  # {name}
)

CJK_IDEOGRAPH_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

STATE_FILE = ".repo-translator-state.json"


def _has_cjk_ideograph(text: str) -> bool:
    """Return True only for real CJK word characters, not punctuation/forms."""
    return bool(CJK_IDEOGRAPH_PATTERN.search(text))


@dataclass
class TranslationStats:
    """Statistics for a translation run."""

    total_files: int = 0
    translated_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    total_chars: int = 0
    translated_chars: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = None

    @property
    def duration(self) -> float:
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    def summary(self) -> str:
        return (
            f"📊 Translation Stats:\n"
            f"   Total files:      {self.total_files}\n"
            f"   Translated:       {self.translated_files}\n"
            f"   Skipped:          {self.skipped_files}\n"
            f"   Failed:           {self.failed_files}\n"
            f"   Total chars:      {self.total_chars:,}\n"
            f"   Translated chars: {self.translated_chars:,}\n"
            f"   Duration:         {self.duration:.1f}s"
        )


class RepoTranslator:
    """
    Main translator class. Orchestrates the full pipeline:
    clone → detect → translate → review → push
    """

    def __init__(
        self,
        source_lang: str = "zh",
        target_lang: str = "en",
        translator_engine: str = "google",
        translator_api_key: str = None,
        translator_model: str = None,
        translator_base_url: str = None,
        review_engine: str = None,
        review_api_key: str = None,
        review_model: str = "gpt-4o-mini",
        review_base_url: str = None,
        review_sample_rate: float = 0.15,
        verify: bool = False,
        verify_ai: bool = False,
        verify_engine: str = "vercel-ai-gateway",
        verify_api_key: str = None,
        verify_model: str = "openai/gpt-4o-mini",
        verify_base_url: str = None,
        verify_sample_rate: float = 0.15,
        verify_max_ai_files: int = 20,
        verify_fail_on: str = "error",
        verify_json_output: str = None,
        include_patterns: List[str] = None,
        exclude_patterns: List[str] = None,
        batch_size: int = 40,
        max_workers: int = 1,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.batch_size = batch_size
        self.dry_run = dry_run
        self.max_workers = max_workers
        self.verbose = verbose
        self.verify = verify
        self.verify_ai = verify_ai
        self.verify_engine = verify_engine
        self.verify_api_key = verify_api_key
        self.verify_model = verify_model
        self.verify_base_url = verify_base_url
        self.verify_sample_rate = verify_sample_rate
        self.verify_max_ai_files = verify_max_ai_files
        self.verify_fail_on = verify_fail_on
        self.verify_json_output = verify_json_output

        # Translation engine
        self.translator = get_translator(
            engine=translator_engine,
            source_lang=source_lang,
            target_lang=target_lang,
            api_key=translator_api_key,
            model=translator_model,
            base_url=translator_base_url,
        )

        # AI Reviewer (optional)
        self.reviewer = None
        if review_engine:
            self.reviewer = AIReviewer(
                source_lang=source_lang,
                target_lang=target_lang,
                engine=review_engine,
                api_key=review_api_key,
                model=review_model,
                base_url=review_base_url,
                sample_rate=review_sample_rate,
            )

        self.stats = TranslationStats()

    def run(
        self,
        repo_url: str = None,
        repo_dir: str = None,
        output_dir: str = None,
        push_to: str = None,
        github_token: str = None,
    ) -> Dict:
        """
        Run the full translation pipeline.

        Args:
            repo_url: GitHub repo URL to clone
            repo_dir: Local repo directory (alternative to repo_url)
            output_dir: Output directory for translated files
            push_to: GitHub repo to push to (e.g., 'user/repo')
            github_token: GitHub token for pushing
        """
        result = {
            "success": False,
            "stats": None,
            "review": None,
            "verification": None,
            "push_url": None,
        }

        try:
            # Step 1: Get source repo
            if repo_url:
                dest_path = Path(output_dir) if output_dir else None
                if dest_path and (dest_path / STATE_FILE).exists():
                    logger.info(f"Resuming: found existing state in {dest_path}")
                    source_dir = dest_path
                else:
                    source_dir = self.clone(repo_url, output_dir or "/tmp/repo-translate-source")
            elif repo_dir:
                source_dir = Path(repo_dir)
            else:
                raise ValueError("Either repo_url or repo_dir is required")

            # Step 2: Translate
            if output_dir and repo_url:
                # Clone + translate to new dir
                dest_dir = Path(output_dir)
                self.translate(source_dir, dest_dir)
            elif output_dir:
                # Copy and translate in place
                dest_dir = Path(output_dir)
                if dest_dir != source_dir:
                    if dest_dir.exists():
                        state_file = dest_dir / STATE_FILE
                        if state_file.exists():
                            logger.info(f"Resuming: keeping existing output directory {dest_dir}")
                        else:
                            shutil.rmtree(dest_dir)
                            shutil.copytree(source_dir, dest_dir, symlinks=True)
                    else:
                        shutil.copytree(source_dir, dest_dir, symlinks=True)
                self.translate_in_place(dest_dir)
            else:
                dest_dir = source_dir
                self.translate_in_place(dest_dir)

            # Step 3: AI Review (optional)
            if self.reviewer:
                report = self.reviewer.review(dest_dir, source_dir)
                result["review"] = report
                console.print(report.summary())

            # Step 4: Equivalence verification (optional, before push)
            if self.verify:
                from .equivalence import verify_equivalence

                report = verify_equivalence(
                    source_dir=source_dir,
                    target_dir=dest_dir,
                    ai_check=self.verify_ai,
                    ai_engine=self.verify_engine,
                    ai_api_key=self.verify_api_key,
                    ai_model=self.verify_model,
                    ai_base_url=self.verify_base_url,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    sample_rate=self.verify_sample_rate,
                    max_ai_files=self.verify_max_ai_files,
                )
                result["verification"] = report
                console.print(report.summary())

                if self.verify_json_output:
                    Path(self.verify_json_output).write_text(
                        json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )

                if report.should_fail(self.verify_fail_on):
                    raise ValueError(
                        f"Verification failed with {len(report.issues)} issue(s); "
                        f"worst severity: {report.worst_severity}"
                    )

            # Step 5: Push to GitHub (optional)
            if push_to:
                push_url = self.push(dest_dir, push_to, github_token)
                result["push_url"] = push_url

            result["success"] = True
            result["stats"] = self.stats

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            if self.verbose:
                import traceback

                traceback.print_exc()
            result["error"] = str(e)

        return result

    def clone(self, repo_url: str, output_dir: str) -> Path:
        """Clone a git repository."""
        import git

        output_path = Path(output_dir)

        if output_path.exists():
            logger.info(f"Removing existing directory: {output_path}")
            shutil.rmtree(output_path)

        console.print(f"📥 Cloning [cyan]{repo_url}[/cyan]...")
        git.Repo.clone_from(repo_url, str(output_path), depth=1)
        console.print(f"   ✅ Cloned to {output_path}")

        return output_path

    def translate(self, source_dir: Path, dest_dir: Path) -> TranslationStats:
        """Translate files from source to destination directory."""
        # Copy source to dest first
        if source_dir != dest_dir:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(source_dir, dest_dir, symlinks=True)

        return self.translate_in_place(dest_dir)

    def _load_state(self, directory: Path) -> set:
        """Load set of already-translated file paths (relative) from state file."""
        state_path = directory / STATE_FILE
        if not state_path.exists():
            return set()
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return set(data.get("translated", []))
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt state file, starting fresh")
            return set()

    def _save_state(self, directory: Path, translated: set):
        """Save translated file paths to state file."""
        state_path = directory / STATE_FILE
        data = {"translated": sorted(translated)}
        state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def translate_in_place(self, directory: Path) -> TranslationStats:
        """Translate files in-place."""
        self.stats = TranslationStats()
        files = get_translatable_files(directory)
        self.stats.total_files = len(files)

        console.print(
            f"\n🌐 Translating [cyan]{len(files)}[/cyan] files "
            f"({self.source_lang} → {self.target_lang})"
        )
        console.print(f"   Engine: [green]{self.translator.name}[/green]")

        # Load resume state
        translated_set = self._load_state(directory)
        if translated_set:
            console.print(f"   Resuming: [yellow]{len(translated_set)}[/yellow] files already translated")

        # Filter to only files that need translation
        to_translate = []
        for filepath in files:
            try:
                rel = str(filepath.relative_to(directory))
                if rel in translated_set:
                    self.stats.skipped_files += 1
                    continue
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                if (
                    has_cjk(content)
                    if self.source_lang in ("zh", "ja", "ko")
                    else len(content.strip()) > 20
                ):
                    to_translate.append(filepath)
                else:
                    self.stats.skipped_files += 1
            except Exception:
                self.stats.skipped_files += 1

        console.print(f"   Files needing translation: [yellow]{len(to_translate)}[/yellow]")

        if not to_translate:
            console.print("   ⚠️  No files need translation!")
            # Save state even if 0 to mark completion
            self._save_state(directory, translated_set)
            return self.stats

        # Translate files
        total = len(to_translate)
        if self.max_workers > 1 and total > 1:
            console.print(f"   Workers: {self.max_workers}")
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading
            lock = threading.Lock()

            def _process(filepath):
                rel_path = str(filepath.relative_to(directory))
                try:
                    result = self._translate_one_file(filepath, directory)
                except Exception as e:
                    result = {"ok": False, "rel": rel_path, "error": str(e)}
                with lock:
                    translated_set.add(rel_path)
                    self._save_state(directory, translated_set)
                    done = len(translated_set)
                    if done % 10 == 0 or done == total:
                        console.print(f"   [{done}/{total}] Translated: {rel_path}")
                return result

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                list(pool.map(_process, to_translate))

            # Recalculate stats from state
            self.stats.translated_files = len(translated_set)
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("({task.completed}/{task.total})"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Translating...", total=total)
                for filepath in to_translate:
                    self._translate_one_file(filepath, directory, progress, task, translated_set)

        self.stats.end_time = datetime.now()

        # Print summary
        console.print(f"\n{self.stats.summary()}")
        return self.stats

    def _translate_one_file(self, filepath: Path, root: Path, progress=None, task=None, translated_set: set = None) -> dict:
        """Translate a single file. Returns dict with translation stats.

        Thread-safe: does not access shared state when progress/task/translated_set are None.
        """
        rel_path = str(filepath.relative_to(root))
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")

            from .detector import extract_translatable_text
            translatable = extract_translatable_text(content, filepath.suffix)

            if not translatable or len(translatable.strip()) < 5:
                if progress:
                    self.stats.skipped_files += 1
                    progress.advance(task)
                return {"ok": True, "rel": rel_path, "action": "skip"}

            if self.dry_run:
                logger.info(f"[DRY RUN] Would translate: {rel_path}")
                if progress:
                    progress.advance(task)
                return {"ok": True, "rel": rel_path, "action": "dry-run"}

            if filepath.suffix in (".md", ".markdown", ".rst", ".txt"):
                translated = self._translate_markdown(content)
                filepath.write_text(translated, encoding="utf-8")
            elif filepath.suffix == ".json":
                translated = self._translate_json_content(content)
                filepath.write_text(translated, encoding="utf-8")
            else:
                translated_lines = self._translate_source_lines(content.split("\n"))
                filepath.write_text("\n".join(translated_lines), encoding="utf-8")

            if progress:
                self.stats.translated_files += 1
                progress.advance(task)

            if translated_set is not None:
                translated_set.add(rel_path)

            return {"ok": True, "rel": rel_path, "action": "translated"}

        except Exception as e:
            logger.warning(f"Failed to translate {rel_path}: {e}")
            if progress:
                self.stats.failed_files += 1
                progress.advance(task)
            return {"ok": False, "rel": rel_path, "error": str(e)}

    def _translate_batch(
        self,
        files: List[Path],
        root: Path,
        progress: Progress,
        task,
        translated_set: set = None,
    ):
        """Translate a batch of files (sequential, for backward compatibility)."""
        if translated_set is None:
            translated_set = set()
        for filepath in files:
            self._translate_one_file(filepath, root, progress, task, translated_set)
            self._save_state(root, translated_set)

    def _translate_source_lines(self, lines: List[str]) -> List[str]:
        """
        Translate source code line-by-line.
        Only translates lines containing CJK characters (comments, strings, docstrings).
        Preserves code structure and indentation.
        """
        translated_lines = []
        batch_to_translate = []
        batch_indices = []
        batch_indents = []

        # Collect lines that need translation, preserving indentation
        for i, line in enumerate(lines):
            if has_cjk(line) and _has_cjk_ideograph(line) and len(line.strip()) > 2:
                batch_to_translate.append(line.strip())
                batch_indices.append(i)
                # Save original indentation
                indent = len(line) - len(line.lstrip())
                batch_indents.append(indent)
            translated_lines.append(line)

        if not batch_to_translate:
            return translated_lines

        # Chunk at 20 lines to keep request size manageable for self-hosted engines
        MAX_LINES_PER_BATCH = 20
        for chunk_start in range(0, len(batch_to_translate), MAX_LINES_PER_BATCH):
            chunk = batch_to_translate[chunk_start : chunk_start + MAX_LINES_PER_BATCH]
            chunk_idx = batch_indices[chunk_start : chunk_start + MAX_LINES_PER_BATCH]
            chunk_indent = batch_indents[chunk_start : chunk_start + MAX_LINES_PER_BATCH]
            joined = "\n|||SEP|||\n".join(chunk)
            try:
                translated = self._translate_preserving_tokens(joined)
                parts = translated.split("|||SEP|||")
                for idx, part, indent in zip(chunk_idx, parts, chunk_indent):
                    cleaned = part.strip()
                    if cleaned:
                        translated_lines[idx] = " " * indent + cleaned
            except Exception as e:
                logger.warning(f"Line translation failed: {e}")

        return translated_lines

    def _translate_markdown(self, content: str) -> str:
        """Translate markdown prose without touching code, URLs, or placeholders.
        Batches all translatable lines into a single API call for speed.
        """
        lines = content.splitlines(keepends=True)
        in_fence = False
        is_cjk_source = getattr(self, "source_lang", "zh") in ("zh", "ja", "ko")

        # Collect lines to translate; leave placeholders for non-translatable ones
        batch_indices = []   # positions in result list
        batch_bodies = []    # line bodies (without trailing newline)
        result = []          # placeholder: None for lines to translate, str for kept-as-is

        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("```"):
                result.append(line)
                in_fence = not in_fence
                continue

            if in_fence:
                result.append(line)
                continue

            # CJK source: skip non-CJK lines (they're likely already in target language)
            if is_cjk_source and not _has_cjk_ideograph(line):
                result.append(line)
                continue

            line_body = line[:-1] if line.endswith("\n") else line
            # Don't translate yet — batch it
            batch_indices.append(len(result))
            batch_bodies.append(line_body)
            result.append(None)  # placeholder, filled after batch translate

        if not batch_indices:
            return content

        # Chunk at 20 lines to keep request size manageable
        MAX_LINES_PER_BATCH = 20
        for chunk_start in range(0, len(batch_indices), MAX_LINES_PER_BATCH):
            chunk_idx = batch_indices[chunk_start : chunk_start + MAX_LINES_PER_BATCH]
            chunk_bodies = batch_bodies[chunk_start : chunk_start + MAX_LINES_PER_BATCH]
            joined = "\n|||SEP|||\n".join(chunk_bodies)
            translated_joined = self._translate_preserving_tokens(joined)
            parts = translated_joined.split("|||SEP|||")
            for idx, part in zip(chunk_idx, parts):
                cleaned = part.strip()
                result[idx] = (cleaned if cleaned else "") + "\n"

        return "".join(result)

    def _translate_json_content(self, content: str) -> str:
        """Translate JSON string values while preserving keys and valid JSON syntax."""
        data = json.loads(content)

        def translate_value(value: Any) -> Any:
            if isinstance(value, str):
                if has_cjk(value):
                    return self._translate_preserving_tokens(value)
                return value
            if isinstance(value, list):
                return [translate_value(item) for item in value]
            if isinstance(value, dict):
                return {key: translate_value(item) for key, item in value.items()}
            return value

        translated = translate_value(data)
        indent = 2 if "\n" in content else None
        return json.dumps(translated, ensure_ascii=False, indent=indent)

    def _translate_preserving_tokens(self, text: str) -> str:
        """Translate unprotected spans and reassemble protected technical tokens unchanged."""
        if not text.strip():
            return text

        pieces = []
        last = 0
        for match in PROTECTED_TOKEN_PATTERN.finditer(text):
            if match.start() > last:
                pieces.append(self._translate_text_span(text[last : match.start()]))
            pieces.append(match.group(0))
            last = match.end()

        if last < len(text):
            pieces.append(self._translate_text_span(text[last:]))

        return "".join(pieces)

    def _translate_text_span(self, text: str) -> str:
        """Translate a non-protected text span, keeping blank spans byte-for-byte."""
        if not text.strip():
            return text
        # Retry once on transient failures (timeout, connection error)
        try:
            return self.translator.translate_text(text)
        except Exception as e:
            logger.warning(f"Retry after error: {e}")
            return self.translator.translate_text(text)

    def _apply_translation(self, original: str, translated: str, suffix: str) -> str:
        """
        Apply translated text back into the original file structure.
        Smart merge: replace translatable content while preserving code structure.
        """
        # For markdown and text files: direct replacement
        if suffix in (".md", ".markdown", ".rst", ".txt"):
            return translated

        # For other files: line-by-line replacement
        orig_lines = original.split("\n")
        trans_lines = translated.split("\n")

        # If line counts match, do 1:1 replacement
        if len(orig_lines) == len(trans_lines):
            result = []
            for orig_line, trans_line in zip(orig_lines, trans_lines):
                if has_cjk(orig_line) and trans_line.strip():
                    result.append(trans_line)
                else:
                    result.append(orig_line)
            return "\n".join(result)

        # Fallback: return translated content
        return translated

    def review(self, directory: Path) -> ReviewReport:
        """Run AI review on translated files."""
        if not self.reviewer:
            console.print("⚠️  No reviewer configured. Skipping review.")
            return ReviewReport()

        console.print(f"\n🔍 Running AI review (sample rate: {self.reviewer.sample_rate:.0%})...")
        report = self.reviewer.review(directory)
        console.print(report.summary())
        return report

    def push(self, directory: Path, repo_name: str, token: str = None) -> str:
        """Push translated files to GitHub."""
        import git

        token = token or os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ValueError("GitHub token required for pushing. Set GITHUB_TOKEN env var.")

        console.print(f"\n📤 Pushing to [cyan]{repo_name}[/cyan]...")

        # Setup git
        repo = git.Repo(directory)

        # Configure remote with token
        remote_url = f"https://x-access-token:{token}@github.com/{repo_name}.git"

        # Remove existing remotes
        for remote in repo.remotes:
            repo.delete_remote(remote)

        origin = repo.create_remote("origin", remote_url)

        # Stage all
        repo.git.add(A=True)

        # Commit
        repo.index.commit(
            f"Translate {self.source_lang} → {self.target_lang} using repo-translator"
        )

        # Push
        origin.push(refspec="main:main", force=True)

        push_url = f"https://github.com/{repo_name}"
        console.print(f"   ✅ Pushed to {push_url}")
        return push_url
