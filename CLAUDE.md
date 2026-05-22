# Project Instructions: repo-translator

## Overview

`repo-translator` is a Python CLI/library for translating whole repositories cheaply. The intended pipeline is:

```text
clone or use local repo → detect/filter translatable files → translate → optional AI review → optional equivalence verification → optional GitHub push
```

Prefer cheap/free bulk translators (`google`, `google-alt`, `deepl`) for most content, and use LLM-backed engines/review only when quality or context requires it.

## Tech Stack

- Language: Python 3.9+
- Packaging: `pyproject.toml` + `setup.py`, editable install supported
- CLI: Click (`repo_translator/cli.py`)
- Terminal output: Rich
- Git operations: GitPython
- Language detection: `langdetect` plus custom CJK detection
- Translation engines: Google Translate, deep-translator Google, DeepL, OpenAI-compatible API, Ollama
- AI review provider support: OpenAI-compatible APIs including Vercel AI Gateway (`https://ai-gateway.vercel.sh/v1`)
- Tests: pytest
- Formatting/lint tooling in dev extras: Black, Ruff

## Build, Install, and Test

Use the project virtualenv when available:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Useful targeted commands:

```bash
.venv/bin/python -m pytest -q tests/test_translator.py
.venv/bin/python -m pytest -q tests/test_translator.py::TestCLI
.venv/bin/repo-translator engines
```

Current full test suite expectation: `29 passed`.

## Project Structure

- `repo_translator/cli.py` — Click command group and CLI commands: `translate`, `detect`, `review`, `engines`, `push`.
- `repo_translator/core.py` — `RepoTranslator` orchestration class for clone/translate/review/push.
- `repo_translator/detector.py` — language detection, CJK helpers, and text extraction from source/markup files.
- `repo_translator/file_filter.py` — translatable extension allowlist and skip rules for binary/dependency/build files.
- `repo_translator/translators/base.py` — translator interface and text chunking helper.
- `repo_translator/translators/google.py` — Google Translate implementations via `googletrans` and `deep-translator`.
- `repo_translator/translators/deepl.py` — DeepL API translator.
- `repo_translator/translators/llm.py` — OpenAI-compatible and Ollama translators.
- `repo_translator/translators/__init__.py` — translator registry/factory.
- `repo_translator/reviewers/__init__.py` — translation quality checks and optional LLM review.
- `repo_translator/equivalence.py` — source-vs-translated verification: manifest, syntax, invariant preservation, and optional AI semantic checks.
- `configs/default.yaml` — default runtime config reference.
- `tests/test_translator.py` — pytest unit/regression tests.
- `tests/test_equivalence.py` — equivalence verification regression tests.
- `tests/test_imports.py` — import smoke test collected by pytest.

## Core Data Flow

1. `repo-translator translate ...` enters through `repo_translator.cli.translate()`.
2. CLI resolves output directory and creates `RepoTranslator` with selected translation/review engines.
3. `RepoTranslator.run()` chooses `repo_url` clone or `repo_dir` local mode.
4. `get_translatable_files()` recursively filters files by extension, size, skip dirs, lock files, binary extensions, and minified patterns.
5. `translate_in_place()` filters files that likely need translation.
   - For CJK source languages (`zh`, `ja`, `ko`), it looks for CJK characters.
   - For other languages, file-level detection is broad, but source-code line translation is still CJK-oriented.
6. Markdown/text files are translated as whole files.
7. Source/config/markup files are translated line-by-line for CJK-containing lines, preserving indentation.
8. Optional `AIReviewer` samples translated files and checks for untranslated CJK, broken markdown fences, and optional OpenAI-compatible deep review.
9. Optional equivalence verification compares source/target manifests, binary checksums, parseability of structured files, and invariants like URLs/placeholders/code blocks. With `--verify-ai`, it also runs sampled semantic checks through the configured OpenAI-compatible reviewer; default provider is Vercel AI Gateway.
10. Optional `push()` force-pushes translated output to `main` of an existing GitHub repo using `GITHUB_TOKEN` or provided token.

## Conventions

- Keep changes small and focused; this is an alpha-stage CLI with simple module boundaries.
- Add regression tests for every bug fix, especially CLI option compatibility and import paths.
- Use `tmp_path` and Click `CliRunner` for CLI tests that should not touch real repos.
- Avoid network-dependent tests. Translator initialization tests are okay; translation API calls should be mocked if added.
- Use source-language-aware naming in tests (`zh`, `en`, CJK sample text) to make detection behavior explicit.
- Preserve indentation and line structure when modifying translation logic.

## Verification Workflow

Run deterministic source-vs-translated checks after translation:

```bash
.venv/bin/repo-translator verify \
  --source ./repo-original \
  --target ./repo-translated \
  --json-output verify-report.json \
  --fail-on error
```

Add sampled AI semantic review through Vercel AI Gateway by setting an environment variable, not by committing secrets:

```bash
export AI_GATEWAY_API_KEY=...
.venv/bin/repo-translator verify \
  --source ./repo-original \
  --target ./repo-translated \
  --ai-check \
  --ai-provider vercel-ai-gateway \
  --model openai/gpt-4o-mini
```

`repo-translator translate --verify` runs the same verification before any optional push. If `--verify` is used on a local repo without `--output-dir`, the CLI writes to a sibling `*-translated` directory so the original remains available for comparison.

## Known Sharp Edges

- README mentions Anthropic support, but current translator registry does not implement an `anthropic` engine.
- `push()` assumes the destination GitHub repo exists and force-pushes `main:main`; it does not create repos yet.
- LLM translation uses OpenAI-compatible `/chat/completions` only.
- Non-CJK source-language support is incomplete for source-code line translation because line selection is CJK-based.
- `tests/test_imports.py` is a smoke script collected by pytest; it prints output and is not Ruff-clean by design.

## Safe Development Workflow

1. Check clean state before editing:
   ```bash
   git status --short
   ```
2. Install/update deps in `.venv`:
   ```bash
   .venv/bin/python -m pip install -e '.[dev]'
   ```
3. Add or update a regression test.
4. Implement the smallest code change.
5. Run targeted tests, then full tests:
   ```bash
   .venv/bin/python -m pytest -q tests/test_translator.py::<ClassName>::<test_name>
   .venv/bin/python -m pytest -q
   ```
6. Review diff before handoff:
   ```bash
   git diff --stat
   git diff
   ```

## Common Extension Points

- Add a translator: implement `BaseTranslator`, register it in `repo_translator/translators/__init__.py`, add tests for factory behavior and failure modes.
- Add file support: update `TRANSLATABLE_EXTENSIONS` and extraction logic in `detector.extract_translatable_text()` if needed.
- Improve review/verification: extend `AIReviewer._check_file()` for translated-only quality checks; extend `repo_translator/equivalence.py` for source-vs-target checks before adding paid/network LLM review.
- Improve push workflow: add repo creation/update behavior carefully; avoid leaking tokens in logs/remotes.
