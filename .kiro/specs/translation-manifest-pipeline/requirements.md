# Requirements Document

## Introduction

The Translation Manifest Pipeline adds a durable, byte-offset-precise manifest layer to the
existing `repo-translator` CLI as an additive mode alongside the current fast in-memory pipeline.
The work is split into five explicit stages — EXTRACT, TRANSLATE-MANIFEST, APPLY, AUDIT, and VERIFY
— connected by two on-disk artifacts: `translation-manifest.jsonl` (one segment per line) and
`translation-memory.json` (context-aware source-to-target reuse). Each segment records exact
UTF-8 byte offsets plus source-file and source-text hashes so that translations can be spliced back
with byte precision and refused whenever a file changed after extraction.

The two most important correctness guarantees are the hash guard (never apply a stale translation
to a file that changed since extract) and byte-offset splicing in descending order (editing a later
span never invalidates the offsets of an earlier span). The manifest layer reuses existing modules
for protected-token handling, structured-file validation, atomic writes, CJK detection, and
equivalence verification. The existing `translate` command and its default in-memory behavior remain
unchanged; new capabilities are opt-in.

## Glossary

- **Segment**: One translatable span located precisely in one source file, carrying byte offsets,
  hashes, kind, source text, optional target text, and a context-aware translation key.
- **Manifest**: A line-delimited JSON (JSONL) file (`translation-manifest.jsonl`) whose first line
  is a header object and whose subsequent lines are Segment objects.
- **ManifestHeader**: The first line of the Manifest, containing version, source/target languages,
  repo root, creation timestamp, and segment count.
- **Translation_Memory**: A JSON store (`translation-memory.json`) mapping a context-aware
  translation key to a memoized source-to-target entry, enabling reuse across segments and runs.
- **Translation_Key**: `sha256` over `source_text`, `kind`, and optional `context` joined by the
  unit-separator character (`\x1f`), used as the reuse key in the Translation_Memory.
- **Segment_Id**: A stable, position-based identifier derived from `sha256(path + start_byte +
  end_byte)`, truncated to 16 hex characters; identifies an occurrence, not a reuse key.
- **SegmentKind**: The enumerated category of a Segment (comment, docstring, string,
  template_string, template_string_fragment, jsx_text, ui_attribute, json_value, yaml_value,
  toml_value, markdown_heading, markdown_paragraph, markdown_table_cell, markdown_link_label,
  html_text, line_comment, block_comment, doc_comment).
- **template_string_fragment**: A SegmentKind for one `string_fragment` child of a template literal,
  extracted independently so interpolations (`${...}`) between fragments stay verbatim.
- **protected_context**: An optional, informational Segment field listing interpolation expressions
  (e.g. `["${pack}"]`) preserved verbatim near a span; never spliced by the Apply_Stage.
- **Candidate**: An extraction-time record wrapping a Segment with a `translatable` boolean and a
  `reason` string; only translatable Candidates become manifest Segments.
- **ExtractionReport**: An audit record of skipped Candidates (with reasons) and files that used the
  lower-fidelity regex fallback.
- **Extractor**: A per-file-type component that parses a file's bytes and returns Segments.
- **Extractor_Registry**: The registry mapping file suffixes to Extractors, with a regex-based
  fallback when no structured Extractor applies or a parser dependency is unavailable.
- **Extract_Stage**: The EXTRACT stage that walks translatable files and writes Segments to the
  Manifest.
- **Translate_Manifest_Stage**: The TRANSLATE-MANIFEST stage that fills each Segment's target text
  using the Translation_Memory and the translation provider.
- **Apply_Stage**: The APPLY stage (`apply_manifest`) that copies the source repo to an output repo
  and splices target text into files using byte offsets under hash guards.
- **Audit_Stage**: The AUDIT stage (`audit_repo`) that scans an output repo for residual Han
  ideographs.
- **Verify_Stage**: The existing VERIFY stage (`equivalence.verify_equivalence`), reused unchanged.
- **CLI**: The `repo-translator` command-line interface defined in `cli.py`.
- **RepoTranslator**: The existing orchestration class whose default in-memory behavior is preserved.
- **Hash_Guard**: The check that the current file's `sha256` equals the `file_sha256` recorded on
  its Segments at extract time.
- **Source_Text_Guard**: The per-segment check that `file_bytes[start_byte:end_byte]` decodes to the
  recorded `source_text`.
- **Han ideograph**: A CJK ideographic character, detected via the existing `detector.has_cjk` /
  `_has_cjk_ideograph` helpers.

## Requirements

### Requirement 1: Segment Data Model and Validation

**User Story:** As a developer, I want each translatable span represented as a fully validated Segment, so that translations can be located and spliced with byte precision.

