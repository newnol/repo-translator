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
    type=int,
    help="Parallel file workers. Use 4+ with libre or multi-engine",
)
@click.option("--dry-run", is_flag=True, help="Show what would be translated without doing it")
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
    dry_run,
):
    """Translate a repository from one language to another."""

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
        dry_run=dry_run,
        verbose=main.context_settings.get("verbose", False),
        max_workers=workers,
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

    report = verify_equivalence(
        source_dir=Path(source),
        target_dir=Path(target),
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


if __name__ == "__main__":
    main()
