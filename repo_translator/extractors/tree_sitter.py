"""Tree-sitter PRIMARY code extractor — template-fragment splitting + context-aware rules."""

# ponytail: import still guarded so a missing/broken grammar degrades to the
# regex fallback instead of crashing extraction — but tree-sitter is a default
# dep now, so the guarded path is the exception, not the norm.

from __future__ import annotations

from pathlib import Path

from ..core import _has_cjk_ideograph
from ..segments import SegmentKind, build_segment
from .base import Candidate, register

try:
    import tree_sitter_language_pack as _tslp

    _TREE_SITTER_AVAILABLE = True
except ImportError:
    _TREE_SITTER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Language mapping
# ---------------------------------------------------------------------------

_SUFFIX_TO_LANG: dict[str, str] = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".vue": "vue",
}

_SUPPORTED_SUFFIXES: set[str] = set(_SUFFIX_TO_LANG)

# ---------------------------------------------------------------------------
# Node-type sets (cross-language)
# ---------------------------------------------------------------------------

_COMMENT_TYPES: set[str] = {"comment", "line_comment", "block_comment"}

_STRING_CONTAINER_TYPES: set[str] = {
    "string",                          # JS/TS/TSX/Java
    "string_literal",                  # Rust/C/C++/Kotlin/Java
    "interpreted_string_literal",      # Go
    "raw_string_literal",              # Go/Rust
}

_STRING_CONTENT_TYPES: set[str] = {
    "string_fragment",                 # JS/TS/TSX/Java
    "string_content",                  # Rust/C/C++/Kotlin
    "interpreted_string_literal_content",  # Go
}

_IMPORT_PARENT_TYPES: set[str] = {
    "import_statement", "export_statement",  # JS/TS
    "import_declaration",                    # Java/Go
    "import_header",                         # Kotlin
    "preproc_include",                       # C/C++
    "use_declaration",                       # Rust
}

# UI attributes whose values are translatable
_UI_ATTRS: set[str] = {"placeholder", "title", "aria-label", "alt"}

# Attributes whose values are NOT translatable
_SKIP_ATTRS: set[str] = {"classname", "data-testid"}

# Function calls whose string args are NOT translatable
_ROUTE_FUNCS: set[str] = {"fetch", "navigate", "push", "replace", "route", "redirect"}

