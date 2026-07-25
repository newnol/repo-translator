"""CLI interface for repo-translator."""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .core import RepoTranslator
from .detector import count_cjk_chars, detect_file_language
from .file_filter import get_translatable_files
from .translators import list_engines

console = Console()


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
def main(verbose):
    """🌐 repo-translator: Translate entire GitHub repositories cheaply."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


@main.command()
@click.option("--repo", "-r", required=True, help="GitHub repo URL or local path")
@click.option("--source-lang", "-s", default="zh", help="Source language (default: zh)")
@click.option("--target-lang", "-t", default="en", help="Target language (default: en)")
@click.option(
    "--translator",
    default="google",
    help="Translation engine(s). Comma-separated for multi: 'libre,mymemory'",
)
@click.option(
    "--api-key", envvar="TRANSLATOR_API_KEY", default=None, help="API key for translation engine"
)
@click.option("--model", default=None, help="Model name for LLM engines")
@click.option("--base-url", default=None, help="Base URL for LLM/Ollama engines")
@click.option("--output-dir", "-o", default=None, help="Output directory")
@click.option("--review-with", default=None, help="AI review engine (openai, vercel-ai-gateway)")
@click.option(
    "--review-api-key", envvar="REVIEW_API_KEY", default=None, help="API key for AI reviewer"
)
@click.option("--review-model", default="gpt-4o-mini", help="Model for AI review")
@click.option("--review-base-url", default=None, help="Base URL for AI reviewer")
@click.option(
    "--review-sample", default=0.15, type=float, help="Sample rate for AI review (0.0-1.0)"
)
@click.option(
    "--verify", is_flag=True, help="Verify source and translated repo equivalence before push"
)
@click.option(
    "--verify-ai", is_flag=True, help="Use AI semantic equivalence checks during verification"
)
@click.option(
    "--verify-provider", default="vercel-ai-gateway", help="AI provider for verify --verify-ai"
)
@click.option(
    "--verify-api-key",
    envvar=["AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "REVIEW_API_KEY"],
    default=None,
    help="API key for verify --verify-ai",
)
@click.option(
    "--verify-model", default="openai/gpt-4o-mini", help="AI model for verify --verify-ai"
)
@click.option("--verify-base-url", default=None, help="Base URL for verify --verify-ai")
@click.option(
    "--verify-sample", default=0.15, type=float, help="Sample rate for verify --verify-ai"
)
@click.option("--verify-max-files", default=20, type=int, help="Max files for verify --verify-ai")
@click.option(
    "--verify-fail-on",
    type=click.Choice(["info", "warning", "error", "never"]),
    default="error",
    help="Fail translation if verify finds this severity or worse",
)
@click.option(
    "--verify-json-output", default=None, help="Write verification report JSON to this path"
)
@click.option("--push-to", default=None, help="Push to GitHub repo (user/repo)")
@click.option(
    "--github-token", envvar="GITHUB_TOKEN", default=None, help="GitHub token for pushing"
)
@click.option(
    "--workers",
    default=1,
    type=click.IntRange(min=1),
    help="Parallel file workers. Use 4+ with LibreTranslate or multiple engines",
)
@click.option(
    "--batch-size",
    default=40,
    type=click.IntRange(min=1, max=100),
    help="Comment/prose spans per native provider batch",
)
@click.option(
    "--include",
    "include_patterns",
    multiple=True,
    help="Only translate matching repository-relative glob(s); repeatable",
)
@click.option(
    "--exclude",
    "exclude_patterns",
    multiple=True,
    help="Skip matching repository-relative glob(s); repeatable",
)
@click.option(
    "--translate-code/--docs-only",
    default=False,
    help="Translate conservative code comments/string values; default: docs only",
)
@click.option(
    "--code-scope",
    type=click.Choice(["comments", "comments-and-strings"]),
    default="comments-and-strings",
    help="With --translate-code, translate only comments or comments plus strings",
)
@click.option("--dry-run", is_flag=True, help="Show what would be translated without doing it")
@click.option(
    "--export-manifest", default=None, type=click.Path(), help="Export a manifest after translation"
)
@click.option(
    "--apply-manifest", default=None, type=click.Path(), help="Apply translations from this manifest instead of translating"
)
@click.option(
    "--translation-memory", default=None, type=click.Path(), help="Path to translation memory JSON file"
)
@click.option(
    "--fail-on-source-mismatch/--skip-on-source-mismatch",
    default=True,
    help="Control apply behavior on file hash mismatch (default: fail)",
)
@click.option("--audit-untranslated", is_flag=True, help="Run AUDIT stage after translation")
def translate(
    repo,
    source_lang,
    target_lang,
    translator,
    api_key,
    model,
    base_url,
    output_dir,
    review_with,
    review_api_key,
    review_model,
    review_base_url,
    review_sample,
    verify,
    verify_ai,
    verify_provider,
    verify_api_key,
    verify_model,
    verify_base_url,
    verify_sample,
    verify_max_files,
    verify_fail_on,
    verify_json_output,
    push_to,
    github_token,
    workers,
    batch_size,
    include_patterns,
    exclude_patterns,
    translate_code,
    code_scope,
    dry_run,
    export_manifest,
    apply_manifest,
    translation_memory,
    fail_on_source_mismatch,
    audit_untranslated,
):
    """Translate a repository from one language to another."""

    # --- Manifest flag validation ---
    # The flag pair --fail-on-source-mismatch/--skip-on-source-mismatch maps to a
    # single bool in Click, so mutual exclusivity is inherently handled. No extra
    # check needed.

    if apply_manifest is not None:
        manifest_path = Path(apply_manifest)
        if not manifest_path.is_file():
            console.print(
                f"[red]❌ --apply-manifest: file not found or not readable: {apply_manifest}[/red]"
            )
            sys.exit(1)
        try:
            manifest_path.read_bytes()[:1]
        except OSError as e:
            console.print(
                f"[red]❌ --apply-manifest: cannot read manifest: {e}[/red]"
            )
            sys.exit(1)

    console.print(
        Panel.fit(
            f"🌐 [bold]repo-translator[/bold]\n"
            f"   Source: {source_lang} → Target: {target_lang}\n"
            f"   Engine: {translator}\n"
            f"   Repo: {repo}",
            border_style="cyan",
        )
    )

    # Auto-detect output dir
    if output_dir is None:
        if repo.startswith(("http", "git@")):
            repo_name = repo.rstrip("/").split("/")[-1].replace(".git", "")
            output_dir = f"./{repo_name}-translated"
        else:
            repo_path = Path(repo)
            output_dir = (
                str(repo_path.with_name(repo_path.name + "-translated")) if verify else repo
            )

    # Create translator
    translator_obj = RepoTranslator(
        source_lang=source_lang,
        target_lang=target_lang,
        translator_engine=translator,
        translator_api_key=api_key,
        translator_model=model,
        translator_base_url=base_url,
        review_engine=review_with,
        review_api_key=review_api_key,
        review_model=review_model,
        review_base_url=review_base_url,
        review_sample_rate=review_sample,
        verify=verify,
        verify_ai=verify_ai,
        verify_engine=verify_provider,
        verify_api_key=verify_api_key,
        verify_model=verify_model,
        verify_base_url=verify_base_url,
        verify_sample_rate=verify_sample,
        verify_max_ai_files=verify_max_files,
        verify_fail_on=verify_fail_on,
        verify_json_output=verify_json_output,
        include_patterns=list(include_patterns) or None,
        exclude_patterns=list(exclude_patterns) or None,
        batch_size=batch_size,
        translate_code=translate_code,
        code_scope=code_scope,
        dry_run=dry_run,
        verbose=main.context_settings.get("verbose", False),
        max_workers=workers,
        export_manifest_path=export_manifest,
        apply_manifest_path=apply_manifest,
        translation_memory_path=translation_memory,
        fail_on_source_mismatch=fail_on_source_mismatch,
        audit_untranslated=audit_untranslated,
    )

    # Run pipeline
    result = translator_obj.run(
        repo_url=repo if repo.startswith(("http", "git@")) else None,
        repo_dir=repo if not (repo.startswith(("http", "git@"))) else None,
        output_dir=output_dir,
        push_to=push_to,
        github_token=github_token,
    )

    if result["success"]:
        console.print(
            Panel.fit(
                f"✅ [bold green]Translation complete![/bold green]\n"
                f"   Output: {output_dir}\n"
                f"   {result['stats'].summary() if result['stats'] else ''}",
                border_style="green",
            )
        )
    else:
        console.print(f"[red]❌ Translation failed: {result.get('error', 'Unknown error')}[/red]")
        sys.exit(1)


@main.command()
@click.option("--repo", "-r", required=True, help="GitHub repo URL or local path")
@click.option("--sample", "-n", default=20, help="Number of files to sample")
def detect(repo, sample):
    """Detect languages in a repository."""
    if repo.startswith(("http", "git@")):
        import tempfile

        import git

        tmpdir = tempfile.mkdtemp()
        console.print(f"📥 Cloning {repo}...")
        git.Repo.clone_from(repo, tmpdir, depth=1)
        repo_path = Path(tmpdir)
    else:
        repo_path = Path(repo)

    files = get_translatable_files(repo_path)
    console.print(f"\n📁 Found {len(files)} translatable files")

    # Sample and detect
    import random

    sample_files = random.sample(files, min(sample, len(files)))

    table = Table(title="Language Detection Results")
    table.add_column("File", style="cyan")
    table.add_column("Language", style="green")
    table.add_column("CJK %", style="yellow")

    lang_counts = {}
    for filepath in sample_files:
        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
            lang = detect_file_language(filepath)
            cjk_pct = (
                f"{count_cjk_chars(content) / max(len(content), 1) * 100:.1f}%" if content else "0%"
            )
            rel = str(filepath.relative_to(repo_path))
            table.add_row(rel, lang or "unknown", cjk_pct)
            lang_counts[lang or "unknown"] = lang_counts.get(lang or "unknown", 0) + 1
        except Exception:
            pass

    console.print(table)
    console.print(f"\n📊 Language summary: {lang_counts}")


@main.command()
@click.option("--dir", "-d", required=True, help="Directory to review")
@click.option("--source-lang", "-s", default="zh", help="Source language")
@click.option(
    "--engine",
    "--reviewer",
    "-e",
    default="openai",
    help="Review engine (openai, vercel-ai-gateway)",
)
@click.option(
    "--api-key",
    envvar=["REVIEW_API_KEY", "AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY"],
    default=None,
    help="API key",
)
@click.option("--sample-rate", default=0.15, type=float, help="Sample rate")
def review(dir, source_lang, engine, api_key, sample_rate):
    """Review translation quality with AI."""
    from .reviewers import AIReviewer

    reviewer = AIReviewer(
        source_lang=source_lang,
        engine=engine,
        api_key=api_key,
        sample_rate=sample_rate,
    )

    report = reviewer.review(Path(dir))
    console.print(report.summary())


@main.command()
@click.option("--source", "-s", required=True, help="Original source repository directory")
@click.option("--target", "-t", required=True, help="Translated repository directory")
@click.option("--ai-check", is_flag=True, help="Run optional AI semantic equivalence review")
@click.option(
    "--ai-provider",
    "--reviewer",
    default="vercel-ai-gateway",
    help="AI provider for semantic review",
)
@click.option(
    "--api-key",
    envvar=["AI_GATEWAY_API_KEY", "VERCEL_AI_GATEWAY_API_KEY", "REVIEW_API_KEY"],
    default=None,
    help="AI provider API key",
)
@click.option("--model", default="openai/gpt-4o-mini", help="AI model for semantic review")
@click.option("--base-url", default=None, help="AI provider base URL")
@click.option("--source-lang", default="zh", help="Source language")
@click.option("--target-lang", default="en", help="Target language")
@click.option("--sample-rate", default=0.15, type=float, help="AI sample rate")
@click.option("--max-ai-files", default=20, type=int, help="Max files for AI semantic review")
@click.option(
    "--fail-on",
    type=click.Choice(["info", "warning", "error", "never"]),
    default="error",
    help="Exit non-zero if this severity or worse is found",
)
@click.option("--json-output", default=None, help="Write verification report JSON to this path")
def verify(
    source,
    target,
    ai_check,
    ai_provider,
    api_key,
    model,
    base_url,
    source_lang,
    target_lang,
    sample_rate,
    max_ai_files,
    fail_on,
    json_output,
):
    """Verify that a translated repo remains technically equivalent to the source."""
    import json

    from .equivalence import verify_equivalence

    source_path = Path(source)
    target_path = Path(target)
    for label, p in [("source", source_path), ("target", target_path)]:
        if not p.exists() or not p.is_dir():
            console.print(f"[red]❌ VERIFY failed: {label} repo is unreadable: {p}[/red]")
            sys.exit(1)

    report = verify_equivalence(
        source_dir=source_path,
        target_dir=target_path,
        ai_check=ai_check,
        ai_engine=ai_provider,
        ai_api_key=api_key,
        ai_model=model,
        ai_base_url=base_url,
        source_lang=source_lang,
        target_lang=target_lang,
        sample_rate=sample_rate,
        max_ai_files=max_ai_files,
    )
    console.print(report.summary())

    if json_output:
        Path(json_output).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if report.should_fail(fail_on):
        sys.exit(1)


@main.command()
def engines():
    """List available translation engines."""
    table = Table(title="Available Translation Engines")
    table.add_column("Engine", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("Cost", style="yellow")

    costs = {
        "google": "Free (500K chars/month)",
        "google-alt": "Free (500K chars/month)",
        "deepl": "$5.49/month (500K chars)",
        "openai": "~$0.01/1K chars",
        "ollama": "Free (local)",
        "libre": "Free (self-hosted Docker)",
        "mymemory": "Free (100K chars/day, no key needed)",
    }

    for name, desc in list_engines().items():
        table.add_row(name, desc, costs.get(name, "Unknown"))

    console.print(table)


@main.command()
@click.option("--dir", "-d", required=True, help="Directory to push")
@click.option("--repo", "-r", required=True, help="GitHub repo (user/repo)")
@click.option("--token", envvar="GITHUB_TOKEN", default=None, help="GitHub token")
def push(dir, repo, token):
    """Push translated files to GitHub."""
    from .core import RepoTranslator

    translator = RepoTranslator()
    url = translator.push(Path(dir), repo, token)
    console.print(f"✅ Pushed to {url}")


# ---------------------------------------------------------------------------
# Manifest pipeline subcommands
# ---------------------------------------------------------------------------


@main.command()
@click.option("--repo", "-r", required=True, help="Source repo directory")
@click.option("--output-dir", "-o", default=".", help="Where to write manifest artifacts")
@click.option("--source-lang", "-s", default="zh", help="Source language (default: zh)")
@click.option("--target-lang", "-t", default="en", help="Target language (default: en)")
@click.option("--translate-code/--docs-only", default=False, help="Include code strings")
@click.option("--include", "include_patterns", multiple=True, help="Include glob patterns")
@click.option("--exclude", "exclude_patterns", multiple=True, help="Exclude glob patterns")
def extract(repo, output_dir, source_lang, target_lang, translate_code, include_patterns, exclude_patterns):
    """EXTRACT stage: walk a repo and write a translation manifest."""
    from .extractors.base import ExtractionReport, extract_repo
    from .manifest import Manifest
    from .segments import ManifestHeader

    root = Path(repo).resolve()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "translation-manifest.jsonl"

    header = ManifestHeader(
        source_lang=source_lang,
        target_lang=target_lang,
        repo_root=str(root),
    )
    report = ExtractionReport()
    man = Manifest.open_for_write(manifest_path, header)
    for seg in extract_repo(
        root,
        include_patterns=list(include_patterns) or None,
        exclude_patterns=list(exclude_patterns) or None,
        translate_code=translate_code,
        report=report,
    ):
        man.append(seg)
    man.finalize()

    console.print(f"✅ Extracted {man._count} segments → {manifest_path}")
    if report.fallback_files:
        console.print(f"⚠️  {len(report.fallback_files)} file(s) used regex fallback")


@main.command(name="translate-manifest")
@click.option("--manifest", "-m", required=True, help="Path to translation-manifest.jsonl")
@click.option("--translator", default="google", help="Translation engine")
@click.option("--api-key", envvar="TRANSLATOR_API_KEY", default=None, help="API key")
@click.option("--translation-memory", default=None, help="Path to translation-memory.json")
@click.option("--batch-size", default=40, type=click.IntRange(1, 100), help="Batch size (1-100)")
def translate_manifest_cmd(manifest, translator, api_key, translation_memory, batch_size):
    """TRANSLATE-MANIFEST stage: fill target text in a manifest."""
    from .manifest import load_memory, translate_manifest
    from .translators import get_translator

    manifest_path = Path(manifest)
    memory_path = Path(translation_memory) if translation_memory else None
    memory = load_memory(memory_path)

    # Read header to get source/target lang
    from .manifest import Manifest as _M
    header, _ = _M.read(manifest_path)

    tr = get_translator(
        engine=translator,
        source_lang=header.source_lang,
        target_lang=header.target_lang,
        api_key=api_key,
    )

    stats = translate_manifest(
        manifest_path,
        tr,
        memory=memory,
        memory_path=memory_path,
        batch_size=batch_size,
    )

    console.print(
        f"✅ Translated {stats.segments_translated}/{stats.segments_total} segments "
        f"({stats.segments_from_memory} from memory, {stats.segments_from_provider} from provider)"
    )


@main.command()
@click.option("--manifest", "-m", required=True, help="Path to translation-manifest.jsonl")
@click.option("--repo", "-r", required=True, help="Source repo (offsets relative to it)")
@click.option("--output-dir", "-o", required=True, help="Output directory")
@click.option("--fail-on-source-mismatch/--skip-on-source-mismatch", default=True, help="Hash guard behavior")
def apply(manifest, repo, output_dir, fail_on_source_mismatch):
    """APPLY stage: splice translations into a copy of the source repo."""
    from .applicator import apply_manifest

    stats = apply_manifest(
        manifest_path=Path(manifest),
        source_root=Path(repo),
        output_root=Path(output_dir),
        fail_on_source_mismatch=fail_on_source_mismatch,
    )

    console.print(
        f"✅ Applied {stats.segments_applied} segments across {stats.files_spliced} files"
    )
    if stats.files_skipped_mismatch:
        console.print(f"⚠️  {stats.files_skipped_mismatch} file(s) skipped (hash mismatch)")


@main.command()
@click.option("--dir", "-d", "directory", required=True, help="Directory to audit")
@click.option("--include", "include_patterns", multiple=True, help="Include glob patterns")
@click.option("--exclude", "exclude_patterns", multiple=True, help="Exclude glob patterns")
def audit(directory, include_patterns, exclude_patterns):
    """AUDIT stage: scan for residual CJK in translated output."""
    from .audit import audit_repo

    report = audit_repo(
        Path(directory),
        include_patterns=list(include_patterns) or None,
        exclude_patterns=list(exclude_patterns) or None,
    )

    if report.total_findings == 0:
        console.print("✅ No residual CJK found")
    else:
        console.print(
            f"⚠️  {report.total_findings} residual finding(s) in "
            f"{report.files_with_residual} file(s)"
        )
        for f in report.findings[:20]:
            console.print(f"  {f.path}:{f.line} — {f.snippet[:80]}")
        if len(report.findings) > 20:
            console.print(f"  … and {len(report.findings) - 20} more")


if __name__ == "__main__":
    main()