#### Acceptance Criteria

1. THE Segment SHALL record `id`, `path`, `kind`, `start_byte`, `end_byte`, `line`, `column`, `source_text`, `target_text`, `file_sha256`, `source_sha256`, `translation_key`, `context_before`, `context_after`, and `protected_context`.
2. THE Segment SHALL store `start_byte` as an inclusive UTF-8 byte offset and `end_byte` as an exclusive UTF-8 byte offset such that `end_byte > start_byte >= 0`.
3. IF a Segment is constructed where `end_byte <= start_byte` or `start_byte < 0`, THEN THE Segment SHALL reject construction, SHALL NOT create the Segment instance, and SHALL signal a validation error indicating the invalid byte range.
4. THE Segment SHALL set `source_sha256` equal to `sha256` of the UTF-8 bytes of `source_text`.
5. THE Segment SHALL store `path` as a repo-relative POSIX-separated path that is neither absolute nor escaping the repo root.
6. IF a Segment `path` is absolute or contains a parent-directory (`..`) traversal, THEN THE Segment SHALL reject construction, SHALL NOT create the Segment instance, and SHALL signal a validation error indicating the invalid path.
7. THE Segment SHALL set `kind` to a member of SegmentKind.
8. WHILE extracting Segments belonging to the same `path`, THE Extract_Stage SHALL ensure that no two Segments have overlapping `[start_byte, end_byte)` ranges.
9. WHEN a Segment has `target_text` equal to null, THE Segment SHALL report `is_translated` as false.
10. WHEN a Segment has a non-null `target_text`, THE Segment SHALL report `is_translated` as true.
11. IF two Segments belonging to the same `path` have overlapping `[start_byte, end_byte)` ranges, THEN THE Extract_Stage SHALL reject the affected Segment set and signal an error indicating the overlapping ranges.
12. IF a Segment is constructed with a `kind` that is not a member of SegmentKind, THEN THE Segment SHALL reject construction, SHALL NOT create the Segment instance, and SHALL signal a validation error indicating the invalid kind.
13. THE Segment SHALL store `protected_context` as an optional list of interpolation expressions preserved verbatim, and THE Apply_Stage SHALL NOT use `protected_context` when splicing, treating it as informational only.

### Requirement 2: Manifest Serialization and Round-Trip

**User Story:** As a developer, I want the Manifest stored as line-delimited JSON, so that large repos can be streamed and appended without loading the whole file into memory.

#### Acceptance Criteria

1. THE Manifest SHALL write the ManifestHeader as the first line and each Segment as one subsequent line, encoding every line as a single UTF-8 JSON object terminated by a newline character (JSONL).
2. WHEN a Segment is serialized to a JSON line and then deserialized, THE Manifest SHALL produce a Segment whose every field is equal in value and type to the corresponding field of the original Segment.
3. THE Manifest SHALL serialize Segment content as UTF-8 without emitting `\uXXXX` escape sequences for non-ASCII characters, so that non-ASCII source text appears verbatim in the JSONL line.
4. WHEN `open_for_write` is called, THE Manifest SHALL append each Segment as a single streamed JSONL line without rewriting previously written lines.
5. WHEN `finalize` is called, THE Manifest SHALL rewrite the ManifestHeader `segment_count` to equal the exact number of Segment lines written.
6. THE Manifest SHALL provide a streaming reader that yields Segments one at a time, holding at most one Segment in memory at any point during iteration.
7. THE ManifestHeader SHALL record `version`, `source_lang`, `target_lang`, `repo_root`, `created_at` (as an ISO 8601 timestamp in UTC), and `segment_count`.
8. IF a line consumed by the streaming reader is not a well-formed JSON object or is missing a required Segment field, THEN THE Manifest SHALL raise an error indicating which line failed and SHALL NOT yield a partial or malformed Segment.

### Requirement 3: Context-Aware Translation Key Derivation

**User Story:** As a developer, I want reuse keyed on source text plus kind plus optional context, so that the same word can resolve differently across UI, database, and documentation.

#### Acceptance Criteria

