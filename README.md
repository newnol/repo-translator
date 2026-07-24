# repo-translator 🌐

**Translate CJK-heavy GitHub documentation and selected source files** while preserving
repository structure.

> Translating a full repo with LLMs costs $$$ (we burned ~$5+ on a single repo).  
> This tool uses **cheap/free translation APIs** for bulk work, then optionally uses **AI for quality review** — saving 95-99% in LLM costs.

## ✨ Features

- 🌐 **7 translation engines** — Google, DeepL, OpenAI-compatible, Ollama, LibreTranslate, MyMemory
- 🚄 **Native batch + multi-engine mode** — batch LibreTranslate requests and spread work across providers/endpoints
- 🔍 **Smart language detection** — line-by-line CJK detection for mixed-language files
- 📁 **Scoped translation** — repeatable `--include`/`--exclude` globs and docs-only default
- 🤖 **AI quality review** — spot-check sample with LLM for 95% cost savings
- 📐 **Conservative code mode** — choose comments only, or comments plus quoted/markup strings
- 📄 **40+ file types** — `.md`, `.py`, `.rs`, `.js`, `.ts`, `.go`, `.java`, `.html`, `.ui`, `.xml`, `.json`, `.yaml`, and more
- ✅ **Independent verification** — immutable source snapshot is compared with translated output
- 📤 **Safe Git push** — pushes `repo-translator/<language>` without force or stored tokens
- 🖥️ **CLI + Python API** — use as command line tool or import as library

## Architecture

```
┌──────────┐   ┌──────────┐   ┌────────────┐   ┌───────────┐   ┌──────────┐
│ Clone    │──▶│ Detect   │──▶│ Translate  │──▶│ AI Review │──▶│ Push to  │
│ Repo     │   │ Language │   │ (API bulk) │   │ (spot     │   │ GitHub   │
│          │   │ per file │   │            │   │  check)   │   │          │
└──────────┘   └──────────┘   └────────────┘   └───────────┘   └──────────┘
```

## 💰 Cost Comparison

| Method | 1000 files (~200K chars) |
|---|---|
| LLM (Claude/GPT for all) | $3-8 |
| **repo-translator** | **$0.01-0.10** |

## 🚀 Quick Start

### Install

```bash
git clone https://github.com/newnol/repo-translator.git
cd repo-translator
pip install -e .
```

### Translate a repo

```bash
# Chinese to English (free with Google Translate!)
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator google \
  --output-dir ./translated-repo

# With AI review (optional, cheap)
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator google \
  --review-with openai \
  --push-to your-username/new-repo-name

# Use DeepL for best quality
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator deepl \
  --output-dir ./translated-repo

# Use local Ollama (completely free, private)
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang ja \
  --target-lang en \
  --translator ollama
```

### Translate `retain-pdf` from Chinese to English

Start with the documentation-only pass. This avoids mutating Python, Rust, and
TypeScript while you validate terminology and translation quality:

```bash
repo-translator translate \
  --repo https://github.com/wxyhgk/retain-pdf.git \
  --source-lang zh \
  --target-lang en \
  --translator libre \
  --base-url http://localhost:5000 \
  --output-dir ./retain-pdf-en \
  --include README.md \
  --include 'backend/README.md' \
  --include 'doc/**/*.md' \
  --include 'docs/**/*.md' \
  --include 'frontend/**/README.md' \
  --include 'frontend-react/**/README.md' \
  --exclude 'doc/reference/**' \
  --workers 4 \
  --verify
```

After reviewing that output, translate selected UI source paths explicitly:

```bash
repo-translator translate \
  --repo https://github.com/wxyhgk/retain-pdf.git \
  --source-lang zh \
  --target-lang en \
  --translator libre \
  --base-url http://localhost:5000 \
  --output-dir ./retain-pdf-en \
  --translate-code \
  --code-scope comments \
  --include 'backend/**/*.py' \
  --include 'backend/rust_api/**/*.rs' \
  --include 'frontend/**/*.ts' \
  --include 'frontend/**/*.tsx' \
  --include 'frontend-react/**/*.ts' \
  --include 'frontend-react/**/*.tsx' \
  --exclude '**/tests/**' \
  --exclude '**/*.test.*' \
  --exclude '**/fixtures/**' \
  --workers 4 \
  --verify
```

Run `--dry-run` first if you want to inspect the selected file count without
calling a translation provider.

### Fast local translation with LibreTranslate

LibreTranslate accepts an array of strings in one `/translate` request. This
project uses that native batch API, so `--batch-size 40` translates up to 40
comment/prose spans per request instead of making one request for every line.

```bash
docker run -d --name libretranslate -p 5000:5000 libretranslate/libretranslate

repo-translator translate \
  --repo ./retain-pdf \
  --source-lang zh \
  --target-lang en \
  --translator libre \
  --base-url http://localhost:5000 \
  --translate-code \
  --code-scope comments \
  --workers 4 \
  --batch-size 40
```

For a host with enough CPU and RAM, start more than one LibreTranslate instance
and repeat the engine name. Each endpoint receives a share of every batch:

```bash
docker run -d --name libre-1 -p 5000:5000 libretranslate/libretranslate
docker run -d --name libre-2 -p 5001:5000 libretranslate/libretranslate

repo-translator translate \
  --repo ./retain-pdf \
  --source-lang zh \
  --target-lang en \
  --translator libre,libre \
  --base-url http://localhost:5000,http://localhost:5001 \
  --translate-code \
  --code-scope comments \
  --workers 4
```

Comma-separated providers also work, for example
`--translator libre,google-alt`. Results remain in input order even though the
provider batches run concurrently.

