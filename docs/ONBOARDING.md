# Onboarding Guide: repo-translator

## What This Project Does

`repo-translator` translates entire GitHub repositories from one language to another while keeping LLM costs low. It uses cheap/free translation APIs for bulk translation and optionally samples files for AI quality review.

The project is useful when a repository has documentation, comments, UI strings, or config text in a source language such as Chinese/Japanese/Korean and you want an English translated copy without manually translating every file.

## Tech Stack

- Python package and CLI (`Python >=3.9`)
- Click for command-line interface
- Rich for terminal UI/progress output
- GitPython for clone/push operations
- `langdetect` and custom CJK helpers for language detection
- Translation backends:
  - Google Translate via `googletrans`
  - Google Translate via `deep-translator` (`google-alt`)
  - DeepL API
  - OpenAI-compatible chat completion API
  - Ollama local generation API
- pytest for tests

## Architecture

```text
CLI command
  ↓
RepoTranslator
  ↓
source repo acquisition: clone URL or use local directory
  ↓
file filtering: extension allowlist + skip binary/dependency/build paths
  ↓
language/text detection: CJK helpers + extract human-readable text
  ↓
translation engine: Google/DeepL/LLM/Ollama
  ↓
optional AI review: deterministic checks + optional OpenAI-compatible review
  ↓
optional equivalence verification: manifest/syntax/invariants + optional Vercel AI Gateway semantic review
  ↓
optional GitHub push
```

## Key Entry Points

- `repo_translator/cli.py`
  - Defines all user-facing commands.
  - Start here for CLI behavior and option compatibility.

- `repo_translator/core.py`
  - Main pipeline orchestration.
  - Start here for clone/translate/review/push flow.

- `repo_translator/file_filter.py`
  - Controls what files are considered translatable.

- `repo_translator/detector.py`
  - Controls CJK detection, language detection, and text extraction from source files.

- `repo_translator/translators/`
  - Contains translation backend implementations and the factory registry.

- `repo_translator/reviewers/__init__.py`
  - Contains translation quality checks and optional AI review.

- `repo_translator/equivalence.py`
  - Compares the original and translated repos after translation.
  - Checks file manifest differences, binary checksum changes, structured syntax, URLs, placeholders, environment variable tokens, and Markdown code blocks.

- `tests/test_translator.py`
  - Main pytest suite and best source of expected translation/review behavior.

- `tests/test_equivalence.py`
  - Regression suite for source-vs-target verification behavior.

## Common Tasks

### Set up local development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

### Run tests

```bash
.venv/bin/python -m pytest -q
```

### Run CLI locally

```bash
.venv/bin/repo-translator engines
.venv/bin/repo-translator detect --repo /path/to/repo
.venv/bin/repo-translator translate --repo /path/to/repo --source-lang zh --target-lang en --translator google-alt --output-dir /tmp/repo-translated
```

### Review translation output

Both `--engine` and README-compatible `--reviewer` are accepted:

```bash
.venv/bin/repo-translator review --dir ./translated-repo --source-lang zh --reviewer openai
```

### Verify source/translated equivalence

Run deterministic checks before trusting or pushing a translated repo:

```bash
.venv/bin/repo-translator verify \
  --source ./original-repo \
  --target ./translated-repo \
  --json-output verify-report.json \
  --fail-on error
```

For semantic equivalence checks, use Vercel AI Gateway via an environment variable. Do not commit keys or pass them in examples:

```bash
export AI_GATEWAY_API_KEY=...
.venv/bin/repo-translator verify \
  --source ./original-repo \
  --target ./translated-repo \
  --ai-check \
  --ai-provider vercel-ai-gateway \
  --model openai/gpt-4o-mini
```

`translate --verify` runs the same verifier before `--push-to`, so a high-severity verification failure prevents push.

## Where To Look

- Add/fix CLI option: `repo_translator/cli.py`, then test with Click `CliRunner`.
- Add/fix translator engine: `repo_translator/translators/`, then update registry in `translators/__init__.py`.
- Change file inclusion/exclusion: `repo_translator/file_filter.py`.
- Improve code/comment/string extraction: `repo_translator/detector.py`.
- Fix translation pipeline behavior: `repo_translator/core.py`.
- Fix quality checks: `repo_translator/reviewers/__init__.py`.
- Fix source-vs-translated verification: `repo_translator/equivalence.py`.
- Add regression coverage: `tests/test_translator.py` or `tests/test_equivalence.py`.

## Current Verified State

After creating `.venv` and installing `.[dev]`, the full test suite passes:

```text
29 passed
```

## Known Limitations

- Anthropic is mentioned in some docs/marketing text but is not implemented in the translator registry yet.
- AI equivalence review uses OpenAI-compatible `/chat/completions`; Vercel AI Gateway is supported through `AI_GATEWAY_API_KEY` or `VERCEL_AI_GATEWAY_API_KEY`.
- `push()` currently expects an existing repo and force-pushes to `main`; it does not create the destination repo.
- Non-CJK source-language support is partial in source-code files because line selection currently keys off CJK detection.
- External translation calls are not integration-tested; prefer mocks for new tests.