1. THE Translation_Key SHALL be the `sha256` digest computed over `source_text`, `kind`, and, when a non-empty `context` is provided, `context`, joined in that order by the unit-separator character (`\x1f`).
2. WHEN two spans have identical `source_text`, `kind`, and `context`, THE Translation_Key derivation SHALL produce identical keys.
3. WHEN two spans share `source_text` but differ in `kind`, THE Translation_Key derivation SHALL produce different keys.
4. WHEN two spans share `source_text` and `kind` but differ in `context`, THE Translation_Key derivation SHALL produce different keys.
5. THE Segment_Id SHALL be deterministically derived from `path`, `start_byte`, and `end_byte` such that the same three input values always yield the same identifier across separate invocations of the derivation.
6. WHEN two spans have identical `path`, `start_byte`, and `end_byte`, THE Segment_Id derivation SHALL produce identical identifiers.
7. WHEN two spans differ in any one of `path`, `start_byte`, or `end_byte`, THE Segment_Id derivation SHALL produce different identifiers.
8. WHERE a SegmentKind is designated context-sensitive by policy, THE Extract_Stage SHALL include `context` in the Translation_Key derivation.
9. WHERE a SegmentKind is not designated context-sensitive by policy, THE Extract_Stage SHALL omit `context` from the Translation_Key derivation, and an empty or absent `context` value SHALL be treated identically as omitted.
10. IF `start_byte` is negative, or `end_byte` is negative, or `start_byte` is greater than or equal to `end_byte`, THEN THE Segment_Id derivation SHALL reject the input without producing an identifier and SHALL return an error indicating an invalid byte range.
11. IF `source_text` is empty or contains only whitespace, THEN THE Extract_Stage SHALL reject the span without producing a Translation_Key and SHALL return an error indicating invalid source text.

### Requirement 4: Segment Construction Invariant

**User Story:** As a developer, I want segment construction to enforce the byte round-trip invariant, so that APPLY can rely on offsets matching the recorded source text.

#### Acceptance Criteria

1. WHEN `build_segment` is called with `file_bytes`, `start_byte`, and `end_byte`, THE Segment SHALL satisfy `file_bytes[start_byte:end_byte].decode("utf-8")` equal to `source_text`.
2. WHEN `build_segment` is called, THE Segment SHALL compute `line` as the 1-based line and `column` as the 0-based column corresponding to `start_byte`.
3. WHEN `build_segment` is called, THE Segment SHALL populate `context_before` with the configured number of characters (0 to 1000, inclusive) immediately preceding `start_byte`, using all available preceding characters when fewer than the configured number exist before the start of the file.
4. WHEN `build_segment` is called, THE Segment SHALL populate `context_after` with the configured number of characters (0 to 1000, inclusive) immediately following `end_byte`, using all available following characters when fewer than the configured number exist before the end of the file.
5. IF `file_bytes[start_byte:end_byte]` does not decode to the provided `source_text`, THEN `build_segment` SHALL reject the input by raising an error indicating the byte-range/source-text mismatch and SHALL NOT construct a Segment.
6. IF `start_byte` is negative, `end_byte` is less than `start_byte`, or `end_byte` exceeds the length of `file_bytes`, THEN `build_segment` SHALL reject the input by raising an error indicating the invalid byte range and SHALL NOT construct a Segment.
7. WHEN `build_segment` converts a character `(row, column)` position to a byte offset, THE Extract_Stage SHALL use a line-start byte table plus the UTF-8 byte length of the preceding characters on that line.

### Requirement 5: EXTRACT Stage and Extractor Registry

**User Story:** As a user, I want the EXTRACT stage to walk translatable files and emit Segments, so that I have a durable record of what will be translated and where it lives.

#### Acceptance Criteria

1. WHEN the Extract_Stage runs, THE Extract_Stage SHALL walk the translatable files returned by the existing `get_translatable_files` traversal in deterministic sorted order honoring include and exclude patterns.
2. WHEN the Extract_Stage processes a file, THE Extractor_Registry SHALL select an Extractor based on the lowercased file suffix.
3. IF no structured Extractor applies to a file or a required parser dependency is unavailable, THEN THE Extractor_Registry SHALL fall back to the existing regex-based line extractor and SHALL record that file in the ExtractionReport as having used the lower-fidelity regex fallback.
4. WHERE the source language is CJK, THE Extract_Stage SHALL emit Segments only for spans containing at least one Han ideograph.
5. WHERE the source language is CJK, THE Extract_Stage SHALL skip and emit no Segment for any span lacking a Han ideograph.
6. THE Extract_Stage SHALL guarantee that for every emitted Segment, `file_bytes[start_byte:end_byte].decode("utf-8")` equals `source_text`.
7. WHEN the Extract_Stage emits a Segment, THE Segment SHALL record `file_sha256` as the `sha256` of the whole source file bytes read at extract time.
8. WHEN the Extract_Stage emits a Segment, THE Segment `target_text` SHALL be null.
9. IF a file cannot be read or cannot be decoded as UTF-8, THEN THE Extract_Stage SHALL skip that file, emit no Segments for it, record an error indication, and continue the traversal.
10. WHEN the Extract_Stage completes, THE Extract_Stage SHALL produce an ExtractionReport recording each skipped Candidate with its path, offset, snippet, and reason, and each file that used the lower-fidelity regex fallback.

### Requirement 6: Markdown Extraction

