# Implementation Plan: Translation Manifest Pipeline

## Overview

Convert the feature design into a series of prompts for a code-generation LLM that will implement
each step with incremental progress. Make sure that each prompt builds on the previous prompts, and
ends with wiring things together. There should be no hanging or orphaned code that isn't integrated
into a previous step. Focus ONLY on tasks that involve writing, modifying, or testing code.

This plan builds the durable manifest pipeline (EXTRACT → TRANSLATE-MANIFEST → APPLY → AUDIT →
VERIFY) as an additive layer on the existing `repo-translator` Python project. The
correctness-critical foundation (byte round-trip `Segment`, translation-key derivation, JSONL
manifest, hash-guarded descending-order applicator) comes first, extractors and integration follow.
Existing helpers are reused rather than reinvented: `PROTECTED_TOKEN_PATTERN`, `_has_cjk_ideograph`,
`detector.has_cjk`, `RepoTranslator._write_text_atomic`, `_validate_translated_content`,
`equivalence.verify_equivalence`, `get_translatable_files`, `_translate_preserving_tokens`,
`_translate_many`, `_replace_translation_markers`.

Implementation language: **Python** (matches the existing codebase and the design's code samples).

## Tasks

- [x] 1. Establish the Segment data model and its ID/key helpers
  - [x] 1.1 Create `repo_translator/segments.py` with the `Segment` dataclass, `SegmentKind`, and `ManifestHeader`
    - Define all Segment fields (`id`, `path`, `kind`, `start_byte`, `end_byte`, `line`, `column`, `source_text`, `target_text`, `file_sha256`, `source_sha256`, `translation_key`, `context_before`, `context_after`, `protected_context`)
    - `protected_context: list[str]` (default empty) records interpolation expressions preserved verbatim (e.g. `["${pack}"]`); informational only, never used by APPLY when splicing
    - Implement `to_json_line`/`from_json_line` (UTF-8, `ensure_ascii=False`) and the `is_translated` property
    - Enforce validation in construction: `end_byte > start_byte >= 0`, `sha256(source_text bytes) == source_sha256`, repo-relative POSIX path with no absolute/`..` traversal, `kind` is a `SegmentKind` member
    - Define `SegmentKind` members including `TEMPLATE_STRING_FRAGMENT = "template_string_fragment"`, and `ManifestHeader` dataclass fields (`type`, `version`, `source_lang`, `target_lang`, `repo_root`, `created_at`, `segment_count`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.9, 1.10, 1.12, 1.13, 2.7_

  - [x] 1.2 Implement `make_segment_id` and `make_translation_key` in `segments.py`
    - `make_translation_key(source_text, kind, *, context="")` → sha256 over parts joined by `\x1f`, appending context only when non-empty
    - `make_segment_id(path, start_byte, end_byte)` → 16-hex sha256 of position; reject invalid byte ranges and empty/whitespace-only source text with an error
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

  - [x] 1.3 Write property test for translation-key and segment-id derivation
    - **Property 1: Key determinism and differentiation** — identical (source, kind, context) yield identical keys; differing kind or context yields different keys; identical (path, start, end) yield identical ids; any differing component yields different ids
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.6, 3.7**

  - [x] 1.4 Implement `build_segment` with byte round-trip enforcement and the line-start byte table helper
    - Compute `line`/`column` from `file_bytes[:start_byte]` via a shared line-start byte table plus UTF-8 length of preceding chars on the line
    - Populate `context_before`/`context_after` (0–1000 chars, clamped at file edges), `source_sha256`, `file_sha256`, `translation_key` (context-sensitive per kind policy), and `id`
    - Accept optional `protected_context: list[str]` parameter (default empty) and pass through to the Segment
    - Assert `file_bytes[start_byte:end_byte].decode("utf-8") == source_text`; reject invalid ranges (`start_byte < 0`, `end_byte < start_byte`, `end_byte > len(file_bytes)`) and round-trip mismatch with errors, constructing no Segment
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [x] 1.5 Write property test for `build_segment` byte round-trip
    - **Property 2: Segment construction round-trip** — for generated files with ASCII and multi-byte CJK spans, `build_segment` yields `file_bytes[start:end].decode() == source_text` and correct line/column; invalid ranges raise
    - **Validates: Requirements 4.1, 4.5, 4.6**

  - [x] 1.6 Write unit tests for Segment validation and JSON escaping
    - Test rejection of bad byte ranges, absolute/`..` paths, invalid kind; test `is_translated` true/false
    - Test `to_json_line` emits verbatim non-ASCII (no `\uXXXX`)
    - _Requirements: 1.3, 1.6, 1.9, 1.10, 1.12, 2.3_

- [x] 2. Implement manifest serialization and translation memory
  - [x] 2.1 Create `repo_translator/manifest.py` with `Manifest` read/write and streaming
    - `open_for_write`/`append` (stream one JSONL line, header first) and `finalize` (rewrite header `segment_count`)
    - `read` (header + list) and `iter_segments` (streaming, at most one Segment in memory)
    - `created_at` as ISO-8601 UTC; raise a line-identifying error on malformed JSON or missing required fields during iteration
    - _Requirements: 2.1, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 2.2 Implement `rewrite_targets` atomic manifest rewrite in `manifest.py`
    - Fill `target_text` for given segment ids and rewrite atomically (temp-file + `os.replace`, mirroring `_write_text_atomic`)
    - _Requirements: 11.5, 11.7_

  - [x] 2.3 Write property test for manifest serialize/deserialize round-trip
    - **Property 3: Manifest round-trip fidelity** — a Segment serialized to a JSON line and deserialized recovers every field equal in value and type
    - **Validates: Requirements 2.2**

  - [x] 2.4 Implement `TranslationMemory`, `MemoryEntry`, `load_memory`, `save_memory`
    - Map `translation_key` → `MemoryEntry`; store only non-null `target_text` entries; missing/absent file → empty memory
    - Halt load with a file-identifying error on unparseable memory (leave file unchanged); atomic save that leaves prior file intact on failure
    - _Requirements: 12.1, 12.2, 12.3, 12.5, 12.6_

  - [x] 2.5 Write property test for translation-memory persistence round-trip
    - **Property 4: Memory persistence round-trip** — saving then reloading recovers `translation_key`, `source_text`, `target_text`, `kind`, and `hits` for every entry
    - **Validates: Requirements 12.4**

- [x] 3. Checkpoint - foundation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement the hash-guarded, descending-order applicator
  - [x] 4.1 Create `repo_translator/applicator.py` with `apply_manifest` and `ApplyStats`
    - Reject output aliasing source and reject non-empty existing output root; `shutil.copytree(symlinks=True)`
    - Per file: hash guard (`sha256(bytes) == segments[0].file_sha256`) — raise when `fail_on_source_mismatch` else record mismatch and leave copied-through file; reject overlapping segment ranges
    - Splice in strictly descending `start_byte`; per-segment source-text guard (raise on drift); replace bytes with `target_text` UTF-8, leave null targets unchanged; all-or-nothing per file
    - Validate structured files (`.json/.yaml/.yml/.toml/.py`, case-insensitive) via `_validate_translated_content` before atomic commit; skip replace and record failure if invalid; write via `_write_text_atomic`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10, 13.11, 13.12, 13.13, 14.1, 14.2, 14.3, 14.4_

  - [x] 4.2 Write property test for round-trip extract+apply identity
    - **Property 5: Identity round-trip** — for generated files with CJK spans, EXTRACT then APPLY with identity translation (`target_text == source_text`) reproduces the file byte-for-byte
    - **Validates: Requirements 13.10, 13.12**

  - [x] 4.3 Write property test for descending-order splice equivalence
    - **Property 6: Descending splice correctness** — with length-changing targets, descending-order splicing equals an independently computed reference splice; multiple CJK segments per line stay correct
    - **Validates: Requirements 13.7**

  - [x] 4.4 Write unit tests for APPLY guards
    - Hash guard raises on a mutated file; source-text drift raises; overlapping ranges raise; broken structured output is rejected leaving copied-through original; `skip-on-source-mismatch` leaves file and records mismatch
    - _Requirements: 13.5, 13.6, 13.8, 13.9, 14.3_

- [x] 5. Checkpoint - applicator
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement the extractor registry and EXTRACT stage
  - [x] 6.1 Create `repo_translator/extractors/base.py` with the `Extractor` protocol, `Candidate`, `ExtractionReport`, registry, and `extract_repo`
    - Define `Candidate` dataclass: wraps a `Segment` with `translatable: bool` and `reason: str` (extraction-time decision metadata, not persisted in manifest)
    - Define `ExtractionReport` dataclass: records skipped candidates (`path`, `start_byte`, `snippet`, `reason`) and files that used the lower-fidelity regex fallback
    - `get_extractor`/`register` mapping lowercased suffix → Extractor; route grammar-backed code files (`.js/.jsx/.ts/.tsx/.mjs/.rs/.go/.java/.kt/.c/.cpp/.h/.vue`) to Tree-sitter as PRIMARY, Python to the AST extractor; fall back to `regex_fallback.py` when no grammar applies or grammar/import fails, recording fallback files in ExtractionReport
    - `extract_repo` walks `get_translatable_files` in deterministic sorted order honoring include/exclude, picks an extractor per file, collects Candidates, yields only `translatable == True` Segments, gates on `_has_cjk_ideograph` when source is CJK, records `file_sha256`, leaves `target_text` null
    - Enforce no-overlapping-ranges within a path; skip unreadable/undecodable files with a recorded error and continue; populate ExtractionReport with all skipped candidates and fallback files
    - _Requirements: 1.8, 1.11, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 5.10, 8.1, 8.2, 8.8, 22.3, 22.4_

  - [x] 6.2 Write unit tests for registry selection, CJK gating, and ExtractionReport
    - Suffix-based selection routes grammar-backed files to Tree-sitter, Python to AST extractor; fallback when no structured extractor or grammar unavailable, recorded in ExtractionReport.fallback_files
    - CJK-only emission; unreadable-file skip; non-translatable candidates appear in ExtractionReport.skipped with correct reasons
    - _Requirements: 5.2, 5.3, 5.4, 5.5, 5.9, 5.10, 8.1, 8.2, 22.4_

  - [x] 6.3 Implement `extractors/markdown.py`
    - Emit Segments for headings, paragraphs, table cells, link labels with non-whitespace text; exclude fenced/inline code, URLs, link targets, template placeholders (reuse `PROTECTED_TOKEN_PATTERN` and `_translate_markdown` fence/table logic)
    - Locate runs in raw bytes (no reflow), assert round-trip before emit; omit+report unresolved runs; report unparseable file without terminating others
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 6.4 Write unit tests for Markdown extraction
    - Known fixture → expected offsets; negative cases (code fences, URLs, placeholders) emit no segments
    - _Requirements: 6.1, 6.2_

  - [x] 6.5 Implement `extractors/python.py`
    - AST docstrings + `tokenize` COMMENT tokens; under `--translate-code`, user-facing string literals; exclude identifiers, import paths, regexes, dict keys/format specs/comparison constants
    - Convert `(row, col)` to byte offsets via the shared line-start byte table; on syntax error emit no segments and report
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 6.6 Write unit tests for Python extraction
    - Docstrings/comments emitted; identifiers/imports/regex/keys excluded; syntax-error file emits nothing
    - _Requirements: 7.1, 7.3, 7.5, 7.6_

  - [x] 6.7 Implement `extractors/tree_sitter.py` — PRIMARY code extractor with template-fragment splitting and context-aware rules
    - **Primary extraction** for `.js .jsx .ts .tsx .mjs .rs .go .java .kt .c .cpp .h .vue` using `node.start_byte`/`node.end_byte` directly as Segment byte offsets
    - **Template-literal fragment splitting**: walk each `template_string` node's children; emit one `template_string_fragment` Segment per `string_fragment` child containing a Han ideograph (exact child byte range); NEVER emit `template_substitution` (`${...}`) nodes as translatable; record enclosing `${...}` expressions in the Segment's `protected_context`
    - **Context-aware translatability rule table**: produce a `Candidate` for every inspected node annotated with `translatable` and `reason`; translate comments (line/block/doc), jsx_text, string/fragment args to `console.*` calls, `throw new Error(...)` / error constructor args, UI attribute values (`placeholder`, `title`, `aria-label`, `alt`); do NOT translate import/module source strings, `fetch(...)` / route/router args, `className` values, `data-testid` / test-selector attrs, object property keys, machine-readable constants
    - **Guarded import with fallback**: on Tree-sitter unavailable OR per-file parse error, fall back to `regex_fallback.py` for that file and record a per-file lower-fidelity indicator in ExtractionReport
    - _Requirements: 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 21.1, 21.2, 21.3, 21.4, 22.1, 22.2, 22.3_

  - [x] 6.7b Create `extractors/regex_fallback.py` — explicit fallback extractor
    - Wrap the existing `extract_translatable_text` / `_translate_source_line` line-based logic as an `Extractor` implementation
    - Emit Candidates with `translatable=True` for lines with CJK; note this inherits the historical template-literal `$` limitation (documented, not fixed here — Tree-sitter handles the fix)
    - _Requirements: 5.3, 8.6, 8.8_

  - [x] 6.8 Write unit tests for Tree-sitter extraction: template fragments, context rules, and fallback
    - **Regression test (template-literal bug fix)**: `console.warn(`[decor] 装饰包 ${pack} manifest 校验失败:`)` yields exactly two `template_string_fragment` segments (`"[decor] 装饰包 "` and `" manifest 校验失败:"`) each with `protected_context == ["${pack}"]`; `${pack}` is NEVER emitted as a segment. This is the explicit regression test for the historical `QUOTED_STRING_PATTERN` `$`-exclusion bug.
    - **Context-rule positive cases**: comment → translatable; jsx_text → translatable; console.warn arg → translatable; `throw new Error("失败")` → translatable; `placeholder="请输入"` → translatable
    - **Context-rule negative cases**: `import x from "路径"` → NOT translatable (reason: "import/module source"); `fetch("/接口")` → NOT translatable (reason: "route/endpoint"); `className="容器"` → NOT translatable (reason: "className"); `data-testid="按钮"` → NOT translatable (reason: "test selector"); all surfaced in ExtractionReport.skipped
    - **Fallback path**: forced Tree-sitter unavailable → pipeline uses regex_fallback and marks ExtractionReport.fallback_files
    - _Requirements: 8.1, 8.4, 8.7, 8.8, 21.4, 22.1, 22.2_

  - [x] 6.9 Implement `extractors/structured_data.py`
    - JSON/YAML/TOML: emit only non-empty string scalar values (incl. nested), never keys/non-string scalars/empty; exclude URLs (`://`), path-like (`/` or `\`), enum-like tokens
    - Locate value bytes in original and confirm decode equals parsed value before emit; skip unlocatable/mismatched values
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x] 6.10 Write unit tests for structured-data extraction
    - Values emitted, keys/numbers/bools/null/empty excluded; URL/path/enum values excluded; unlocatable values skipped
    - _Requirements: 9.1, 9.2, 9.4_

  - [x] 6.11 Implement `extractors/markup.py`
    - HTML/XML/SVG via `xml.etree.ElementTree`: text nodes + UI attrs (`title`, `alt`, `aria-label`, `placeholder`) with non-whitespace; exclude SVG `id`/`#`-references (`xlink:href="#..."`, `url(#...)`) and `script`/`style` text
    - Locate node text at/after node start byte; exclude+report unlocatable text, continue
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x] 6.12 Write unit tests for markup extraction
    - Text nodes/UI attrs emitted; SVG ids/`#`-refs and script/style excluded; unlocatable text skipped
    - _Requirements: 10.1, 10.2, 10.3, 10.5_

- [x] 7. Checkpoint - extractors
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement the TRANSLATE-MANIFEST stage
  - [x] 8.1 Implement `translate_manifest` and `ManifestStats` in `manifest.py`
    - For each null-target segment: memory hit → reuse and increment `hits`; miss → batch by distinct `translation_key` (dedup within batch), translate, store entry with `hits=0`
    - Route span text through existing `_translate_preserving_tokens`; issue at most one provider request per distinct key and fan out; write targets back via `rewrite_targets`; persist memory
    - Idempotent: leave already-translated segments untouched, zero provider calls when all filled
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.8, 11.9, 12.4_

  - [x] 8.2 Wire provider error handling via existing `_translate_many` semantics
    - Batch count mismatch → hard error, no output for batch; batch error → retry once (2 attempts); retry failure → hard error propagated, no partial/passthrough output
    - _Requirements: 20.1, 20.2, 20.3_

  - [x] 8.3 Write unit tests for TRANSLATE-MANIFEST reuse, dedup, and idempotency
    - Memory hit reuses + increments hits; miss stores entry; one request per distinct key; second run makes zero provider calls; count-mismatch and retry-exhaustion raise
    - _Requirements: 11.1, 11.2, 11.3, 11.8, 20.1, 20.3_

- [x] 9. Implement the AUDIT stage
  - [x] 9.1 Create `repo_translator/audit.py` with `audit_repo` and `AuditReport`
    - Walk `get_translatable_files` honoring include/exclude; per line with a Han ideograph (reuse `detector.has_cjk`/`_has_cjk_ideograph`) emit a finding with path, 1-based line, snippet ≤200 chars; one finding per line
    - Skip unreadable/undecodable files recorded as unaudited; report totals (files with residual, total findings) and zero when clean
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [x] 9.2 Write unit tests for AUDIT
    - Multi-line residual → multiple findings; snippet cap; unreadable file recorded; clean repo reports zero
    - _Requirements: 15.2, 15.3, 15.4, 15.5_

- [x] 10. Checkpoint - stages complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Wire new CLI subcommands and VERIFY reuse
  - [x] 11.1 Add `extract`, `translate-manifest`, `apply`, and `audit` subcommands in `cli.py`
    - `extract` (repo, output-dir, source/target lang, translate-code toggle, include/exclude) → `extract_repo` + `Manifest`
    - `translate-manifest` (manifest, translator, api-key, translation-memory, batch-size with `IntRange(1,100)`) → `translate_manifest`
    - `apply` (manifest, repo, output-dir, fail/skip-on-source-mismatch) → `apply_manifest`
    - `audit` (dir, include/exclude) → `audit_repo`; reject missing required args and out-of-range batch size with error messages
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.8_

  - [x] 11.2 Confirm/wire the existing `verify` subcommand as the VERIFY stage
    - Invoke `equivalence.verify_equivalence(source, output)`; pass/fail against configured severity threshold; surface issues; fail with error on unreadable repos leaving both unmodified
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 17.7_

  - [x] 11.3 Write unit tests for CLI subcommand argument handling
    - Missing required arg rejected; batch size out of range rejected; each subcommand dispatches to its stage
    - _Requirements: 17.4, 17.8_

- [x] 12. Integrate manifest flags into the existing translate command and RepoTranslator
  - [x] 12.1 Add manifest flags to the `translate` command in `cli.py`
    - `--export-manifest`, `--apply-manifest`, `--translation-memory`, `--fail-on-source-mismatch/--skip-on-source-mismatch`, `--audit-untranslated`
    - Reject mutually-exclusive mismatch flags; abort non-zero on missing/invalid apply manifest before writing output
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8_

  - [x] 12.2 Add additive hooks to `RepoTranslator` in `core.py`
    - New optional `__init__` fields (`export_manifest_path`, `apply_manifest_path`, `translation_memory_path`, `fail_on_source_mismatch`, `audit_untranslated`) all defaulting to current behavior
    - `run` branches up front: apply mode → `apply_manifest` then existing review/verify/push; export mode → existing translate then persist manifest via `extract_repo`; default unchanged
    - Reuse `_translate_preserving_tokens`, `_translate_many`, `_replace_translation_markers`, `_write_text_atomic`, `_validate_translated_content`; abort before output on missing/invalid apply manifest; error without corrupting output on export write failure
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6_

  - [x] 12.3 Write integration test for the full five-stage run and fallback
    - Small synthetic repo (md + py + json + tsx with template literals containing `${...}`) through EXTRACT→TRANSLATE→APPLY→AUDIT→VERIFY: no residual CJK in translatable positions, structured files still parse, `verify_equivalence` reports no errors; template fragments extracted correctly; forced Tree-sitter-unavailable run still completes via regex fallback (ExtractionReport.fallback_files populated)
    - _Requirements: 5.6, 8.8, 15.5, 16.2, 19.1, 21.4_

  - [x] 12.4 Write regression test that default RepoTranslator behavior is unchanged
    - With no manifest option supplied, output equals pre-feature in-memory behavior
    - _Requirements: 19.1_

- [x] 13. Declare default and dev dependencies
  - [x] 13.1 Update `pyproject.toml`: add `tree-sitter` + `tree-sitter-language-pack` as DEFAULT runtime dependencies and `hypothesis` as a dev dependency
    - `tree-sitter` and `tree-sitter-language-pack` move from an optional extra to default runtime deps (they ship prebuilt wheels; this fixes the template-literal extraction bug out of the box); the guarded import in `tree_sitter.py` is retained as a safety net for missing/broken grammars
    - `hypothesis` stays under the dev extra for property-based tests
    - _Requirements: 8.6, 8.8, 8.9_

- [x] 14. Final checkpoint - ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (tests) and can be skipped for a faster MVP; core implementation
  tasks are never optional.
- Each task references specific requirement sub-clauses for traceability.
- Property tests (Properties 1–6) validate the universal correctness guarantees: key/id derivation,
  segment byte round-trip, manifest and memory persistence round-trips, identity round-trip, and
  descending-order splice equivalence. They use `hypothesis` (dev extra).
- Unit tests validate examples, negative cases, and edge conditions per stage.
- The correctness-critical foundation (Segment, keys, manifest, applicator) lands before extractors
  and integration so errors surface early.
- All work is additive; the existing `translate` command and in-memory path stay unchanged.
- Tree-sitter is a DEFAULT runtime dependency (prebuilt wheels); it is the PRIMARY code extractor
  for grammar-backed files. The regex fallback (task 6.7b) is retained purely as a safety net and
  inherits the historical template-literal `$` limitation. Task 6.8 includes an explicit regression
  test for the template-literal bug that motivated elevating Tree-sitter from optional to default.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.4"] },
    { "id": 2, "tasks": ["1.3", "1.5", "1.6", "2.1", "2.4"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.5", "4.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "6.1", "9.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "6.5", "6.7b", "6.9", "6.11", "8.1", "9.2"] },
    { "id": 6, "tasks": ["6.4", "6.6", "6.7", "6.10", "6.12", "8.2"] },
    { "id": 7, "tasks": ["6.8", "8.3", "11.1", "11.2"] },
    { "id": 8, "tasks": ["11.3", "12.1", "12.2", "13.1"] },
    { "id": 9, "tasks": ["12.3", "12.4"] }
  ]
}
```
