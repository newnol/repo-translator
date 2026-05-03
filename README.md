# repo-translator 🌐

**Translate entire GitHub repositories** from any language to another — cheaply and reliably.

> Translating a full repo with LLMs costs $$$ (we burned ~$5+ on a single repo).  
> This tool uses **cheap/free translation APIs** for bulk work, then optionally uses **AI for quality review** — saving 95-99% in LLM costs.

## ✨ Features

- 🌐 **5 translation engines** — Google (free), DeepL, OpenAI, Anthropic, Ollama
- 🔍 **Smart language detection** — line-by-line CJK detection for mixed-language files
- 📁 **Smart file filtering** — skip binary, node_modules, lock files automatically
- 🤖 **AI quality review** — spot-check sample with LLM for 95% cost savings
- 📐 **Indentation preservation** — source code indentation fully maintained
- 📄 **40+ file types** — `.md`, `.py`, `.rs`, `.js`, `.ts`, `.go`, `.java`, `.html`, `.ui`, `.xml`, `.json`, `.yaml`, and more
- 📤 **Push to GitHub** — full pipeline: clone → translate → review → push
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

## 🌐 Translation Engines

| Engine | Cost | Quality | Speed | Best For |
|---|---|---|---|---|
| `google` | **Free** (500K chars/month) | Good | ⚡ Fast | Bulk translation, docs |
| `deepl` | $5.49/month (500K chars) | ⭐ Best | ⚡ Fast | Technical content |
| `openai` | ~$0.01/1K chars | Great | 🐢 Medium | Nuanced translation |
| `anthropic` | ~$0.01/1K chars | Great | 🐢 Medium | Code-aware translation |
| `ollama` | **Free** (local) | Good | 🐢 Slow | Privacy-sensitive repos |

## 🤖 AI Review

After bulk translation, optionally run LLM spot-check on a sample:

- ✅ Checks for untranslated foreign characters
- ✅ Validates technical term accuracy
- ✅ Fixes formatting / code block issues
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

## ⚙️ Configuration

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
  - "*.ui"
  - "*.xml"

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
- For source code: extracts only comments and string literals (preserves code)
- Batches text to minimize API calls
- Preserves indentation and formatting

### 3. AI Review (optional)
- Samples 10-20% of translated files
- LLM checks for: untranslated text, technical accuracy, formatting issues
- Auto-fixes common problems
- Generates quality report

### 4. Push
- Creates new repo or updates existing one
- Commits all translated files
- Preserves git history structure

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