**User Story:** As a user, I want Markdown extracted at the prose level, so that documentation is translated without corrupting code or links.

#### Acceptance Criteria

1. WHEN a Markdown file is extracted, THE Extractor SHALL emit one Segment for each heading, paragraph, table cell, and link label whose translatable text contains at least one non-whitespace character.
2. THE Markdown Extractor SHALL exclude fenced code blocks, inline code, URLs, link targets, and template placeholders from emitted Segments, so that no bytes belonging to those constructs appear within any emitted Segment's text range.
3. WHEN the Markdown Extractor locates a translatable run, THE Extractor SHALL compute the Segment's start and end byte offsets by locating the run within the raw file bytes rather than reflowing text, such that the bytes at the emitted offset range are byte-identical to the run's source text.
4. IF the Markdown Extractor cannot locate a translatable run within the raw file bytes, THEN THE Extractor SHALL omit that run from emitted Segments and report an error indicating the run could not be resolved to byte offsets, while continuing to extract remaining runs.
5. IF a Markdown file cannot be parsed, THEN THE Extractor SHALL emit no Segments for that file and report an error indicating the file could not be parsed, without terminating extraction of other files.

### Requirement 7: Python Extraction

**User Story:** As a user, I want Python extracted via AST and tokenize, so that comments, docstrings, and optionally user-facing strings are captured without touching code identifiers.

#### Acceptance Criteria

1. WHEN a Python file is successfully parsed by the AST, THE Python Extractor SHALL emit one Segment for each docstring located via the AST.
2. WHERE the translate-code option is enabled, THE Python Extractor SHALL additionally emit one Segment for each user-facing string literal, where a user-facing string literal is any string literal that is not excluded under criterion 3.
3. THE Python Extractor SHALL exclude identifiers, import paths, regular expressions, and machine-readable constants (string literals used as dictionary keys, format specifiers, or comparison targets against fixed values) from emitted Segments.
4. WHEN the Python Extractor converts a token `(row, column)` position to a byte offset, THE Extractor SHALL use a precomputed line-start byte table.
5. WHEN a Python file is extracted, THE Python Extractor SHALL emit one Segment for each comment token located via tokenize.
6. IF a Python file cannot be parsed by the AST because of a syntax error, THEN THE Python Extractor SHALL emit no docstring or string-literal Segments for that file, leave the source file unchanged, and report an error indicating that the file could not be parsed.

### Requirement 8: Tree-sitter Primary Code Extraction with Regex Fallback

**User Story:** As a user, I want grammar-backed code files extracted via Tree-sitter as the primary extractor, so that I get high-fidelity structural spans out of the box while regex is reserved for narrow roles and fallback.

#### Acceptance Criteria

1. THE Extractor_Registry SHALL route grammar-backed code files (`.js`, `.jsx`, `.ts`, `.tsx`, `.mjs`, `.rs`, `.go`, `.java`, `.kt`, `.c`, `.cpp`, `.h`, `.vue`) to the Tree-sitter Extractor as the primary extractor.
2. THE Extractor_Registry SHALL route Python files to the existing AST/tokenize extractor and SHALL NOT route Python files to the Tree-sitter Extractor.
3. THE Tree-sitter Extractor SHALL set each Segment `start_byte` and `end_byte` from the extracted node byte range expressed as UTF-8 byte offsets measured from byte 0 of the file, where `start_byte` is inclusive and `end_byte` is exclusive.
4. WHEN a JS/TS/TSX file is extracted, THE Tree-sitter Extractor SHALL emit Segments for comment, string, template fragment, and jsx_text nodes and for the UI attribute values `title`, `placeholder`, `aria-label`, and `alt`.
5. WHEN a Rust file is extracted, THE Tree-sitter Extractor SHALL emit Segments for line comments, block comments, doc comments, and user-facing UI, log, and error string literals.
6. THE Tree-sitter Extractor SHALL use regex only for (a) Han-ideograph detection on node text and (b) fallback extraction when no grammar applies or the grammar or its import fails.
7. THE Tree-sitter Extractor SHALL exclude import paths, technical object keys, CSS class names, route and endpoint strings, and test selectors from emitted Segments.
8. IF a grammar is unavailable or import or parse fails for a file, THEN THE Extractor_Registry SHALL fall back to the regex extractor for that file and SHALL record a per-file lower-fidelity indicator in the ExtractionReport.
9. THE feature SHALL declare `tree-sitter` and `tree-sitter-language-pack` as default runtime dependencies.

### Requirement 9: Structured Data Extraction

**User Story:** As a user, I want JSON/YAML/TOML extracted at the value level, so that configuration values are translated without breaking keys or endpoints.

#### Acceptance Criteria