## 🌐 Translation Engines

| Engine | Cost | Quality | Speed | Best For |
|---|---|---|---|---|
| `google` | **Free** (500K chars/month) | Good | ⚡ Fast | Bulk translation, docs |
| `deepl` | $5.49/month (500K chars) | ⭐ Best | ⚡ Fast | Technical content |
| `openai` | ~$0.01/1K chars | Great | 🐢 Medium | Nuanced translation |
| `ollama` | **Free** (local) | Good | 🐢 Slow | Privacy-sensitive repos |
| `libre` | Depends on host | Good | ⚡ Fast | Self-hosted bulk translation |
| `mymemory` | Free tier | Good | ⚡ Fast | Small documentation sets |

## 🤖 AI Review

After bulk translation, optionally run LLM spot-check on a sample:

- ✅ Checks for untranslated foreign characters
- ✅ Validates technical term accuracy
- ✅ Reports formatting / code block issues
- ✅ Only reviews 10-20% of files → **massive cost savings**

```bash
# Review with OpenAI (samples 15% of files)
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh --target-lang en \
  --translator google \
  --review-with openai \
  --review-sample-rate 0.15

# Review existing translation
repo-translator review \
  --dir ./translated-repo \
  --source-lang zh \
  --reviewer openai
```

## 📁 Supported File Types

### Documentation & Config
- `.md`, `.rst`, `.txt`, `.adoc`
- `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`

### Source Code (comments & strings only)
- **Python**: `.py` — docstrings, comments, string literals
- **Rust**: `.rs` — doc comments, `//` comments, string literals
- **JavaScript/TypeScript**: `.js`, `.ts`, `.jsx`, `.tsx`
- **Go**: `.go` — comments, string literals
- **Java/Kotlin**: `.java`, `.kt`
- **C/C++**: `.c`, `.cpp`, `.h`, `.hpp`
- **Ruby/PHP/Perl**: `.rb`, `.php`, `.pl`
- **Shell**: `.sh`, `.bash`, `.zsh`
- **Lua/Elixir/Haskell**: `.lua`, `.ex`, `.exs`, `.hs`
- **R/Julia**: `.r`, `.jl`
- **SQL**: `.sql`
- **Dart/Swift**: `.dart`, `.swift`
- **Scala**: `.scala`

### Templates & Markup
- `.html`, `.htm`, `.xml`, `.svg`
- `.jinja2`, `.j2`, `.tmpl`, `.tpl`
- `.ui`, `.qml` (Qt UI files)
- `.proto`, `.graphql`, `.gql`

### Skip Patterns (by default)
- Binary files (images, PDFs, fonts, compiled files)
- Package directories (`node_modules/`, `target/`, `vendor/`, `.venv/`)
- Minified files (`.min.js`, `.min.css`)
- Lock files (`package-lock.json`, `Cargo.lock`, `poetry.lock`)
- Hidden directories (`.git/`, `.github/`, `.vscode/`)

## ⚙️ File selection

```bash
# Repeat --include and --exclude as needed.
repo-translator translate \
  --repo ./source-repo \
  --include 'README.md' \
  --include 'docs/**/*.md' \
  --exclude 'docs/archive/**'
```

Source code is excluded by default. Add `--translate-code` only for paths you
have selected and can validate with the target repository's own test suite.
Use `--code-scope comments` when the goal is to translate Chinese comments
without changing runtime strings. The default `comments-and-strings` mode also
translates quoted values and plain HTML/JSX text nodes.

## 🖥️ CLI Commands

```bash
# Translate a full repo
repo-translator translate \
  --repo https://github.com/user/repo \
  --source-lang zh \
  --target-lang en \
  --translator google

# Detect language of files in a repo
repo-translator detect --repo https://github.com/user/repo

# List available translation engines
repo-translator engines

# Review existing translation quality
repo-translator review \
  --dir ./translated-repo \
  --source-lang zh \
  --reviewer openai

# Push translated repo to GitHub
repo-translator push \
  --dir ./translated-repo \
  --repo username/new-repo-name
```

## 🐍 Programmatic Usage

```python
from repo_translator import RepoTranslator

translator = RepoTranslator(
    source_lang="zh",
    target_lang="en",
    translator_engine="google",
    review_engine="openai",  # optional
    review_sample_rate=0.15,
)

# Full pipeline (clone → translate → review → push)
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

## 🔧 How It Works

### 1. Clone & Detect
- Clones the target repo
- Scans all files for foreign language content (CJK, Arabic, etc.)
- Classifies files: translatable text vs binary vs code

### 2. Translate
- Extracts translatable text from each file
- For source code: extracts comments, with string literals enabled only by the selected code scope
- Uses native provider batches and optional multi-engine distribution
- Preserves indentation and formatting

### 3. AI Review (optional)
- Samples 10-20% of translated files
- LLM checks for: untranslated text, technical accuracy, formatting issues
- Auto-fixes common problems
- Generates quality report

### 4. Push
- Requires an existing destination repository
- Commits to `repo-translator/<target-language>`
- Pushes without force and never writes the token to `.git/config`

## 🤝 Contributing

Contributions welcome! Areas that need help:

- [ ] More translation engines (Yandex, Baidu, etc.)
- [ ] Better source code parsing (AST-based extraction)
- [ ] Parallel translation for speed
- [ ] Docker support
- [ ] GitHub Action integration
- [ ] Translation memory / glossary support
- [ ] Incremental translation (only changed files)

## 📄 License

MIT

## 🙏 Acknowledgments

Built after spending $5+ translating a single repo with LLMs. Never again.

---

**Made with ❤️ by [Newnol](https://github.com/newnol)**
