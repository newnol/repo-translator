"""Core translation pipeline."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from .detector import has_cjk
from .file_filter import get_translatable_files
from .reviewers import AIReviewer, ReviewReport
from .translators import get_translator

logger = logging.getLogger(__name__)
console = Console()

PROTECTED_TOKEN_PATTERN = re.compile(
    r"```[\s\S]*?```"  # fenced markdown code blocks
    r"|`[^`\n]+`"  # inline markdown code
    r"|</?[A-Za-z][^>]*>"  # HTML/JSX tags
    r"|\|\|\|SEP\|\|\|"  # internal batch separator
    r"|https?://[^\s)>'\"]+"  # URLs
    r"|\((?:[#./][^)\s]+|[^)\s]+\.[A-Za-z0-9]{1,8}(?:#[^)\s]+)?)\)"  # link targets
    r"|\\(?:[\\'\"abfnrtv]|x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8})"
    r"|\{\{[^{}]+\}\}"  # template placeholders
    r"|\$\{[A-Za-z_][A-Za-z0-9_]*\}"  # ${VAR}
    r"|\$[A-Za-z_][A-Za-z0-9_]*(?:_[A-Za-z0-9]+)*"  # $x_t / $API_KEY
    r"|%\([^)]+\)[sd]|%[sd]"  # printf placeholders
    r"|\{[A-Za-z_][A-Za-z0-9_]*\}"  # {name}
)

CJK_IDEOGRAPH_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
QUOTED_STRING_PATTERN = re.compile(
    r'"(?P<double>(?:\\.|[^"\\])*)"'
    r"|'(?P<single>(?:\\.|[^'\\])*)'"
    r'|`(?P<backtick>(?:\\.|[^`\\$])*)`'
)

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
        translator_api_key: str | None = None,
        translator_model: str | None = None,
        translator_base_url: str | None = None,
        review_engine: str | None = None,
        review_api_key: str | None = None,
        review_model: str = "gpt-4o-mini",
        review_base_url: str | None = None,
        review_sample_rate: float = 0.15,
        verify: bool = False,
        verify_ai: bool = False,
        verify_engine: str = "vercel-ai-gateway",
        verify_api_key: str | None = None,
        verify_model: str = "openai/gpt-4o-mini",
        verify_base_url: str | None = None,
        verify_sample_rate: float = 0.15,
        verify_max_ai_files: int = 20,
        verify_fail_on: str = "error",
        verify_json_output: str | None = None,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        batch_size: int = 40,
        max_workers: int = 1,
        translate_code: bool = False,
        code_scope: str = "comments-and-strings",
        dry_run: bool = False,
        verbose: bool = False,
        # Manifest pipeline hooks (additive, all default to no-op)
        export_manifest_path: str | None = None,
        apply_manifest_path: str | None = None,
        translation_memory_path: str | None = None,
        fail_on_source_mismatch: bool = True,
        audit_untranslated: bool = False,
    ):
        if code_scope not in {"comments", "comments-and-strings"}:
            raise ValueError("code_scope must be 'comments' or 'comments-and-strings'")
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.batch_size = batch_size
        self.include_patterns = include_patterns
        self.exclude_patterns = exclude_patterns
        self.translate_code = translate_code
        self.code_scope = code_scope
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

        # Manifest pipeline hooks
        self.export_manifest_path = export_manifest_path
        self.apply_manifest_path = apply_manifest_path
        self.translation_memory_path = translation_memory_path
        self.fail_on_source_mismatch = fail_on_source_mismatch
        self.audit_untranslated = audit_untranslated

        self.stats = TranslationStats()

    def run(
        self,
        repo_url: str | None = None,
        repo_dir: str | None = None,
        output_dir: str | None = None,
        push_to: str | None = None,
        github_token: str | None = None,
    ) -> dict:
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

        temporary_source: tempfile.TemporaryDirectory | None = None
        try:
            # Step 1: Get source repo
            if repo_url:
                temporary_source = tempfile.TemporaryDirectory(prefix="repo-translator-source-")
                source_dir = self.clone(repo_url, Path(temporary_source.name) / "source")
                if output_dir is None:
                    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
                    output_dir = str(Path.cwd() / f"{repo_name}-translated")
            elif repo_dir:
                source_dir = Path(repo_dir)
            else:
                raise ValueError("Either repo_url or repo_dir is required")

            if not output_dir and (self.reviewer or self.verify):
                raise ValueError(
                    "Review and verification require an output directory separate from source"
                )

            # Step 2: Translate (or apply manifest)
            if self.apply_manifest_path:
                # --- Apply mode: splice from manifest instead of live translation ---
                from .applicator import apply_manifest, ApplyError

                manifest_p = Path(self.apply_manifest_path)
                if not manifest_p.exists():
                    raise ValueError(
                        f"Apply manifest not found: {manifest_p}"
                    )
                try:
                    from .manifest import Manifest
                    Manifest.read(manifest_p)  # validate readability
                except Exception as e:
                    raise ValueError(
                        f"Apply manifest invalid: {manifest_p}: {e}"
                    ) from e

                if not output_dir:
                    raise ValueError(
                        "--apply-manifest requires an output directory"
                    )
                dest_dir = Path(output_dir)
                apply_manifest(
                    manifest_p,
                    source_dir,
                    dest_dir,
                    fail_on_source_mismatch=self.fail_on_source_mismatch,
                )
            elif output_dir and repo_url:
                dest_dir = Path(output_dir)
                self.translate(source_dir, dest_dir)
            elif output_dir:
                dest_dir = Path(output_dir)
                if dest_dir.resolve() == source_dir.resolve():
                    self.translate_in_place(dest_dir)
                else:
                    self.translate(source_dir, dest_dir)
            else:
                dest_dir = source_dir
                self.translate_in_place(dest_dir)

            # Export manifest (after translation completes)
            if self.export_manifest_path and not self.apply_manifest_path:
                try:
                    from .extractors.base import extract_repo
                    from .manifest import Manifest, ManifestHeader

                    manifest_out = Path(self.export_manifest_path)
                    header = ManifestHeader(
                        source_lang=self.source_lang,
                        target_lang=self.target_lang,
                        repo_root=str(dest_dir),
                    )
                    man = Manifest.open_for_write(manifest_out, header)
                    for seg in extract_repo(
                        dest_dir,
                        include_patterns=self.include_patterns,
                        exclude_patterns=self.exclude_patterns,
                        translate_code=self.translate_code,
                        source_is_cjk=(self.source_lang in ("zh", "ja", "ko")),
                    ):
                        man.append(seg)
                    man.finalize()
                except Exception as e:
                    logger.error(f"Failed to export manifest: {e}")
                    # Don't corrupt translated output — just log the error

            # Audit untranslated (after translate/apply)
            if self.audit_untranslated:
                from .audit import audit_repo

                audit_report = audit_repo(
                    dest_dir,
                    include_patterns=self.include_patterns,
                    exclude_patterns=self.exclude_patterns,
                )
                result["audit"] = audit_report
                if audit_report.total_findings:
                    console.print(
                        f"   ⚠️  {audit_report.total_findings} residual CJK "
                        f"finding(s) in {audit_report.files_with_residual} file(s)"
                    )

            # Step 3: AI Review (optional)
            if self.reviewer:
                self._assert_distinct_directories(source_dir, dest_dir)
                report = self.reviewer.review(dest_dir, source_dir)
                result["review"] = report
                console.print(report.summary())

            # Step 4: Equivalence verification (optional, before push)
            if self.verify:
                from .equivalence import verify_equivalence

                self._assert_distinct_directories(source_dir, dest_dir)
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
        finally:
            if temporary_source is not None:
                temporary_source.cleanup()

        return result

    @staticmethod
    def _assert_distinct_directories(source_dir: Path, target_dir: Path):
        """Prevent review/verification from comparing a translated tree to itself."""
        if source_dir.resolve() == target_dir.resolve():
            raise ValueError("Source and target directories must be different")

    def clone(self, repo_url: str, output_dir: str | Path) -> Path:
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
        self._assert_distinct_directories(source_dir, dest_dir)
        state_path = dest_dir / STATE_FILE
        if dest_dir.exists() and state_path.exists():
            logger.info(f"Resuming: keeping existing output directory {dest_dir}")
        else:
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
            if data.get("source_lang", self.source_lang) != self.source_lang:
                logger.warning("Resume state source language changed; starting fresh")
                return set()
            if data.get("target_lang", self.target_lang) != self.target_lang:
                logger.warning("Resume state target language changed; starting fresh")
                return set()
            if data.get("translate_code", self.translate_code) != self.translate_code:
                logger.warning("Resume state code mode changed; starting fresh")
                return set()
            if data.get("code_scope", self.code_scope) != self.code_scope:
                logger.warning("Resume state code scope changed; starting fresh")
                return set()
            return set(data.get("translated", []))
        except (json.JSONDecodeError, KeyError):
            logger.warning("Corrupt state file, starting fresh")
            return set()

    def _save_state(self, directory: Path, translated: set):
        """Atomically save translated file paths to avoid corrupt resume state."""
        state_path = directory / STATE_FILE
        data = {
            "version": 2,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "translate_code": self.translate_code,
            "code_scope": self.code_scope,
            "translated": sorted(translated),
        }
        fd, temporary_name = tempfile.mkstemp(
            dir=directory,
            prefix=f"{STATE_FILE}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
                json.dump(data, temporary_file, ensure_ascii=False)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, state_path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def translate_in_place(self, directory: Path) -> TranslationStats:
        """Translate files in-place."""
        self.stats = TranslationStats()
        files = get_translatable_files(
            directory,
            include_patterns=self.include_patterns,
            exclude_patterns=self.exclude_patterns,
            translate_code=self.translate_code,
        )
        self.stats.total_files = len(files)

        console.print(
            f"\n🌐 Translating [cyan]{len(files)}[/cyan] files "
            f"({self.source_lang} → {self.target_lang})"
        )
        console.print(f"   Engine: [green]{self.translator.name}[/green]")

        # Load resume state
        translated_set = self._load_state(directory)
        if translated_set:
            console.print(
                f"   Resuming: [yellow]{len(translated_set)}[/yellow] files already translated"
            )

        # Filter to only files that need translation
        to_translate = []
        for filepath in files:
            try:
                rel = str(filepath.relative_to(directory))
                if rel in translated_set:
                    self.stats.skipped_files += 1
                    continue
                content = filepath.read_text(encoding="utf-8")
                self.stats.total_chars += len(content)
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
            if not self.dry_run:
                self._save_state(directory, translated_set)
            return self.stats

        # Translate files
        total = len(to_translate)
        if self.max_workers > 1 and total > 1:
            console.print(f"   Workers: {self.max_workers}")
            import threading
            from concurrent.futures import ThreadPoolExecutor

            lock = threading.Lock()
            processed = 0

            def _process(filepath):
                nonlocal processed
                rel_path = str(filepath.relative_to(directory))
                try:
                    result = self._translate_one_file(filepath, directory)
                except Exception as e:
                    result = {"ok": False, "rel": rel_path, "error": str(e)}
                with lock:
                    if result.get("action") == "translated":
                        translated_set.add(rel_path)
                    if not self.dry_run:
                        self._save_state(directory, translated_set)
                    processed += 1
                    if processed % 10 == 0 or processed == total:
                        console.print(f"   [{processed}/{total}] Processed: {rel_path}")
                return result

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                results = list(pool.map(_process, to_translate))
            for result in results:
                self._record_result(result)
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
                    result = self._translate_one_file(filepath, directory)
                    self._record_result(result)
                    if result.get("action") == "translated":
                        translated_set.add(result["rel"])
                    if not self.dry_run:
                        self._save_state(directory, translated_set)
                    progress.advance(task)

        self.stats.end_time = datetime.now()

        # Print summary
        console.print(f"\n{self.stats.summary()}")
        return self.stats

    def _record_result(self, result: dict):
        """Update run statistics from one file result."""
        if not result.get("ok"):
            self.stats.failed_files += 1
            return
        if result.get("action") == "translated":
            self.stats.translated_files += 1
            self.stats.translated_chars += int(result.get("translated_chars", 0))
        else:
            self.stats.skipped_files += 1

    def _translate_one_file(
        self,
        filepath: Path,
        root: Path,
        progress=None,
        task=None,
        translated_set: set | None = None,
    ) -> dict:
        """Translate a single file. Returns dict with translation stats.

        Thread-safe: does not access shared state when progress/task/translated_set are None.
        """
        rel_path = str(filepath.relative_to(root))
        try:
            content = filepath.read_text(encoding="utf-8")

            from .detector import extract_translatable_text

            translatable = extract_translatable_text(content, filepath.suffix)

            if not translatable or len(translatable.strip()) < 5:
                return {"ok": True, "rel": rel_path, "action": "skip"}

            if self.dry_run:
                logger.info(f"[DRY RUN] Would translate: {rel_path}")
                return {"ok": True, "rel": rel_path, "action": "dry-run"}

            if filepath.suffix in (".md", ".markdown", ".rst", ".txt"):
                translated = self._translate_markdown(content)
            elif filepath.suffix == ".json":
                translated = self._translate_json_content(content)
            else:
                translated_lines = self._translate_source_lines(
                    content.split("\n"),
                    filepath.suffix.lower(),
                )
                translated = "\n".join(translated_lines)

            if translated == content:
                return {"ok": True, "rel": rel_path, "action": "skip"}

            self._validate_translated_content(filepath, translated)
            self._write_text_atomic(filepath, translated)

            return {
                "ok": True,
                "rel": rel_path,
                "action": "translated",
                "translated_chars": len(translatable),
            }

        except Exception as e:
            logger.warning(f"Failed to translate {rel_path}: {e}")
            return {"ok": False, "rel": rel_path, "error": str(e)}

    @staticmethod
    def _write_text_atomic(filepath: Path, content: str):
        """Replace a text file atomically after translation and validation."""
        fd, temporary_name = tempfile.mkstemp(
            dir=filepath.parent,
            prefix=f".{filepath.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, filepath)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _validate_translated_content(filepath: Path, content: str):
        """Reject translated structured files that no longer parse."""
        suffix = filepath.suffix.lower()
        if suffix == ".json":
            json.loads(content)
        elif suffix in {".yaml", ".yml"}:
            import yaml

            yaml.safe_load(content)
        elif suffix == ".toml":
            try:
                import tomllib
            except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
                return
            tomllib.loads(content)
        elif suffix == ".py":
            compile(content, str(filepath), "exec")

    def _translate_batch(
        self,
        files: list[Path],
        root: Path,
        progress: Progress,
        task,
        translated_set: set | None = None,
    ):
        """Translate a batch of files (sequential, for backward compatibility)."""
        if translated_set is None:
            translated_set = set()
        for filepath in files:
            result = self._translate_one_file(filepath, root)
            self._record_result(result)
            if result.get("action") == "translated":
                translated_set.add(result["rel"])
            if not self.dry_run:
                self._save_state(root, translated_set)
            progress.advance(task)

    def _translate_source_lines(self, lines: list[str], suffix: str = "") -> list[str]:
        """
        Translate only comments, quoted string bodies, and markup text nodes.

        Delimiters and surrounding source code are never sent to the provider. This
        intentionally leaves ambiguous constructs untouched instead of risking a
        syntactically invalid repository. All extracted spans are sent through the
        provider's batch API, which is substantially faster for LibreTranslate.
        """
        deferred: list[str] = []
        markers_by_text: dict[str, str] = {}

        def defer(text: str) -> str:
            marker = markers_by_text.get(text)
            if marker is None:
                marker = f"__REPO_TRANSLATOR_SPAN_{len(deferred):08d}__"
                markers_by_text[text] = marker
                deferred.append(text)
            return marker

        templates = []
        in_block_comment = False
        comments_only = getattr(self, "code_scope", "comments-and-strings") == "comments"
        python_docstrings = (
            self._find_python_docstrings(lines) if comments_only and suffix == ".py" else {}
        )
        for line_number, line in enumerate(lines, start=1):
            if line_number in python_docstrings:
                template = self._translate_python_docstring_line(
                    line,
                    line_number,
                    python_docstrings[line_number],
                    defer,
                )
            elif comments_only:
                template, in_block_comment = self._translate_comment_only_line(
                    line,
                    suffix,
                    in_block_comment,
                    defer,
                )
            else:
                template = self._translate_source_line(line, suffix, defer)
            templates.append(template)
        if not deferred:
            return templates

        translated = self._translate_many(deferred)
        replacements = {
            markers_by_text[original]: value.replace("\r", " ").replace("\n", " ")
            for original, value in zip(deferred, translated)
        }
        return [self._replace_translation_markers(template, replacements) for template in templates]

    @staticmethod
    def _find_python_docstrings(lines: list[str]) -> dict[int, tuple[int, int, int, str]]:
        """Map Python docstring lines using AST positions, excluding ordinary strings."""
        import ast

        try:
            tree = ast.parse("\n".join(lines))
        except SyntaxError:
            return {}

        positions: dict[int, tuple[int, int, int, str]] = {}
        owners = [tree]
        owners.extend(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        for owner in owners:
            body = getattr(owner, "body", [])
            if not body:
                continue
            expression = body[0]
            value = getattr(expression, "value", None)
            if not isinstance(expression, ast.Expr) or not isinstance(value, ast.Constant):
                continue
            if not isinstance(value.value, str):
                continue

            start = expression.lineno
            end = getattr(expression, "end_lineno", start)
            column = expression.col_offset
            opening_line = lines[start - 1]
            quote_match = re.search(r"(?:[rRuUbBfF]*)(?P<quote>\"\"\"|''')", opening_line[column:])
            if not quote_match:
                continue
            quote = quote_match.group("quote")
            quote_column = column + quote_match.start("quote")
            for line_number in range(start, end + 1):
                positions[line_number] = (start, end, quote_column, quote)
        return positions

    def _translate_python_docstring_line(
        self,
        line: str,
        line_number: int,
        position: tuple[int, int, int, str],
        translate_span,
    ) -> str:
        """Translate a Python docstring body while preserving prefixes and quotes."""
        start, end, quote_column, quote = position

        def translate_body(body: str) -> str:
            if not _has_cjk_ideograph(body):
                return body
            match = re.match(r"(?P<prefix>\s*)(?P<body>.*?)(?P<suffix>\s*)$", body)
            if not match:
                return body
            translated = (
                match.group("prefix")
                + self._translate_preserving_tokens(
                    match.group("body"), translate_span=translate_span
                )
                + match.group("suffix")
            )
            if "__REPO_TRANSLATOR_SPAN_" in translated:
                tag = "TRIPLE_DOUBLE" if quote == '"""' else "TRIPLE_SINGLE"
                translated = re.sub(
                    r"(__REPO_TRANSLATOR_SPAN_\d{8}__)",
                    rf"\1__QUOTE_{tag}__",
                    translated,
                )
            return translated

        if start == end:
            body_start = quote_column + len(quote)
            body_end = line.rfind(quote, body_start)
            if body_end < body_start:
                return line
            return line[:body_start] + translate_body(line[body_start:body_end]) + line[body_end:]
        if line_number == start:
            body_start = quote_column + len(quote)
            return line[:body_start] + translate_body(line[body_start:])
        if line_number == end:
            body_end = line.rfind(quote)
            if body_end < 0:
                return line
            return translate_body(line[:body_end]) + line[body_end:]
        return translate_body(line)

    def _translate_comment_only_line(
        self,
        line: str,
        suffix: str,
        in_block_comment: bool,
        translate_span,
    ) -> tuple[str, bool]:
        """Translate line and C-style block comments without touching literals."""

        def translate_body(body: str) -> str:
            if not _has_cjk_ideograph(body):
                return body
            match = re.match(r"(?P<prefix>\s*\*?\s*)(?P<body>.*?)(?P<suffix>\s*)$", body)
            if not match or not _has_cjk_ideograph(match.group("body")):
                return body
            return (
                match.group("prefix")
                + self._translate_preserving_tokens(
                    match.group("body"), translate_span=translate_span
                )
                + match.group("suffix")
            )

        if in_block_comment:
            end = line.find("*/")
            if end < 0:
                return translate_body(line), True
            return translate_body(line[:end]) + line[end:], False

        start = self._find_block_comment_start(line)
        if start is None:
            return self._translate_source_line(line, suffix, translate_span), False

        end = line.find("*/", start + 2)
        if end < 0:
            return line[: start + 2] + translate_body(line[start + 2 :]), True
        return (
            line[: start + 2] + translate_body(line[start + 2 : end]) + line[end:],
            False,
        )

    def _translate_source_line(self, line: str, suffix: str, translate_span=None) -> str:
        if not _has_cjk_ideograph(line):
            return line
        translate_span = translate_span or self._translate_text_span

        # Whole-line and inline block comments.
        for pattern in (
            r"^(?P<prefix>\s*/\*+\s*)(?P<body>.*?)(?P<suffix>\s*\*/\s*)$",
            r"^(?P<prefix>\s*<!--\s*)(?P<body>.*?)(?P<suffix>\s*-->\s*)$",
            r"^(?P<prefix>\s*\*+\s*)(?P<body>.*?)(?P<suffix>\s*)$",
            r"^(?P<prefix>\s*\{\s*/\*+\s*)(?P<body>.*?)(?P<suffix>\s*\*/\}\s*)$",  # JSX {/* … */}
        ):
            match = re.match(pattern, line)
            if match and _has_cjk_ideograph(match.group("body")):
                return (
                    match.group("prefix")
                    + self._translate_preserving_tokens(
                        match.group("body"), translate_span=translate_span
                    )
                    + match.group("suffix")
                )

        comment_start = self._find_line_comment_start(line, suffix)
        translated_comment = ""
        if comment_start is not None:
            marker_length = 2 if line.startswith("//", comment_start) else 1
            body_start = comment_start + marker_length
            body = line[body_start:]
            if _has_cjk_ideograph(body):
                spacing = re.match(r"(?P<prefix>\s*)(?P<body>.*?)(?P<suffix>\s*)$", body)
                if spacing:
                    translated_comment = (
                        spacing.group("prefix")
                        + self._translate_preserving_tokens(
                            spacing.group("body"), translate_span=translate_span
                        )
                        + spacing.group("suffix")
                    )
                else:
                    translated_comment = self._translate_preserving_tokens(
                        body, translate_span=translate_span
                    )
                line = line[:body_start]

        if getattr(self, "code_scope", "comments-and-strings") == "comments":
            return line + translated_comment

        replacements: list[tuple[int, int, str]] = []

        # Quoted values, including backtick template literals.
        # Template interpolation ${…} is safe because translate_preserving
        # extracts it as markers before sending the body to the translator.
        for match in QUOTED_STRING_PATTERN.finditer(line):
            if match.group("double") is not None:
                group_name, quote = "double", '"'
            elif match.group("single") is not None:
                group_name, quote = "single", "'"
            else:
                group_name, quote = "backtick", "`"
            body = match.group(group_name)
            if not _has_cjk_ideograph(body):
                continue
            translated = self._translate_preserving_tokens(body, translate_span=translate_span)
            if "__REPO_TRANSLATOR_SPAN_" in translated:
                tag = {"double": "DOUBLE", "single": "SINGLE", "backtick": "BACKTICK"}[group_name]
                translated = re.sub(
                    r"(__REPO_TRANSLATOR_SPAN_\d{8}__)",
                    rf"\1__QUOTE_{tag}__",
                    translated,
                )
            else:
                translated = translated.replace(quote, f"\\{quote}")
            replacements.append((match.start(group_name), match.end(group_name), translated))

        # Plain HTML/JSX text nodes such as <span>中文</span>.
        for match in re.finditer(r">(?P<body>[^<>{}]+)<", line):
            body = match.group("body")
            if not _has_cjk_ideograph(body):
                continue
            replacements.append(
                (
                    match.start("body"),
                    match.end("body"),
                    self._translate_preserving_tokens(body, translate_span=translate_span),
                )
            )

        for start, end, translated in sorted(replacements, reverse=True):
            line = line[:start] + translated + line[end:]
        return line + translated_comment

    @staticmethod
    def _find_block_comment_start(line: str) -> int | None:
        """Find a C-style block-comment opener outside quoted strings."""
        quote = None
        escaped = False
        index = 0
        while index < len(line) - 1:
            char = line[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    quote = None
            elif char in {'"', "'", "`"}:
                quote = char
            elif line.startswith("/*", index):
                return index
            index += 1
        return None

    @staticmethod
    def _find_line_comment_start(line: str, suffix: str) -> int | None:
        """Find a line-comment marker that is outside quoted strings."""
        hash_comment_suffixes = {
            "",
            ".py",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
            ".r",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".env",
        }
        quote = None
        escaped = False
        index = 0
        while index < len(line):
            char = line[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if char == "\\":
                escaped = True
                index += 1
                continue
            if quote:
                if char == quote:
                    quote = None
                index += 1
                continue
            if char in {'"', "'", "`"}:
                quote = char
                index += 1
                continue
            if line.startswith("//", index):
                return index
            if char == "#" and suffix in hash_comment_suffixes:
                if index == 0 and line.startswith("#!"):
                    return None
                return index
            index += 1
        return None

    def _translate_markdown(self, content: str) -> str:
        """Translate markdown prose without touching code, URLs, or placeholders.
        Batches all translatable lines into a single API call for speed.
        """
        lines = content.splitlines(keepends=True)
        fence_marker = None
        is_cjk_source = getattr(self, "source_lang", "zh") in ("zh", "ja", "ko")

        batch_entries: list[tuple[int, str, str]] = []
        batch_bodies: list[str] = []
        result: list[str | None] = []

        for line in lines:
            stripped = line.lstrip()
            marker_match = re.match(r"(`{3,}|~{3,})", stripped)
            if marker_match:
                result.append(line)
                marker = marker_match.group(1)[0]
                fence_marker = None if fence_marker == marker else marker
                continue

            if fence_marker:
                result.append(line)
                continue

            # CJK source: skip non-CJK lines (they're likely already in target language)
            if is_cjk_source and not _has_cjk_ideograph(line):
                result.append(line)
                continue

            line_body = line.rstrip("\r\n")
            newline = line[len(line_body) :]

            # Markdown table delimiters are structural. Translate cells separately
            # while keeping every pipe byte-for-byte.
            if "|" in line_body and line_body.strip().startswith("|"):
                if re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", line_body):
                    result.append(line)
                    continue
                cells = line_body.split("|")
                translated_cells = [
                    self._translate_preserving_tokens(cell) if _has_cjk_ideograph(cell) else cell
                    for cell in cells
                ]
                result.append("|".join(translated_cells) + newline)
                continue

            prefix_match = re.match(
                r"^(?P<prefix>\s*(?:#{1,6}\s+|>\s+|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)?)",
                line_body,
            )
            prefix = prefix_match.group("prefix") if prefix_match else ""
            body = line_body[len(prefix) :]
            if not body.strip():
                result.append(line)
                continue

            batch_entries.append((len(result), prefix, newline))
            batch_bodies.append(body)
            result.append(None)  # placeholder, filled after batch translate

        if not batch_entries:
            return content

        max_lines_per_batch = max(1, min(getattr(self, "batch_size", 40), 40))
        for chunk_start in range(0, len(batch_entries), max_lines_per_batch):
            chunk_entries = batch_entries[chunk_start : chunk_start + max_lines_per_batch]
            chunk_bodies = batch_bodies[chunk_start : chunk_start + max_lines_per_batch]
            translated_bodies = self._translate_many_preserving_tokens(chunk_bodies)
            for (idx, prefix, newline), translated_body in zip(chunk_entries, translated_bodies):
                cleaned = translated_body.strip()
                result[idx] = prefix + cleaned + newline

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

    def _translate_many_preserving_tokens(self, texts: list[str]) -> list[str]:
        """Batch human-readable spans while preserving repository-specific tokens."""
        deferred: list[str] = []
        templates = []

        def defer(text: str) -> str:
            marker = f"__REPO_TRANSLATOR_SPAN_{len(deferred):08d}__"
            deferred.append(text)
            return marker

        for text in texts:
            templates.append(self._translate_preserving_tokens(text, translate_span=defer))

        translated = self._translate_many(deferred)
        replacements = {
            f"__REPO_TRANSLATOR_SPAN_{index:08d}__": value for index, value in enumerate(translated)
        }
        return [self._replace_translation_markers(template, replacements) for template in templates]

    @staticmethod
    def _replace_translation_markers(text: str, replacements: dict[str, str]) -> str:
        for marker, translated in replacements.items():
            text = text.replace(
                f"{marker}__QUOTE_DOUBLE__",
                translated.replace('"', '\\"'),
            )
            text = text.replace(
                f"{marker}__QUOTE_SINGLE__",
                translated.replace("'", "\\'"),
            )
            text = text.replace(
                f"{marker}__QUOTE_TRIPLE_DOUBLE__",
                translated.replace('"""', '\\"""'),
            )
            text = text.replace(
                f"{marker}__QUOTE_TRIPLE_SINGLE__",
                translated.replace("'''", "\\'''"),
            )
            text = text.replace(marker, translated)
        return text

    def _translate_many(self, texts: list[str]) -> list[str]:
        """Translate spans using native provider batches, with one retry per batch."""
        if not texts:
            return []

        results = []
        batch_size = max(1, getattr(self, "batch_size", 40))
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            translate_batch = getattr(self.translator, "translate_batch", None)
            if translate_batch is None:

                def translate_batch(values):
                    return [self.translator.translate_text(value) for value in values]

            try:
                translated = translate_batch(batch)
            except Exception as error:
                logger.warning(f"Retry batch after error: {error}")
                translated = translate_batch(batch)
            if len(translated) != len(batch):
                raise ValueError(
                    f"Translation provider returned {len(translated)} results "
                    f"for {len(batch)} inputs"
                )
            results.extend(translated)
        return results

    def _translate_preserving_tokens(self, text: str, translate_span=None) -> str:
        """Translate unprotected spans and reassemble protected technical tokens unchanged."""
        if not text.strip():
            return text
        translate_span = translate_span or self._translate_text_span

        pieces = []
        last = 0
        for match in PROTECTED_TOKEN_PATTERN.finditer(text):
            if match.start() > last:
                pieces.append(translate_span(text[last : match.start()]))
            pieces.append(match.group(0))
            last = match.end()

        if last < len(text):
            pieces.append(translate_span(text[last:]))

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

    def push(self, directory: Path, repo_name: str, token: str | None = None) -> str:
        """Commit on a translation branch and push without persisting credentials."""
        import git

        token = token or os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ValueError("GitHub token required for pushing. Set GITHUB_TOKEN env var.")

        console.print(f"\n📤 Pushing to [cyan]{repo_name}[/cyan]...")

        repo = git.Repo(directory)
        branch_name = f"repo-translator/{self.target_lang}"

        if branch_name in {head.name for head in repo.heads}:
            repo.heads[branch_name].checkout()
        else:
            repo.create_head(branch_name).checkout()

        repo.git.add(A=True)
        if repo.is_dirty(index=True, working_tree=True, untracked_files=True):
            repo.index.commit(
                f"Translate {self.source_lang} → {self.target_lang} using repo-translator"
            )

        remote_url = f"https://github.com/{repo_name}.git"
        with tempfile.TemporaryDirectory(prefix="repo-translator-auth-") as auth_dir:
            askpass = Path(auth_dir) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
                '  *) printf "%s\\n" "$REPO_TRANSLATOR_GITHUB_TOKEN" ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            with repo.git.custom_environment(
                GIT_ASKPASS=str(askpass),
                GIT_TERMINAL_PROMPT="0",
                REPO_TRANSLATOR_GITHUB_TOKEN=token,
            ):
                repo.git.push(remote_url, f"{branch_name}:refs/heads/{branch_name}")

        push_url = f"https://github.com/{repo_name}/tree/{branch_name}"
        console.print(f"   ✅ Pushed to {push_url}")
        return push_url