1. WHEN a JSON, YAML, or TOML file is extracted, THE Structured Data Extractor SHALL emit Segments only for non-empty string scalar values, including string values nested within arrays and objects, and SHALL NOT emit Segments for keys, for non-string scalars (numbers, booleans, and null), or for empty or whitespace-only string values.
2. THE Structured Data Extractor SHALL exclude from emitted Segments any string value that is a URL (a value whose text contains a URI scheme delimiter `://`), a filesystem or route path (a value containing a `/` or `\` path separator), or an enum-like token (a whitespace-free value composed solely of letters, digits, underscores, hyphens, and dots).
3. WHEN the Structured Data Extractor locates a string value, THE Extractor SHALL locate the value's bytes within the original file bytes and confirm that `file_bytes[start_byte:end_byte]` decodes to the parsed value before emitting a Segment.
4. IF the Structured Data Extractor cannot locate a value's bytes within the original file bytes, or the located bytes do not decode to the parsed value, THEN THE Extractor SHALL skip that value and emit no Segment for it.

### Requirement 10: Markup Extraction

**User Story:** As a user, I want HTML/XML/SVG extracted at the text-node and UI-attribute level, so that visible content is translated without breaking internal references.

#### Acceptance Criteria

1. WHEN an HTML, XML, or SVG file is extracted, THE Markup Extractor SHALL emit one Segment for each text node that contains at least one non-whitespace character and one Segment for each of the UI attributes `title`, `alt`, `aria-label`, and `placeholder` whose value contains at least one non-whitespace character.
2. THE Markup Extractor SHALL exclude SVG internal identifiers, including `id` attributes and `#`-references such as `xlink:href="#..."` and `url(#...)`, from emitted Segments.
3. THE Markup Extractor SHALL exclude the text content of `script` and `style` elements from emitted Segments.
4. WHEN the Markup Extractor locates a node's text, THE Extractor SHALL compute byte offsets by locating the first occurrence of that text within the raw file bytes at or after the byte offset of the node's start position.
5. IF the Markup Extractor cannot locate a node's text within the raw file bytes, THEN THE Extractor SHALL exclude that node from emitted Segments and record an error indicating that the text could not be located, while continuing extraction of the remaining nodes.

### Requirement 11: TRANSLATE-MANIFEST Stage

**User Story:** As a user, I want the TRANSLATE-MANIFEST stage to fill target text using memory and the provider, so that translations are reused, deduplicated, and resumable.

#### Acceptance Criteria

1. WHEN the Translate_Manifest_Stage processes a Segment whose `target_text` is null and whose `translation_key` exists in the Translation_Memory, THE Translate_Manifest_Stage SHALL reuse the memory entry's target text and increment that entry's `hits`.
2. WHEN the Translate_Manifest_Stage processes a Segment whose `target_text` is null and whose `translation_key` is absent from the Translation_Memory, THE Translate_Manifest_Stage SHALL translate the source text via the provider, store the result in the Translation_Memory with a `hits` value of zero, and record the target text.
3. WHEN the Translate_Manifest_Stage batches segments for translation with a batch size in the range 1 through 100 inclusive, THE Translate_Manifest_Stage SHALL issue at most one provider request per distinct `translation_key` in the batch and fan the result out to every Segment sharing that key.
4. WHEN the Translate_Manifest_Stage translates span text, THE Translate_Manifest_Stage SHALL preserve protected technical tokens by routing text through the existing `_translate_preserving_tokens` logic.
5. WHEN the Translate_Manifest_Stage completes, THE Translate_Manifest_Stage SHALL write the resolved `target_text` back into the Manifest for every Segment resolved via memory reuse or provider translation.
6. WHEN the Translate_Manifest_Stage completes, THE Translation_Memory SHALL contain an entry for every newly translated `translation_key`.
7. WHEN the Translate_Manifest_Stage rewrites the Manifest, THE Translate_Manifest_Stage SHALL use an atomic temp-file plus replace pattern such that the Manifest on disk is always either the complete prior file or the complete rewritten file, never partial.
8. WHEN the Translate_Manifest_Stage runs and every Segment already has non-null `target_text`, THE Translate_Manifest_Stage SHALL perform zero provider calls.
9. WHEN the Translate_Manifest_Stage processes a Segment that already has non-null `target_text`, THE Translate_Manifest_Stage SHALL leave that Segment unchanged and SHALL make no provider call for it.

### Requirement 12: Translation Memory Load and Save

**User Story:** As a user, I want the translation memory persisted and reloaded, so that reuse carries across runs.

#### Acceptance Criteria

1. THE Translation_Memory SHALL map each `translation_key` to a MemoryEntry whose `translation_key` equals the map key.
2. THE Translation_Memory SHALL store only entries whose `target_text` is non-null.
3. IF the memory file path is absent or the file is missing, THEN THE Translate_Manifest_Stage SHALL treat the Translation_Memory as empty (zero entries).
4. WHEN the Translation_Memory is saved and then reloaded, THE Translate_Manifest_Stage SHALL recover, for every MemoryEntry, `translation_key`, `source_text`, `target_text`, and `kind` values identical to their values at save time, and a `hits` value equal to its value at save time.
5. IF the memory file exists but cannot be parsed as a valid Translation_Memory store, THEN THE Translate_Manifest_Stage SHALL halt the load with an error indication identifying the offending file and SHALL leave the existing memory file unchanged.
6. IF a save operation fails before completion, THEN THE Translate_Manifest_Stage SHALL leave any previously persisted memory file unchanged.

### Requirement 13: APPLY Stage Hash Guard and Byte Splicing

**User Story:** As a user, I want APPLY to splice translations by byte offset under hash guards, so that stale translations are never applied to a changed file and offsets stay valid.

#### Acceptance Criteria

1. WHEN the Apply_Stage begins, THE Apply_Stage SHALL copy the source repo to the output repo, preserving symlinks.
2. IF the output root resolves to the same path as the source root, THEN THE Apply_Stage SHALL reject the operation as invalid.
3. IF the output root already exists and is non-empty, THEN THE Apply_Stage SHALL reject the operation and signal an error indicating the output root is not empty.
4. WHEN the Apply_Stage processes a file, THE Apply_Stage SHALL compute the current file `sha256` and compare it to the `file_sha256` recorded on the file's Segments.
5. WHERE fail-on-source-mismatch is enabled AND a file's current `sha256` does not equal the recorded `file_sha256`, THE Apply_Stage SHALL raise an error identifying the file and SHALL leave that file's copied-through content unchanged.
6. WHERE fail-on-source-mismatch is disabled AND a file's current `sha256` does not equal the recorded `file_sha256`, THE Apply_Stage SHALL leave that file's copied-through content unchanged and record a mismatch.
7. WHEN the Apply_Stage splices a file's Segments, THE Apply_Stage SHALL process the Segments in strictly descending `start_byte` order.
8. IF two Segments of a file have overlapping `[start_byte, end_byte)` ranges, THEN THE Apply_Stage SHALL raise an error and leave that file's copied-through content unchanged.
9. IF `file_bytes[start_byte:end_byte]` is not byte-equal to the UTF-8 encoding of a Segment's `source_text` even though the file hash matched, THEN THE Apply_Stage SHALL leave that file's copied-through content unchanged and raise an error identifying the drifted Segment.
10. WHEN a Segment has a non-null `target_text` and all of the file's guards pass, THE Apply_Stage SHALL replace the bytes at `[start_byte, end_byte)` with the UTF-8 bytes of `target_text`.
11. WHEN a Segment's `target_text` is null, THE Apply_Stage SHALL leave the corresponding bytes unchanged.
12. THE Apply_Stage SHALL either splice all of a file's guarded Segments successfully or leave that file with its copied-through content unchanged, never partially applied.
13. WHEN the Apply_Stage writes a spliced file, THE Apply_Stage SHALL use the existing atomic temp-file plus replace write pattern.

### Requirement 14: APPLY Stage Structured-File Validation

**User Story:** As a user, I want structured output files validated before commit, so that a translation never produces an unparseable file.

#### Acceptance Criteria

1. WHEN the Apply_Stage produces spliced content for a file whose extension is `.json`, `.yaml`, `.yml`, `.toml`, or `.py` (extension matched case-insensitively), THE Apply_Stage SHALL validate the spliced content by parsing or compiling it with the parser corresponding to that extension using the existing `_validate_translated_content` logic before committing the file.
2. IF the spliced structured content parses or compiles successfully, THEN THE Apply_Stage SHALL commit the validated content via the atomic replace.
3. IF the spliced structured content fails to parse or compile, THEN THE Apply_Stage SHALL skip the atomic replace, leave the copied-through original file in place unchanged, and record a per-file result indicating that structured validation failed for that file.
4. WHEN the Apply_Stage produces spliced content for a file whose extension is not one of `.json`, `.yaml`, `.yml`, `.toml`, or `.py`, THE Apply_Stage SHALL commit the content without performing structured-file validation.

### Requirement 15: AUDIT Stage

**User Story:** As a user, I want AUDIT to report residual CJK, so that I can see what slipped through translation.

#### Acceptance Criteria

1. WHEN the Audit_Stage runs, THE Audit_Stage SHALL walk the translatable files in the output repo returned by the existing `get_translatable_files` traversal, honoring the supplied include and exclude patterns.
2. WHEN the Audit_Stage encounters a line in a translatable file that contains at least one Han ideograph, THE Audit_Stage SHALL emit one finding recording the repo-relative file path, the 1-based line number, and a residual snippet of up to 200 characters drawn from that line and containing the detected Han ideograph.
3. WHEN a translatable file contains Han ideographs on multiple lines, THE Audit_Stage SHALL emit a separate finding for each such line.
4. IF a translatable file cannot be read or cannot be decoded as UTF-8, THEN THE Audit_Stage SHALL skip scanning that file, record it in the report as unaudited with an indication of the read failure, and continue auditing the remaining files without terminating.
5. WHEN the Audit_Stage completes, THE Audit_Stage SHALL produce a report that states the total count of files containing residual Han ideographs and the total count of residual findings, and that lists each finding from criterion 2; and WHEN no translatable file contains a Han ideograph, THE report SHALL state a residual finding count of zero.

### Requirement 16: VERIFY Stage Reuse

**User Story:** As a user, I want VERIFY to reuse the existing equivalence check, so that structural and build invariants are validated without new code.

#### Acceptance Criteria

1. WHEN the Verify_Stage runs, THE Verify_Stage SHALL invoke the existing `equivalence.verify_equivalence` function with the source repository and the output repository as inputs and SHALL obtain the resulting equivalence report.
2. WHEN the equivalence report is obtained and it contains zero issues at or above the configured failure severity threshold (one of: never, info, warning, error), THE Verify_Stage SHALL mark the stage outcome as passed.
3. IF the equivalence report contains one or more issues at or above the configured failure severity threshold, THEN THE Verify_Stage SHALL mark the stage outcome as failed and SHALL surface the report issues identifying each affected file and the reason for each issue.
4. IF the source repository or the output repository path does not exist or cannot be read, THEN THE Verify_Stage SHALL mark the stage outcome as failed and SHALL produce an error indication identifying the unreadable repository, while leaving both the source and output repositories unmodified.

### Requirement 17: New CLI Subcommands

**User Story:** As a user, I want dedicated CLI subcommands for each stage, so that I can run the pipeline in low-risk waves.

#### Acceptance Criteria

1. THE CLI SHALL provide an `extract` subcommand accepting repo, output directory, source language, target language, translate-code toggle, and include and exclude patterns.
2. THE CLI SHALL provide a `translate-manifest` subcommand accepting manifest path, translator, API key, translation-memory path, and batch size.
3. WHEN a batch size within the range 1 through 100 inclusive is supplied to `translate-manifest`, THE CLI SHALL use that value as the batch size for the translation run.
4. IF a batch size outside the range 1 through 100 inclusive is supplied to `translate-manifest`, THEN THE CLI SHALL reject the command and terminate without performing any translation, providing an error message indicating the valid batch size range.
5. THE CLI SHALL provide an `apply` subcommand accepting manifest path, source repo, output directory, and a fail-on-source-mismatch toggle.
6. THE CLI SHALL provide an `audit` subcommand accepting a directory and include and exclude patterns.
7. THE CLI SHALL retain the existing `verify` subcommand unchanged as the VERIFY stage.
8. IF a required argument for any subcommand is omitted, THEN THE CLI SHALL reject the invocation and terminate without executing the stage, providing an error message indicating which required argument is missing.

### Requirement 18: New Flags on the Existing translate Command

**User Story:** As a user, I want manifest flags on the existing translate command, so that I can emit or consume a manifest without leaving the fast path.

#### Acceptance Criteria

1. WHERE `--export-manifest` is supplied, THE CLI SHALL run the normal in-memory translation and write a manifest artifact containing the translated targets for the run.
2. WHERE `--apply-manifest` is supplied, THE CLI SHALL skip live translation and splice targets from the existing manifest via the Apply_Stage.
3. WHERE `--translation-memory` is supplied, THE CLI SHALL load the reuse store before translation begins and persist any updates to the store after the run completes.
4. WHERE `--fail-on-source-mismatch` is supplied AND the Apply_Stage detects a source-hash mismatch on any entry, THE CLI SHALL abort with a non-zero exit status and an error indicating the mismatched entry, without writing output.
5. WHERE `--audit-untranslated` is supplied, THE CLI SHALL run the Audit_Stage after writing output and report the count and location of each residual CJK character remaining in the output.
6. WHERE `--skip-on-source-mismatch` is supplied AND the Apply_Stage detects a source-hash mismatch on an entry, THE CLI SHALL skip that entry, retain its original source text, and continue processing the remaining entries.
7. IF `--apply-manifest` is supplied AND the referenced manifest is missing or cannot be parsed, THEN THE CLI SHALL abort with a non-zero exit status and an error indicating the manifest is missing or invalid, without writing output.
8. IF both `--fail-on-source-mismatch` and `--skip-on-source-mismatch` are supplied, THEN THE CLI SHALL abort with a non-zero exit status and an error indicating the two flags are mutually exclusive.

### Requirement 19: Additive Integration with RepoTranslator

**User Story:** As a maintainer, I want the manifest layer to be strictly additive, so that the existing in-memory pipeline behaves exactly as before.

#### Acceptance Criteria

1. WHERE no manifest option is supplied, THE RepoTranslator SHALL execute its existing in-memory translation path and produce output equivalent to the behavior prior to this feature.
2. WHERE an apply-manifest path is supplied, THE RepoTranslator SHALL invoke the Apply_Stage instead of live translation and then continue into the existing review, verify, and push steps in that order.
3. WHERE an export-manifest path is supplied, THE RepoTranslator SHALL run its existing translation path and, after translation completes, persist a manifest artifact.
4. THE RepoTranslator SHALL reuse the existing `_translate_preserving_tokens`, `_translate_many`, `_replace_translation_markers`, `_write_text_atomic`, and `_validate_translated_content` helpers rather than duplicating them.
5. IF an apply-manifest path is supplied but the manifest is missing or invalid, THEN THE RepoTranslator SHALL abort before modifying any output and signal an error identifying the manifest problem.
6. IF an export-manifest artifact cannot be written, THEN THE RepoTranslator SHALL signal an error identifying the write failure without corrupting the translated output already produced.

### Requirement 20: Provider Error Handling

**User Story:** As a user, I want provider errors handled consistently, so that translation failures are surfaced rather than silently producing wrong output.

#### Acceptance Criteria

1. IF the translation provider returns a batch whose result count does not equal the number of inputs sent in that batch, THEN THE Translate_Manifest_Stage SHALL abort translation with a hard error (reusing existing `_translate_many` semantics), surface an error indication identifying the count mismatch to the caller, and produce no translated output for that batch.
2. IF a provider batch call raises an error during translation, THEN THE Translate_Manifest_Stage SHALL retry that batch exactly once (2 total attempts) before treating it as failed, reusing existing `_translate_many` retry semantics.
3. IF the single retry of a failed provider batch call also raises an error, THEN THE Translate_Manifest_Stage SHALL abort translation with a hard error that propagates to the caller and SHALL NOT emit partial, substituted, or original-passthrough output for the affected batch.

### Requirement 21: Template-Literal Fragment Extraction and Interpolation Protection

**User Story:** As a user, I want template literals split into fragments with interpolations preserved, so that CJK inside interpolated template strings is translated without corrupting `${...}` expressions.

#### Acceptance Criteria

1. WHEN the Tree-sitter Extractor processes a `template_string` node, THE Tree-sitter Extractor SHALL emit one `template_string_fragment` Segment for each `string_fragment` child containing at least one Han ideograph, using that fragment's exact byte range.
2. THE Tree-sitter Extractor SHALL NOT emit any `template_substitution` (`${...}`) node as a translatable Segment.
3. WHEN the Tree-sitter Extractor emits a `template_string_fragment` Segment, THE Segment SHALL record the interpolation expressions of its enclosing template literal in `protected_context`.
4. WHEN the Tree-sitter Extractor processes `` console.warn(`[decor] 装饰包 ${pack} manifest 校验失败:`) ``, THE Tree-sitter Extractor SHALL emit exactly two `template_string_fragment` Segments (`"[decor] 装饰包 "` and `" manifest 校验失败:"`) and SHALL NOT emit a Segment for `${pack}`.

### Requirement 22: Context-Aware Translatability of Code Strings

**User Story:** As a user, I want string translatability decided by syntactic context, so that only user-facing text is translated and machine-readable strings are left intact.

#### Acceptance Criteria

1. THE Tree-sitter Extractor SHALL treat as translatable: comments (line, block, and doc), jsx_text nodes, string and fragment arguments to `console.*` calls, string and fragment arguments to `throw new Error(...)` and error constructors, and the UI attribute values `placeholder`, `title`, `aria-label`, and `alt`.
2. THE Tree-sitter Extractor SHALL treat as not translatable: import specifier and module source strings, string arguments to `fetch(...)` and route or router calls, `className` attribute values, `data-testid` and other test-selector attributes, object property keys, and machine-readable constants.
3. WHEN the Tree-sitter Extractor inspects a candidate span, THE Tree-sitter Extractor SHALL produce a Candidate annotated with `translatable` and a `reason`, and SHALL write only translatable Candidates to the Manifest.
4. WHEN a candidate is not translatable, THE Extract_Stage SHALL record it in the ExtractionReport with its path, offset, snippet, and reason rather than dropping it silently.
