# repo-translator 🌐

**Translate entire GitHub repositories** from any language to another — cheaply and reliably.

## Why?

Translating a full repo with LLMs costs $$$ (we burned ~$5+ on a single repo). This tool uses **cheap/free translation APIs** for bulk work, then optionally uses **AI for quality review** — saving 95-99% in LLM costs.

## Architecture

```
┌──────────┐   ┌──────────┐   ┌────────────┐   ┌───────────┐   ┌──────────┐
│ Clone    │──▶│ Detect   │──▶│ Translate  │──▶│ AI Review │──▶│ Push to  │
│ Repo     │   │ Language │   │ (API bulk) │   │ (spot     │   │ GitHub   │
│          │   │ per file │   │            │   │  check)   │   │          │
└──────────┘   └──────────┘   └────────────┘   └───────────┘   └──────────┘
```

## Cost Comparison

| Method | 1000 files (~200K chars) |
|---|---|
| LLM (Claude/GPT for all) | $3-8 |
| **repo-translator** | **$0.01-0.10** |

## Quick Start

```bash
pip install -e .

# Translate a repo from Chinese to English
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator google \
  --output-dir ./translated-repo

# With AI review (optional)
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator deepl \
  --review-with openai \
  --push-to your-username/new-repo-name
```

## Translation Engines

| Engine | Cost | Quality | Speed |
|---|---|---|---|
| `google` | Free (500K chars/month) | Good | Fast |
| `deepl` | $5.49/month (500K chars) | Best | Fast |
| `openai` | ~$0.01/1K chars | Great | Medium |
| `anthropic` | ~$0.01/1K chars | Great | Medium |

## AI Review

After bulk translation, optionally run LLM spot-check on a sample:
- Checks for untranslated Chinese characters
- Validates technical term accuracy
- Fixes formatting/code block issues
- Only reviews 10-20% of files → massive cost savings

## Supported File Types

- Markdown (`.md`)
- Source code comments (`.py`, `.rs`, `.js`, `.ts`, `.go`, `.java`, etc.)
- HTML/Jinja templates (`.html`, `.jinja2`)
- Config/JSON with translatable strings
- Documentation files (`.rst`, `.txt`)

## Skip Patterns (by default)

- Binary files (images, PDFs, fonts)
- Package files (`node_modules/`, `target/`, `vendor/`)
- Minified files (`.min.js`, `.min.css`)
- Lock files (`package-lock.json`, `Cargo.lock`)

## Configuration

```yaml
# config.yaml
source_lang: zh
target_lang: en
translator: google
review_with: null  # or: openai, anthropic, ollama
review_sample_rate: 0.15  # review 15% of files

# File patterns to include/exclude
include:
  - "*.md"
  - "*.py"
  - "*.rs"
  - "*.js"
  - "*.html"
  - "*.json"

exclude:
  - "node_modules/**"
  - "target/**"
  - "*.min.js"
  - "*.lock"
  - "package-lock.json"

# Batch settings
batch_size: 40  # files per batch
max_chars_per_request: 5000  # chars per API call
retry_attempts: 3
retry_delay: 2  # seconds

# GitHub settings
github_token: null  # or set GITHUB_TOKEN env var
```

## CLI Commands

```bash
# Translate
repo-translator translate --repo URL --source-lang zh --target-lang en

# Detect language of files
repo-translator detect --repo URL

# Review existing translation
repo-translator review --dir ./translated-repo --source-lang zh

# Push to GitHub
repo-translator push --dir ./translated-repo --repo username/repo-name
```

## Programmatic Usage

```python
from repo_translator import RepoTranslator

translator = RepoTranslator(
    source_lang="zh",
    target_lang="en",
    translator_engine="google",
    review_engine="openai",  # optional
    review_sample_rate=0.15,
)

# Full pipeline
translator.run(
    repo_url="https://github.com/user/repo",
    output_dir="./translated-repo",
    push_to="username/new-repo-name",
)

# Or step by step
translator.clone("https://github.com/user/repo", "./repo")
translator.translate("./repo", "./translated-repo")
report = translator.review("./translated-repo")
translator.push("./translated-repo", "username/new-repo-name")
```

## License

MIT