# Error constructor names
_ERROR_NAMES: set[str] = {
    "Error", "TypeError", "RangeError", "SyntaxError", "ReferenceError",
    "Exception", "RuntimeException", "IllegalArgumentException",
    "IllegalStateException",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_text(node, file_bytes: bytes) -> str:
    return file_bytes[node.start_byte:node.end_byte].decode("utf-8")


def _ident_text(node) -> str:
    """Get identifier text from a node (works on any node with .text)."""
    t = node.text
    return t.decode("utf-8") if isinstance(t, bytes) else str(t)


def _callee_name(call_node) -> str | None:
    """Extract the callee name from a call_expression node."""
    if not call_node.children:
        return None
    callee = call_node.children[0]
    if callee.type in ("identifier", "simple_identifier"):
        return _ident_text(callee)
    if callee.type == "member_expression":
        prop = callee.child_by_field_name("property")
        if prop:
            return _ident_text(prop)
    if callee.type == "selector_expression":
        # Go: obj.Method — field_identifier is the method
        for c in callee.children:
            if c.type == "field_identifier":
                return _ident_text(c)
    return _ident_text(callee)


def _callee_object(call_node) -> str | None:
    """Get the object part of a member call (console.log → 'console')."""
    if not call_node.children:
        return None
    callee = call_node.children[0]
    if callee.type == "member_expression":
        obj = callee.child_by_field_name("object")
        if obj:
            return _ident_text(obj)
    return None


def _find_ancestor(node, type_set: set[str]):
    """Walk up parents until finding one with type in type_set, or None."""
    cur = node.parent
    while cur is not None:
        if cur.type in type_set:
            return cur
        cur = cur.parent
    return None


def _is_import_context(node) -> bool:
    """Is this string inside an import/require context?"""
    # Direct parent check (covers most cases)
    parent = node.parent
    while parent is not None:
        if parent.type in _IMPORT_PARENT_TYPES:
            return True
        # require("x") pattern
        if parent.type == "arguments":
            gp = parent.parent
            if gp and gp.type == "call_expression":
                name = _callee_name(gp)
                if name == "require":
                    return True
        # Stop at statement level
        if parent.type.endswith("_statement") or parent.type.endswith("_declaration"):
            break
        parent = parent.parent
    return False


def _get_enclosing_call(node):
    """Find the enclosing call_expression if this node is an argument to it."""
    parent = node.parent
    while parent is not None:
        # arguments/argument_list/value_arguments → parent is call_expression/call_suffix
        if parent.type in ("arguments", "argument_list", "value_arguments"):
            gp = parent.parent
            if gp and gp.type in ("call_expression", "method_invocation"):
                return gp
            # Kotlin: call_suffix → call_expression
            if gp and gp.type == "call_suffix":
                ggp = gp.parent
                if ggp and ggp.type == "call_expression":
                    return ggp
        if parent.type.endswith("_statement") or parent.type.endswith("_declaration"):
            break
        parent = parent.parent
    return None


def _is_console_call(call_node) -> bool:
    """Is this call console.*(...)?"""
    obj = _callee_object(call_node)
    return obj == "console"


def _is_error_constructor_call(node) -> bool:
    """Is the enclosing context a throw/new Error(...)? Check ancestors for throw + new."""
    parent = node.parent
    while parent is not None:
        if parent.type in ("arguments", "argument_list", "value_arguments"):
            gp = parent.parent
            # JS/TS: new_expression with Error-like identifier
            if gp and gp.type == "new_expression":
                for c in gp.children:
                    if c.type in ("identifier", "type_identifier") and _ident_text(c) in _ERROR_NAMES:
                        return True
            # Java: object_creation_expression with Error/Exception type
            if gp and gp.type == "object_creation_expression":
                for c in gp.children:
                    if c.type == "type_identifier" and any(
                        err in _ident_text(c) for err in ("Error", "Exception")
                    ):
                        return True
            # Kotlin: call_suffix parent is call_expression with Exception name
            if gp and gp.type == "call_suffix":
                ggp = gp.parent
                if ggp and ggp.type == "call_expression":
                    for c in ggp.children:
                        if c.type in ("simple_identifier", "identifier"):
                            if any(err in _ident_text(c) for err in ("Error", "Exception")):
                                return True
        if parent.type.endswith("_statement") or parent.type == "source_file":
            break
        parent = parent.parent
    return False


def _is_route_call(call_node) -> bool:
    """Is this call fetch/navigate/push/replace/route/redirect?"""
    name = _callee_name(call_node)
    return name in _ROUTE_FUNCS if name else False


def _is_object_key(node) -> bool:
    """Is this string node an object property key?"""
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "pair":
        key = parent.child_by_field_name("key")
        if key is not None:
            return key.id == node.id
        # Fallback: first named child
        named = parent.named_children
        if named and named[0].id == node.id:
            return True
    return False


def _get_jsx_attr_name(node) -> str | None:
    """If node is a JSX attribute value, return the attribute name."""
    parent = node.parent
    if parent is None:
        return None
    if parent.type == "jsx_attribute":
        for child in parent.children:
            if child.type == "property_identifier":
                return _ident_text(child)
    return None


def _is_machine_constant(text: str) -> bool:
    """SCREAMING_SNAKE or no-CJK non-natural-language string."""
    if _has_cjk_ideograph(text):
        return False
    stripped = text.strip()
    if stripped and all(c.isupper() or c.isdigit() or c == "_" for c in stripped):
        return True
    return False


def _classify_string(node, file_bytes: bytes) -> tuple[bool, str]:
    """Classify a string container node. Returns (translatable, reason)."""
    text = _node_text(node, file_bytes)

    # Object key — never translate
    if _is_object_key(node):
        return False, "object key"

    # Import context — never translate
    if _is_import_context(node):
        return False, "import/module source"

    # JSX attribute value
    attr_name = _get_jsx_attr_name(node)
    if attr_name is not None:
        attr_lower = attr_name.lower()
        if attr_lower in _SKIP_ATTRS or attr_lower.startswith("data-test"):
            return False, "test selector" if "test" in attr_lower else "className"
        if attr_lower == "classname":
            return False, "className"
        if attr_lower in _UI_ATTRS:
            return True, "UI attribute"
        # Other attrs: only if CJK
        if _has_cjk_ideograph(text):
            return True, "UI attribute"
        return False, "non-translatable attribute"

    # Route/fetch call
    call = _get_enclosing_call(node)
    if call is not None:
        if _is_route_call(call):
            return False, "route/endpoint"
        if _is_console_call(call):
            if _has_cjk_ideograph(text):
                return True, "console.* arg (message)"
            return False, "no CJK content"

    # Error constructor
    if _is_error_constructor_call(node):
        if _has_cjk_ideograph(text):
            return True, "error message"
        return False, "no CJK content"

    # Machine constant
    if _is_machine_constant(text):
        return False, "machine constant"

    # Default: translatable if CJK
    if _has_cjk_ideograph(text):
        return True, "string with CJK"

    return False, "no CJK content"


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class TreeSitterExtractor:
    """PRIMARY code extractor: Tree-sitter CST with context-aware rules."""

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in _SUPPORTED_SUFFIXES

    def extract(self, path: Path, file_bytes: bytes) -> list[Candidate]:
        if not _TREE_SITTER_AVAILABLE:
            raise RuntimeError("tree-sitter not available")

        suffix = path.suffix.lower()
        lang = _SUFFIX_TO_LANG.get(suffix)
        if lang is None:
            raise RuntimeError(f"No language mapping for {suffix}")

        try:
            parser = _tslp.get_parser(lang)
        except Exception as e:
            raise RuntimeError(f"Failed to get parser for {lang}: {e}") from e

        tree = parser.parse(file_bytes)
        path_str = path.as_posix()
        candidates: list[Candidate] = []

        self._walk(tree.root_node, file_bytes, path_str, candidates)
        return candidates

    def _walk(self, node, file_bytes: bytes, path_str: str, candidates: list[Candidate]) -> None:
        node_type = node.type

        # --- Comments ---
        if node_type in _COMMENT_TYPES:
            text = _node_text(node, file_bytes)
            if _has_cjk_ideograph(text):
                kind = self._comment_kind(text)
                seg = build_segment(
                    path=path_str, kind=kind,
                    start_byte=node.start_byte, end_byte=node.end_byte,
                    file_bytes=file_bytes, source_text=text,
                )
                candidates.append(Candidate(segment=seg, translatable=True, reason="comment"))
            return

        # --- JSX text / Vue template text ---
        if node_type in ("jsx_text", "text"):
            text = _node_text(node, file_bytes)
            if text.strip():
                seg = build_segment(
                    path=path_str, kind=SegmentKind.JSX_TEXT,
                    start_byte=node.start_byte, end_byte=node.end_byte,
                    file_bytes=file_bytes, source_text=text,
                )
                has_cjk = _has_cjk_ideograph(text)
                candidates.append(Candidate(
                    segment=seg,
                    translatable=has_cjk,
                    reason="jsx text" if has_cjk else "no CJK content",
                ))
            return

        # --- Template strings (JS/TS) — fragment splitting ---
        if node_type == "template_string":
            self._handle_template_string(node, file_bytes, path_str, candidates)
            return

        # --- String containers (all languages) ---
        if node_type in _STRING_CONTAINER_TYPES:
            self._handle_string_node(node, file_bytes, path_str, candidates)
            return

        # --- Recurse ---
        for child in node.children:
            self._walk(child, file_bytes, path_str, candidates)

    def _handle_string_node(self, node, file_bytes: bytes, path_str: str, candidates: list[Candidate]) -> None:
        """Handle a string container node — emit its content children."""
        translatable, reason = _classify_string(node, file_bytes)

        # Find the inner content children
        content_children = [c for c in node.children if c.type in _STRING_CONTENT_TYPES]

        if not content_children:
            # Some grammars (Kotlin string_literal) may not have separate content child;
            # the node itself minus quotes is the content. Check if node has quote children.
            # ponytail: for Kotlin, string_content is the child — already covered above.
            # For raw_string_literal in Go, the whole thing is the literal.
            text = _node_text(node, file_bytes)
            # Strip surrounding quotes for raw check
            if text and text[0] in ('"', "'", '`') and len(text) > 1:
                inner = text[1:-1] if text[-1] == text[0] else text[1:]
                if inner.strip():
                    # Emit the whole node (conservative fallback)
                    seg = build_segment(
                        path=path_str, kind=SegmentKind.STRING,
                        start_byte=node.start_byte, end_byte=node.end_byte,
                        file_bytes=file_bytes, source_text=text,
                    )
                    candidates.append(Candidate(segment=seg, translatable=translatable, reason=reason))
            return

        for content in content_children:
            content_text = _node_text(content, file_bytes)
            if not content_text.strip():
                continue
            seg = build_segment(
                path=path_str, kind=SegmentKind.STRING,
                start_byte=content.start_byte, end_byte=content.end_byte,
                file_bytes=file_bytes, source_text=content_text,
            )
            candidates.append(Candidate(segment=seg, translatable=translatable, reason=reason))

    def _handle_template_string(self, node, file_bytes: bytes, path_str: str, candidates: list[Candidate]) -> None:
        """Template string: emit each string_fragment child as TEMPLATE_STRING_FRAGMENT."""
        protected = []
        for child in node.children:
            if child.type == "template_substitution":
                protected.append(_node_text(child, file_bytes))

        # Context classification at the template_string level
        is_import = _is_import_context(node)
        is_route = False
        is_console = False
        is_error = False

        if not is_import:
            call = _get_enclosing_call(node)
            if call is not None:
                is_route = _is_route_call(call)
                is_console = _is_console_call(call)
            is_error = _is_error_constructor_call(node)

        for child in node.children:
            if child.type == "string_fragment":
                frag_text = _node_text(child, file_bytes)
                if not frag_text:
                    continue

                if is_import:
                    translatable, reason = False, "import/module source"
                elif is_route:
                    translatable, reason = False, "route/endpoint"
                elif is_console:
                    if _has_cjk_ideograph(frag_text):
                        translatable, reason = True, "console.* arg (message)"
                    else:
                        translatable, reason = False, "no CJK content"
                elif is_error:
                    if _has_cjk_ideograph(frag_text):
                        translatable, reason = True, "error message"
                    else:
                        translatable, reason = False, "no CJK content"
                elif _has_cjk_ideograph(frag_text):
                    translatable, reason = True, "string with CJK"
                else:
                    translatable, reason = False, "no CJK content"

                seg = build_segment(
                    path=path_str,
                    kind=SegmentKind.TEMPLATE_STRING_FRAGMENT,
                    start_byte=child.start_byte, end_byte=child.end_byte,
                    file_bytes=file_bytes, source_text=frag_text,
                    protected_context=protected if translatable else None,
                )
                candidates.append(Candidate(segment=seg, translatable=translatable, reason=reason))

    def _comment_kind(self, text: str) -> str:
        if text.startswith("/**"):
            return SegmentKind.DOC_COMMENT
        if text.startswith("/*"):
            return SegmentKind.BLOCK_COMMENT
        return SegmentKind.LINE_COMMENT


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

if _TREE_SITTER_AVAILABLE:
    _extractor = TreeSitterExtractor()
    register(_extractor, _SUPPORTED_SUFFIXES)
