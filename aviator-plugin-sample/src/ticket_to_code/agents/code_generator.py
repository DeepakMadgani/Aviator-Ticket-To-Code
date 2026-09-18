"""
Code Generator Agent

Generates production-ready code using validated context and requirements.

This is Agent 5 in the autonomous pipeline.

Author: Deepak Madgani
Date: April 2026
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional

from ticket_to_code.llm_utils import llm_invoke


def _get_anchor_methods(task) -> list[str]:
    """
    Collect the method names that extract_exact_methods() should extract.

    Handles two task shapes:
      MODIFY: The task edits existing methods. anchor_methods = the methods
              being modified (from allowed_methods + target_method).
      INSERT: The task adds a new method. anchor_methods = the neighbor method
              (from edit_anchors.anchor_method) that the new code is placed after.
              We need that method's real closing brace for the LLM to anchor against.

    Returns a deduplicated list of method names to extract verbatim.
    """
    methods: list[str] = []

    # Source 1: edit_anchors — carries both MODIFY targets and INSERT anchors
    anchors = getattr(task, "edit_anchors", []) or []
    for anchor in anchors:
        # For INSERT tasks: action="add_method", anchor_method="findById"
        # We need findById's body so the LLM sees its closing brace
        anchor_method = anchor.get("anchor_method", "")
        if anchor_method and anchor_method not in methods:
            methods.append(anchor_method)
        # For MODIFY tasks: method_name="validateDuplicateName"
        method_name = anchor.get("method_name", "")
        if method_name and method_name not in methods:
            methods.append(method_name)

    # Source 2: allowed_methods — the planner's explicit scope
    for m in (getattr(task, "allowed_methods", []) or []):
        if m not in methods:
            methods.append(m)

    # Source 3: target_method — primary edit target
    target = getattr(task, "target_method", None)
    if target and target not in methods:
        methods.append(target)

    # Source 4: change_targets — explicit ChangeTarget symbols
    for ct in (getattr(task, "change_targets", []) or []):
        sym = getattr(ct, "symbol", None)
        if sym and sym not in methods:
            methods.append(sym)

    return methods


# tsconfig.json path alias cache: workspace_path → {alias_prefix: resolved_dir}
_tsconfig_alias_cache: dict[str, dict[str, str]] = {}


def _load_tsconfig_aliases(workspace_path: str) -> dict[str, str]:
    """Read tsconfig.json compilerOptions.paths to build alias → dir map."""
    if workspace_path in _tsconfig_alias_cache:
        return _tsconfig_alias_cache[workspace_path]
    aliases: dict[str, str] = {}
    import json as _json
    for name in ("tsconfig.json", "tsconfig.base.json", "tsconfig.app.json"):
        tsconfig = Path(workspace_path) / name
        if not tsconfig.exists():
            # Also check one level down (e.g. xchange-ui/tsconfig.json)
            for sub in Path(workspace_path).iterdir() if Path(workspace_path).is_dir() else []:
                tsconfig = sub / name
                if tsconfig.exists():
                    break
            else:
                continue
        try:
            raw = _json.loads(tsconfig.read_text(encoding="utf-8"))
            paths = raw.get("compilerOptions", {}).get("paths", {})
            base_url = raw.get("compilerOptions", {}).get("baseUrl", ".")
            for alias, targets in paths.items():
                # e.g. "@shared/*" → ["src/app/shared/*"]  →  store "@shared" → "src/app/shared"
                alias_prefix = alias.rstrip("/*")
                if targets:
                    resolved = targets[0].rstrip("/*")
                    aliases[alias_prefix] = resolved
            break
        except Exception:
            pass
    _tsconfig_alias_cache[workspace_path] = aliases
    return aliases


def _build_import_hint(
    symbol_name: str,
    file_path: str,
    source_file_path: str = "",
    workspace_path: str = "",
) -> str:
    """Convert a repo-relative file path to a language-appropriate import hint."""
    p = Path(file_path)
    ext = p.suffix.lower()
    stem = p.stem

    if ext == ".java":
        # Derive Java package from path: src/main/java/com/foo/Bar.java → com.foo.Bar
        parts = p.with_suffix("").parts
        try:
            java_idx = next(i for i, x in enumerate(parts) if x == "java")
            pkg = ".".join(parts[java_idx + 1:])
            return f"import {pkg};"
        except StopIteration:
            return f"import {stem};"

    if ext in (".ts", ".tsx"):
        # ── Cursor technique: compute correct relative OR aliased path ────────
        import_path = _resolve_ts_import_path(
            source_file=source_file_path or "",
            target_file=file_path,
            workspace_path=workspace_path or "",
        )
        return f"import {{ {symbol_name} }} from '{import_path}';"

    if ext == ".py":
        parts = p.with_suffix("").parts
        # Strip common src prefixes
        start = next((i + 1 for i, x in enumerate(parts) if x in ("src", "app")), 0)
        pkg = ".".join(parts[start:])
        return f"from {pkg} import {symbol_name}"

    if ext in (".cs",):
        # C# — namespace from directory
        parts = p.parent.parts
        ns = ".".join(pt for pt in parts if pt not in ("src", ".", ""))
        return f"using {ns};"

    return ""


def _resolve_ts_import_path(
    source_file: str,
    target_file: str,
    workspace_path: str,
) -> str:
    """
    Cursor technique: compute the correct TypeScript import path.

    Priority:
      1. tsconfig.json path alias if the target file sits under an aliased directory
         (e.g.  @shared/models/contract  when @shared → src/app/shared)
      2. Relative path computed from source to target (normalized with forward slashes,
         prefixed with './'' or '../' as required by the TypeScript module resolver)
    """
    # Normalise to forward-slash repo-relative paths
    target_norm = target_file.replace("\\", "/")
    target_no_ext = re.sub(r'\.tsx?$', '', target_norm)

    # 1. Check tsconfig aliases
    if workspace_path:
        aliases = _load_tsconfig_aliases(workspace_path)
        for alias_prefix, resolved_dir in aliases.items():
            resolved_dir_norm = resolved_dir.replace("\\", "/").rstrip("/")
            if target_no_ext.startswith(resolved_dir_norm + "/"):
                rel_within = target_no_ext[len(resolved_dir_norm) + 1:]
                # Gap 5: Check for barrel export — if index.ts exists in the target's
                # directory and re-exports the symbol, use the shorter directory path.
                if workspace_path:
                    target_dir = str(Path(target_file.replace("\\", "/")).parent)
                    barrel = Path(workspace_path) / target_dir / "index.ts"
                    if barrel.exists():
                        try:
                            barrel_content = barrel.read_text(encoding="utf-8", errors="ignore")
                            # Check if barrel re-exports from the target file
                            target_stem = Path(target_file).stem
                            if target_stem in barrel_content:
                                # Use directory path instead of full file path
                                dir_within = str(Path(rel_within).parent).replace("\\", "/")
                                if dir_within and dir_within != ".":
                                    return f"{alias_prefix}/{dir_within}"
                                return alias_prefix
                        except Exception:
                            pass
                return f"{alias_prefix}/{rel_within}"

    # 2. Compute relative path from source to target
    if source_file:
        source_dir = Path(source_file.replace("\\", "/")).parent
        try:
            rel = Path(target_no_ext).relative_to(source_dir)
            rel_str = str(rel).replace("\\", "/")
            return rel_str if rel_str.startswith(".") else f"./{rel_str}"
        except ValueError:
            # Not a sub-path; compute ../.. manually
            import os
            rel = os.path.relpath(target_no_ext, str(source_dir)).replace("\\", "/")
            return rel if rel.startswith(".") else f"./{rel}"

    # 3. Fallback: just the stem
    return f"./{Path(target_no_ext).name}"


from aviator.services.llm import LLMRegistry
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from ticket_to_code.models import (
    DevelopmentTask,
    CodeChunk,
    StructuredRequirements,
    GeneratedCode,
    ProgrammingLanguage,
)

logger = logging.getLogger(__name__)

# Module-level tier trace accumulator — reset per file, read by workflow.py
# Each entry: {"file": str, "edit_index": int, "tier": str, "old_chars": int, "new_chars": int}
_patch_tier_traces: list[dict] = []

# Module-level reuse directive across agents (cleared per run)
_reuse_directive: str = ""


def set_reuse_directive(directive: str) -> None:
    """Set the module-level reuse directive for code generation."""
    global _reuse_directive
    _reuse_directive = str(directive or "")


def clear_reuse_directive() -> None:
    """Clear the reuse directive to prevent leakage across runs or Celery tasks."""
    global _reuse_directive
    _reuse_directive = ""


def _apply_str_replace_edits(edits: list[dict], existing: str, file_path: str) -> str:
    """
    Apply a batch of str_replace edits atomically using a 6-Tier Matching Cascade.

    Each edit is a dict with keys:
        old_str      — code to find (should match exactly, but tiers handle mismatches)
        new_str      — replacement text
        scope_method — (optional) method name from edit_anchors; narrows search

    6-Tier Resolution Cascade (ALL tiers run in ONE pass per edit — not separate retries):
      Tier 1: Exact string match                      (fastest, O(n))
      Tier 2: CRLF normalization (\r\n ↔ \n)           (O(n))
      Tier 3: Whitespace-normalized sliding window     (catches indent diffs, O(n*k))
      Tier 4: AST-structural matching via tree-sitter  (catches format/comment diffs, O(n))
      Tier 5: Levenshtein fuzzy match (≥90% threshold) (catches minor token changes, O(n*m))
      Tier 6: Scoped search within method body         (resolves ambiguous matches)

    Atomicity: all edits succeed or ZERO bytes are written.
    """
    result = existing
    # Reset tier traces for this file
    _patch_tier_traces.clear()

    for i, edit in enumerate(edits):
        old_str = edit.get("old_str", "")
        new_str = edit.get("new_str", "")
        scope_method = edit.get("scope_method", "")

        if not old_str:
            raise ValueError(
                f"Edit {i+1}/{len(edits)} for {file_path}: old_str is empty. "
                f"Every edit must specify the exact code to replace."
            )

        # ── Tier 1: Direct exact match ─────────────────────────────────
        count = result.count(old_str)

        if count == 1:
            result = result.replace(old_str, new_str, 1)
            _log_edit(i, len(edits), file_path, old_str, new_str, "Tier 1: Exact match")
            continue

        # ── Tier 2: CRLF mismatch resolution ──────────────────────────
        if count == 0:
            _adapted_old = _try_crlf_adapt(old_str, result)
            if _adapted_old is not None:
                result = result.replace(_adapted_old, new_str, 1)
                _log_edit(i, len(edits), file_path, old_str, new_str, "Tier 2: CRLF-adapted")
                continue

        # ── Tier 3: Whitespace-normalized sliding window ───────────────
        if count == 0:
            _ws_result = _whitespace_normalized_match(old_str, new_str, result)
            if _ws_result is not None:
                result = _ws_result
                _log_edit(i, len(edits), file_path, old_str, new_str,
                          "Tier 3: Whitespace-normalized")
                continue

        # ── Tier 4: AST-structural matching via tree-sitter ────────────
        #    Fresh parse at apply time (like Cursor) — NOT from stale index.
        #    Files change task-to-task, so we parse the CURRENT content.
        if count == 0:
            _ast_result = _ast_structural_match(old_str, new_str, result, file_path)
            if _ast_result is not None:
                result = _ast_result
                _log_edit(i, len(edits), file_path, old_str, new_str,
                          "Tier 4: AST-structural match")
                continue

        # ── Tier 5: Levenshtein fuzzy match (≥90% similarity) ──────────
        if count == 0:
            _fuzzy_result = _levenshtein_fuzzy_match(old_str, new_str, result)
            if _fuzzy_result is not None:
                result = _fuzzy_result
                _log_edit(i, len(edits), file_path, old_str, new_str,
                          "Tier 5: Fuzzy match (Levenshtein)")
                continue

        # ── Tier 6: Scoped search within method body ───────────────────
        #    Handles both count==0 (not found) and count>1 (ambiguous)
        if scope_method:
            _scope_resolved = _resolve_within_scope(
                result, old_str, new_str, scope_method, file_path
            )
            if _scope_resolved is not None:
                result = _scope_resolved
                _log_edit(i, len(edits), file_path, old_str, new_str,
                          f"Tier 6: Scoped to '{scope_method}'")
                continue

        # ── All 6 tiers exhausted — fail with actionable diagnostics ───
        if count == 0:
            # Find the closest match to show in the error for better retry
            _best = _find_closest_match(old_str, result)
            _best_info = ""
            if _best:
                _best_info = (
                    f"\n\nCLOSEST MATCH FOUND ({_best['ratio']:.0%} similar, "
                    f"lines {_best['start_line']}-{_best['end_line']}):\n"
                    f"{_best['content'][:500]}"
                )
            preview = old_str[:300].replace('\n', '\\n')
            raise ValueError(
                f"Edit {i+1}/{len(edits)} for {file_path}: old_str not found "
                f"after ALL 6 matching tiers (exact → CRLF → whitespace → AST → "
                f"fuzzy → scope). Searched for:\n{preview}{_best_info}"
            )
        else:
            _match_locations = _find_all_match_lines(result, old_str)
            _loc_desc = "; ".join(
                f"match {j+1} at line {ln}" for j, ln in enumerate(_match_locations[:5])
            )
            _scope_msg = (f" (scope_method='{scope_method}' was tried but didn't resolve it)"
                          if scope_method
                          else " (no edit_anchor / scope_method was provided)")
            raise ValueError(
                f"Edit {i+1}/{len(edits)} for {file_path}: old_str matches "
                f"{count} locations ({_loc_desc}). "
                f"Provide more surrounding context lines to make old_str unique."
                + _scope_msg
            )

    # ── Post-patch AST validation ──────────────────────────────────────
    # Parse the result with tree-sitter to catch broken patches BEFORE
    # returning to the caller (which writes to disk).
    _ast_issues = _validate_patch_ast(existing, result, file_path)
    if _ast_issues:
        logger.warning(
            f"  ⚠️ AST validation warnings for {file_path}: {_ast_issues}"
        )
        # Don't block — log warnings. The write-side guards in workflow.py
        # make the final accept/reject decision.

    return result


# ─── Tier Helper Functions ───────────────────────────────────────────────

def _log_edit(i: int, total: int, file_path: str, old_str: str, new_str: str, tier: str):
    """Consistent logging for all edit tiers + record for trace output."""
    _old_preview = old_str[:500].replace('\n', '\\n')
    _new_preview = new_str[:500].replace('\n', '\\n')
    logger.info(
        f"  str_replace edit {i+1}/{total} applied to {file_path} "
        f"[{tier}] (old={len(old_str)} chars → new={len(new_str)} chars)"
    )
    logger.info(f"    OLD: {_old_preview}")
    logger.info(f"    NEW: {_new_preview}")
    # Record for trace output
    _patch_tier_traces.append({
        "file": file_path,
        "edit_index": i + 1,
        "total_edits": total,
        "tier": tier,
        "old_chars": len(old_str),
        "new_chars": len(new_str),
    })


def _try_crlf_adapt(old_str: str, content: str) -> Optional[str]:
    """Tier 2: Try adapting line endings. Returns the adapted old_str or None."""
    if '\n' in old_str and '\r\n' not in old_str:
        _adapted = old_str.replace('\n', '\r\n')
        if content.count(_adapted) == 1:
            return _adapted
    elif '\r\n' in old_str:
        _adapted = old_str.replace('\r\n', '\n')
        if content.count(_adapted) == 1:
            return _adapted
    return None


def _whitespace_normalized_match(
    old_str: str, new_str: str, content: str
) -> Optional[str]:
    """
    Tier 3: Whitespace-normalized sliding window.

    Algorithm: Normalize ALL whitespace (leading/trailing/internal) to single
    space, then slide a window of len(search_lines) over the file lines.
    If the normalized forms match, replace the ORIGINAL (un-normalized) window.

    Time complexity: O(n * k) where n = file lines, k = search lines.

    This catches the most common failure: LLM uses different indentation than
    the actual file (2-space vs 4-space vs tabs).
    """
    search_lines = old_str.strip().splitlines()
    if not search_lines:
        return None

    n = len(search_lines)
    normalized_search = [re.sub(r'\s+', ' ', l.strip()) for l in search_lines]

    # Skip if search is too short (single blank line etc.)
    if all(not s for s in normalized_search):
        return None

    file_lines = content.splitlines(True)  # Keep line endings for faithful replacement
    file_lines_stripped = [re.sub(r'\s+', ' ', l.strip()) for l in file_lines]

    matches = []
    for i in range(len(file_lines_stripped) - n + 1):
        window = file_lines_stripped[i:i + n]
        if window == normalized_search:
            matches.append(i)

    if len(matches) == 1:
        # Unique match — replace the ORIGINAL lines (preserving encoding/BOM)
        match_start = matches[0]
        original_window = ''.join(file_lines[match_start:match_start + n])
        # Detect indentation of the target location to re-indent new_str
        _target_indent = _detect_indent(file_lines[match_start])
        _search_indent = _detect_indent(search_lines[0]) if search_lines else ""
        _reindented_new = _reindent(new_str, _search_indent, _target_indent)
        return content.replace(original_window, _reindented_new, 1)

    return None


def _detect_indent(line: str) -> str:
    """Extract the leading whitespace from a line."""
    return line[:len(line) - len(line.lstrip())] if line.strip() else ""


def _reindent(code: str, old_indent: str, new_indent: str) -> str:
    """
    Re-indent code from old_indent base to new_indent base.

    If the LLM generated code with 2-space indent but the file uses 4-space,
    this adjusts ALL lines proportionally.
    """
    if old_indent == new_indent or not old_indent:
        return code

    lines = code.splitlines(True)
    result_lines = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result_lines.append(line)
            continue
        current_indent = line[:len(line) - len(stripped)]
        # Count how many "units" of old_indent this line has
        if old_indent and current_indent.startswith(old_indent):
            # Calculate relative indentation
            if len(old_indent) > 0:
                depth = 0
                remaining = current_indent
                while remaining.startswith(old_indent):
                    depth += 1
                    remaining = remaining[len(old_indent):]
                new_line_indent = new_indent * depth + remaining
            else:
                new_line_indent = current_indent
            result_lines.append(new_line_indent + stripped)
        else:
            result_lines.append(line)
    return ''.join(result_lines)


def _ast_structural_match(
    old_str: str, new_str: str, content: str, file_path: str
) -> Optional[str]:
    """
    Tier 4: AST-structural matching via tree-sitter.

    Parse the SEARCH block and the file with tree-sitter (FRESH parse —
    not from stale index). Find the AST node in the file whose structure
    matches the SEARCH block's AST.

    Like Cursor's approach: parse on-the-fly at edit time, replace at
    the matched node's exact byte range.

    Returns the patched content, or None if no structural match found.
    """
    try:
        from ticket_to_code.agents.smart_extract import parse_with_treesitter
    except ImportError:
        return None

    # Parse the full file (fresh — current content, not cached)
    file_tree, lang = parse_with_treesitter(content, file_path)
    if file_tree is None:
        return None

    # Parse the SEARCH block as a code fragment
    search_tree, _ = parse_with_treesitter(old_str, file_path)
    if search_tree is None:
        return None

    # Get the structural signature of the SEARCH block
    search_sig = _ast_signature(search_tree.root_node)
    if not search_sig:
        return None

    # Walk the file AST to find a node with matching structure
    source_bytes = content.encode("utf-8")
    matches = []

    def _find_matching_nodes(node):
        node_sig = _ast_signature(node)
        if node_sig == search_sig:
            # Verify the text content is reasonably similar (>70%)
            # to avoid false positives from structurally identical but
            # semantically different code blocks
            node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            from difflib import SequenceMatcher
            ratio = SequenceMatcher(None, old_str.strip(), node_text.strip()).ratio()
            if ratio >= 0.70:
                matches.append((node, node_text, ratio))
        for child in node.children:
            _find_matching_nodes(child)

    _find_matching_nodes(file_tree.root_node)

    if len(matches) == 1:
        node, node_text, ratio = matches[0]
        logger.info(f"    AST match: {ratio:.0%} similar, "
                     f"L{node.start_point[0]+1}-L{node.end_point[0]+1}")
        # Replace the matched text in the content (line-based, safe for all encodings)
        return content.replace(node_text, new_str, 1)

    if len(matches) > 1:
        logger.info(f"    AST match: {len(matches)} candidates (ambiguous), skipping")


    return None


def _ast_signature(node) -> tuple:
    """
    Generate a structural signature for an AST node (tree isomorphism).

    The signature captures the STRUCTURE (node types and their tree shape)
    but IGNORES the actual text content (identifiers, literals, whitespace).

    Two code blocks with identical AST signatures are structurally equivalent
    regardless of formatting, comments, or variable naming.
    """
    if node.child_count == 0:
        return (node.type,)
    child_sigs = tuple(_ast_signature(child) for child in node.children
                       if child.type not in ("comment", "line_comment", "block_comment"))
    return (node.type, child_sigs)


def _levenshtein_fuzzy_match(
    old_str: str, new_str: str, content: str,
    threshold: float = 0.90
) -> Optional[str]:
    """
    Tier 5: Levenshtein fuzzy match with sliding window.

    Algorithm: Slide a window of len(search_lines) over the file lines.
    For each window, compute SequenceMatcher.ratio(). Accept if:
      1. ratio >= threshold (default 90%)
      2. Exactly ONE candidate above threshold (no ambiguity)

    Time complexity: O(n * m) where n = file chars, m = search chars.

    Uses Python's difflib.SequenceMatcher which is based on the
    Ratcliff/Obershelp algorithm — faster than pure Levenshtein for
    large strings because it finds longest common subsequences.
    """
    from difflib import SequenceMatcher

    search_lines = old_str.strip().splitlines()
    if not search_lines or len(search_lines) < 2:
        # Too short for fuzzy — risk of false positives
        return None

    n = len(search_lines)
    file_lines = content.splitlines(True)  # Keep line endings

    if len(file_lines) < n:
        return None

    best_ratio = 0.0
    best_start = -1
    candidates = []

    # Sliding window over file lines
    for i in range(len(file_lines) - n + 1):
        window_text = ''.join(file_lines[i:i + n])
        ratio = SequenceMatcher(None, old_str.strip(), window_text.strip()).ratio()
        if ratio >= threshold:
            candidates.append((i, ratio, window_text))
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i

    if len(candidates) == 1:
        # Unique fuzzy match — safe to apply
        start, ratio, window_text = candidates[0]
        logger.info(f"    Fuzzy match: {ratio:.1%} similar at line {start+1}")
        # Re-indent new_str to match the target location's indentation
        _target_indent = _detect_indent(file_lines[start])
        _search_indent = _detect_indent(search_lines[0]) if search_lines else ""
        _reindented_new = _reindent(new_str, _search_indent, _target_indent)
        return content.replace(window_text, _reindented_new, 1)

    if len(candidates) > 1:
        logger.info(
            f"    Fuzzy match: {len(candidates)} candidates above {threshold:.0%} "
            f"threshold (ambiguous) — not applying"
        )

    return None


def _find_closest_match(old_str: str, content: str) -> Optional[dict]:
    """
    Find the closest matching region in the file for a failed SEARCH block.
    Used to provide actionable diagnostics in error messages.

    Returns dict with 'content', 'start_line', 'end_line', 'ratio' or None.
    """
    from difflib import SequenceMatcher

    search_lines = old_str.strip().splitlines()
    if not search_lines:
        return None

    n = len(search_lines)
    file_lines = content.splitlines()

    if len(file_lines) < n:
        return None

    best_ratio = 0.0
    best_start = -1
    best_text = ""

    # Sample every 1st line to keep O(n) for large files
    step = max(1, (len(file_lines) - n) // 500)
    for i in range(0, len(file_lines) - n + 1, step):
        window = '\n'.join(file_lines[i:i + n])
        ratio = SequenceMatcher(None, old_str.strip(), window.strip()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_start = i
            best_text = window

    if best_ratio >= 0.50:  # Only report if at least 50% similar
        return {
            "content": best_text,
            "start_line": best_start + 1,
            "end_line": best_start + n,
            "ratio": best_ratio,
        }
    return None


def _resolve_within_scope(
    content: str, old_str: str, new_str: str,
    scope_method: str, file_path: str
) -> Optional[str]:
    """
    Tier 6: Resolve an ambiguous old_str by restricting search to a method body.

    Uses brace-counting to find the method named scope_method, then checks
    if old_str appears exactly once inside that region. If yes, replaces
    only that occurrence.

    Returns the patched content, or None if scoping didn't resolve ambiguity.
    """
    # Find the method declaration
    pattern = re.compile(
        r'(?:^|\n)([ \t]*(?:(?:public|private|protected|static|async|final|'
        r'override|abstract|@\w+\s+)*)'
        + re.escape(scope_method) + r'\s*\()',
        re.MULTILINE
    )
    match = pattern.search(content)
    if not match:
        return None

    # Find method body boundaries using brace counting
    start_pos = match.start(1)
    depth = 0
    end_pos = len(content)
    found_open = False
    idx = match.start(1)
    while idx < len(content):
        ch = content[idx]
        if ch == '{':
            depth += 1
            found_open = True
        elif ch == '}':
            depth -= 1
            if found_open and depth == 0:
                end_pos = idx + 1
                break
        idx += 1

    # Extract method body and check for uniqueness within it
    method_body = content[start_pos:end_pos]
    scoped_count = method_body.count(old_str)

    if scoped_count == 1:
        # Unique within scope — find its absolute position
        offset_in_body = method_body.index(old_str)
        abs_pos = start_pos + offset_in_body
        return content[:abs_pos] + new_str + content[abs_pos + len(old_str):]

    # Tier 6 fallback: try whitespace-normalized match within scope
    if scoped_count == 0:
        search_lines = old_str.strip().splitlines()
        if search_lines:
            n = len(search_lines)
            normalized_search = [re.sub(r'\s+', ' ', l.strip()) for l in search_lines]
            body_lines = method_body.splitlines(True)
            body_stripped = [re.sub(r'\s+', ' ', l.strip()) for l in body_lines]
            for j in range(len(body_stripped) - n + 1):
                if body_stripped[j:j + n] == normalized_search:
                    original_window = ''.join(body_lines[j:j + n])
                    abs_pos = start_pos + method_body.index(original_window)
                    _target_indent = _detect_indent(body_lines[j])
                    _search_indent = _detect_indent(search_lines[0])
                    _reindented = _reindent(new_str, _search_indent, _target_indent)
                    return content[:abs_pos] + _reindented + content[abs_pos + len(original_window):]

    return None  # Still ambiguous even within scope


def _find_all_match_lines(content: str, old_str: str) -> list[int]:
    """Return 1-indexed line numbers where old_str starts."""
    lines = []
    start = 0
    while True:
        idx = content.find(old_str, start)
        if idx == -1:
            break
        line_num = content[:idx].count('\n') + 1
        lines.append(line_num)
        start = idx + 1
    return lines


def _validate_patch_ast(
    original: str, patched: str, file_path: str
) -> list[str]:
    """
    Post-patch AST validation using fresh tree-sitter parse.

    Parses the patched content and checks for:
    1. Syntax errors introduced by the patch
    2. Drastic reduction in method/class count (truncation)
    3. Import clobbering

    Returns a list of warning strings (empty = all good).
    """
    issues = []

    try:
        from ticket_to_code.agents.smart_extract import (
            parse_with_treesitter,
            has_ast_errors,
            get_ast_error_ranges,
            count_ast_nodes,
        )
    except ImportError:
        return []  # tree-sitter not available — skip validation

    patched_tree, lang = parse_with_treesitter(patched, file_path)
    if patched_tree is None:
        return []  # Can't validate — skip

    # Check 1: Syntax errors in patched file
    if has_ast_errors(patched_tree):
        error_ranges = get_ast_error_ranges(patched_tree)
        issues.append(
            f"Patch introduces syntax errors at lines: "
            f"{[f'L{s}-L{e}' for s, e in error_ranges[:5]]}"
        )

    # Check 2: Compare structural counts with original
    orig_tree, _ = parse_with_treesitter(original, file_path)
    if orig_tree is not None:
        if lang == "java":
            node_types = ["method_declaration", "constructor_declaration"]
        elif lang in ("typescript", "javascript"):
            node_types = ["method_definition", "function_declaration"]
        elif lang == "python":
            node_types = ["function_definition"]
        else:
            node_types = []

        if node_types:
            orig_count = count_ast_nodes(orig_tree, node_types)
            new_count = count_ast_nodes(patched_tree, node_types)
            if orig_count > 0 and new_count < orig_count - 1:
                issues.append(
                    f"Method count dropped: {orig_count} → {new_count} "
                    f"(possible truncation or accidental deletion)"
                )

    return issues









class CodeGeneratorAgent:
    """
    Agent 5: Code Generation
    
    Generates production-ready, idiomatic code using:
    - Validated requirements
    - Retrieved code context
    - Task specifications
    
    Features:
    - Context-aware generation (no hallucination)
    - Proper error handling
    - Documentation generation
    - Idiomatic patterns
    """
    
    def __init__(self):
        # Always use the smarter assistant model (gemini-1.5-pro / gemini-2.5-flash) for coding
        self.llm = LLMRegistry.get_llm(assistant=True)
        logger.info("Code Generator Agent initialized")

    def _maybe_dump_llm_io(self, task: DevelopmentTask, messages, full_content: str, cont_attempt: int) -> None:
        """Best-effort debug dump for prompt/response text.

        Dumps are disabled by default. Set AVIATOR_DEBUG_LLM_IO=1 to enable.
        """
        if os.getenv("AVIATOR_DEBUG_LLM_IO", "").strip().lower() not in {"1", "true", "yes", "on"}:
            return

        try:
            base = Path(os.getenv("AVIATOR_DEBUG_DIR", ".aviator")) / "llm_debug"
            task_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(task, "id", "task")))
            debug_dir = base / task_id
            debug_dir.mkdir(parents=True, exist_ok=True)

            (debug_dir / f"{task_id}_prompt_{cont_attempt}.txt").write_text(
                str(messages), encoding="utf-8"
            )
            (debug_dir / f"{task_id}_response_{cont_attempt}.txt").write_text(
                full_content, encoding="utf-8"
            )
        except Exception as dump_err:
            logger.debug(f"LLM debug dump skipped: {dump_err}")
    
    # ── Cross-tier call validation (no hallucinated service methods) ────────
    # Top AI IDEs never invent a call chain: every `this.<svc>.<method>()` in
    # generated code must resolve to a REAL method — in the repo, or generated
    # earlier in this same run (session files). Observed failure (2026-09-18):
    # the generator called `this.contractService.getProjectMembership()` — a
    # method that existed nowhere — burning 2 TSC retries and deferring to
    # fix_build. This check catches it BEFORE the code is written to disk.
    _SVC_CALL_RE = re.compile(r"this\.([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    _CTOR_INJECT_RE = re.compile(
        r"(?:private|public|protected)\s+(?:readonly\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"
    )
    _METHOD_NAME_RE = re.compile(
        r"(?:^|[^.\w$])([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:\{|:)",
        re.MULTILINE,
    )

    def _find_class_source(self, class_name: str) -> Optional[str]:
        """Locate the source text of a TypeScript class by name.

        Order: session files (generated/modified this run — freshest), then a
        bounded workspace scan (services first, capped so it can never hang).
        Returns None when unresolvable (caller must not false-flag).
        """
        session = getattr(self, "_session_files", None) or {}
        for _key, text in session.items():
            try:
                if class_name in text:
                    return text
            except Exception:
                continue
        ws = getattr(self, "workspace_path", None) or getattr(self, "_workspace_path", None)
        if not ws:
            return None
        ws_root = Path(ws)
        scanned = 0
        MAX_SCAN = 3000
        try:
            for fp in ws_root.rglob("*.ts"):
                scanned += 1
                if scanned > MAX_SCAN:
                    break
                parts = fp.parts
                if any(p in ("node_modules", ".git", "dist", ".aviator") for p in parts):
                    continue
                if fp.name.endswith(".spec.ts"):
                    continue
                try:
                    head = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if f"class {class_name}" in head:
                    return head
        except Exception:
            return None
        return None

    def _validate_service_calls(self, content: str, existing_content: Optional[str]) -> list:
        """Return human-readable descriptions of `this.<prop>.<method>()` calls
        whose method does not exist on the injected service's class. Only calls
        whose service class CAN be resolved are validated (unresolvable ones
        are left to the TSC gate — no false positives).
        """
        try:
            src = (existing_content or "") + "\n" + (content or "")
            calls = set(self._SVC_CALL_RE.findall(src))
            if not calls:
                return []
            prop_to_class = dict(self._CTOR_INJECT_RE.findall(src))
            if not prop_to_class:
                return []
            own_methods = {m.group(1) for m in self._METHOD_NAME_RE.finditer(src)}
            unknown = []
            for prop, method in sorted(calls):
                cls = prop_to_class.get(prop)
                if cls is None:
                    continue
                if method in own_methods:
                    continue
                class_src = self._find_class_source(cls)
                if class_src is None:
                    continue
                cls_methods = {m.group(1) for m in self._METHOD_NAME_RE.finditer(class_src)}
                if method not in cls_methods:
                    unknown.append(
                        f"this.{prop}.{method}() — class {cls} has no method '{method}' "
                        f"(existing: {', '.join(sorted(cls_methods)[:10])})"
                    )
            return unknown
        except Exception:
            return []

    def generate_code(
        self,
        task: DevelopmentTask,
        requirements: StructuredRequirements,
        context: List[CodeChunk],
        existing_content: str = None,
        allowed_files: List[str] = None,
        readonly_files: List[str] = None,
    ) -> GeneratedCode:
        """
        Generate code for a specific development task.
        
        Args:
            task: Development task to implement
            requirements: Structured requirements
            context: Retrieved code context
            
        Returns:
            Generated code with metadata
        """
        print("\n" + "="*80)
        print("[ENTER] CodeGeneratorAgent.generate_code() in code_generator.py")
        print("   Purpose: Generate actual code using LLM with RAG context")
        print(f"   Task: {task.id} - {task.title}")
        print(f"   File: {task.file_path}")
        print("="*80)
        logger.info(f"Generating code for task: {task.id} - {task.title}")
        
        self._current_existing_content = existing_content
        self._current_allowed_files = set(allowed_files or [])
        self._current_readonly_files = set(readonly_files or [])
        
        # ── LSP context injection for HTML/Angular templates ──────────────────
        # Before generating, query the TypeScript LSP for the sibling controller's
        # declared class members. This gives the HTML generator exact property names
        # instead of invented ones — the same capability that makes Cursor accurate.
        _lsp_context_block = ""
        _task_ext = Path(task.file_path).suffix.lower()
        if _task_ext in (".html", ".htm") and getattr(self, "_workspace_path", None):
            try:
                from ticket_to_code.agents.lsp_client import TypeScriptLSP
                _ts_lsp = TypeScriptLSP(self._workspace_path)
                _stem = task.file_path.rsplit(".", 1)[0]
                for _ctrl_ext in (".ts", ".tsx"):
                    _ctrl_path = _stem + _ctrl_ext
                    # Check session map first (freshest content this run)
                    _ctrl_key = _ctrl_path.replace("\\", "/").lower()
                    _session = getattr(self, "_session_files", None) or {}
                    if _ctrl_key in _session:
                        # Parse the session content directly
                        from pathlib import Path as _Path
                        _tmp = _Path(self._workspace_path) / _ctrl_path
                        _members = _ts_lsp._extract_via_regex(_tmp, _ctrl_path) if _tmp.exists() else None
                        if not _members:
                            # Parse from session map content
                            import tempfile as _tf, os as _os
                            _td = _tf.mkdtemp()
                            try:
                                _fake = _Path(_td) / _Path(_ctrl_path).name
                                _fake.write_text(_session[_ctrl_key], encoding="utf-8")
                                _members = _ts_lsp._extract_via_regex(_fake, _ctrl_path)
                            finally:
                                import shutil; shutil.rmtree(_td, ignore_errors=True)
                    else:
                        _members = _ts_lsp.get_class_members(_ctrl_path)
                    if _members:
                        _lsp_context_block = (
                            f"\n\n=== LSP: SIBLING CONTROLLER MEMBERS (use EXACTLY these names) ===\n"
                            f"{_members.to_prompt_block()}\n"
                            f"=== END LSP CONTEXT ===\n"
                        )
                        logger.info(
                            f"  [LSP] Injected {len(_members.properties)} properties + "
                            f"{len(_members.methods)} methods from {_ctrl_path}"
                        )
                        break
            except Exception as _lsp_exc:
                logger.debug(f"  [LSP] Context injection failed: {_lsp_exc}")

        max_retries = 2
        for attempt in range(max_retries):
            try:
                # Build generation prompt — unified for all task types
                system_prompt = self._build_generation_prompt(task.language)
                user_prompt = self._build_user_prompt(
                    task, requirements, context,
                    existing_content=existing_content,
                    allowed_files=allowed_files,
                    readonly_files=readonly_files,
                )
                
                # Append LSP context to user prompt for HTML tasks
                if _lsp_context_block:
                    user_prompt = _lsp_context_block + user_prompt
                
                self.last_system_prompt = system_prompt
                self.last_user_prompt = user_prompt
                
                # Add delimiter reminder on retry
                if attempt > 0:
                    user_prompt += "\n\nIMPORTANT: You MUST use the <<<AVIATOR_CODE_START>>> / <<<AVIATOR_CODE_END>>> delimiters. Do not use JSON for the code content."
                
                messages = [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt)
                ]
                
                full_content = ""
                max_continuations = 3
                for cont_attempt in range(max_continuations):
                    # Invoke LLM (with retry on transient network errors)
                    response = llm_invoke(self.llm, messages)
                    # B10: validate each merged segment for boundary integrity
                    merged = full_content + response.content
                    if cont_attempt > 0:
                        # Check that the merge boundary is not mid-token (e.g. split inside a string)
                        _merge_ok = self._validate_merge_boundary(full_content, response.content, task)
                        if not _merge_ok:
                            logger.warning(
                                "B10: merge boundary validation failed on continuation %d "
                                "— re-requesting that segment",
                                cont_attempt,
                            )
                            messages.append(AIMessage(content=response.content))
                            messages.append(HumanMessage(
                                content=(
                                    "The previous continuation appears to have split mid-token or "
                                    "mid-block. Please restart from the last complete statement and "
                                    "continue to the <<<AVIATOR_CODE_END>>> delimiter."
                                )
                            ))
                            continue  # retry this continuation slot
                    full_content = merged

                    try:
                        # Optional best-effort debug dump; never affects generation flow.
                        self._maybe_dump_llm_io(task, messages, full_content, cont_attempt)

                        # Parse response
                        generated = self._parse_response(full_content, task)
                        logger.info(f"Code generated: {len(generated.content)} chars")

                        # ── Cross-tier call validation (pre-write) ──
                        _unknown_calls = self._validate_service_calls(generated.content, existing_content)
                        if _unknown_calls:
                            if cont_attempt < max_continuations - 1:
                                logger.warning(
                                    f"  ⛔ Hallucinated service call(s) in generated code — "
                                    f"requesting correction before writing: {_unknown_calls}"
                                )
                                messages.append(AIMessage(content=response.content))
                                messages.append(HumanMessage(
                                    content=(
                                        "Your generated code calls methods that DO NOT EXIST:\n"
                                        + "\n".join(f"  - {c}" for c in _unknown_calls)
                                        + "\n\nFix the code: use ONLY methods that exist on the "
                                        "injected services (their real methods were listed in the "
                                        "context above). Prefer REUSING an existing service "
                                        "capability over inventing a new method. Do not invent "
                                        "methods or endpoints. Re-output the FULL corrected code "
                                        "between the delimiters."
                                    )
                                ))
                                full_content = ""
                                continue  # re-invoke LLM with the corrective context
                            else:
                                logger.error(
                                    f"  ⛔ Hallucinated service call(s) persist after retries — "
                                    f"writing anyway (TSC/build gates will verify): {_unknown_calls}"
                                )

                        # ── Post-generation: record in ImplementationState ──
                        _impl = getattr(self, "_impl_state", None)
                        if _impl is not None:
                            try:
                                _impl.record_generated(
                                    task_id=task.id,
                                    file_path=task.file_path,
                                    content=generated.content,
                                )
                            except Exception as _rec_exc:
                                logger.debug(
                                    f"  [ImplementationState] record_generated failed: {_rec_exc}"
                                )

                        return generated
                    except ValueError as e:
                        if "AVIATOR_CODE_END missing" in str(e) and cont_attempt < max_continuations - 1:
                            logger.info(f"LLM output truncated on attempt {cont_attempt + 1}, asking to continue...")
                            messages.append(AIMessage(content=response.content))
                            messages.append(HumanMessage(content="Your output was truncated before closing the <<<AVIATOR_CODE_END>>> tag. Please continue EXACTLY from where you left off. Do not repeat what you have already written. Just output the next characters."))
                        else:
                            raise e
                
            except (json.JSONDecodeError, ValueError) as e:
                _err_msg = str(e)
                if attempt < max_retries - 1:
                    logger.warning(f"Generation failed (attempt {attempt + 1}/{max_retries}), retrying: {_err_msg[:200]}")
                    
                    # ── Fix 8: On retry after SEARCH error, re-read file + refresh line numbers ──
                    # If the error message contains actual file context, the LLM saw the real code.
                    # But in case other tasks modified the file, re-read it now.
                    if "SEARCH block not found" in _err_msg and task.task_type.value == "modify":
                        _retry_path = Path(self.workspace_path or ".") / task.file_path
                        if _retry_path.exists():
                            try:
                                _fresh_content = _retry_path.read_text(encoding='utf-8')
                                self._current_existing_content = _fresh_content
                                
                                # Re-build numbered content for the retry prompt
                                def _numbered_retry(text: str) -> str:
                                    lines = text.split('\n')
                                    return '\n'.join(f'{i+1:4d} | {ln}' for i, ln in enumerate(lines[:min(len(lines), 50)]))
                                
                                _fresh_numbered = _numbered_retry(_fresh_content)
                                
                                # Re-build the user prompt with the fresh content + error message
                                user_prompt = self._build_user_prompt(
                                    task, requirements, context,
                                    existing_content=_fresh_content,
                                    allowed_files=allowed_files,
                                    readonly_files=readonly_files,
                                )
                                
                                # Append error context so LLM learns what went wrong
                                user_prompt += (
                                    f"\n\n⚠️  RETRY ATTEMPT {attempt + 1}:\n"
                                    f"Your previous SEARCH block did not match the file. "
                                    f"Here is the CURRENT file state (may have changed since your first attempt):\n"
                                    f"```\n{_fresh_numbered}\n```\n"
                                    f"Error was: {_err_msg[:500]}\n"
                                    f"Copy 3-4 lines EXACTLY from the current file above into your SEARCH block."
                                )
                                
                                if _lsp_context_block:
                                    user_prompt = _lsp_context_block + user_prompt
                                
                                # Rebuild message stack with the updated prompt
                                messages = [
                                    SystemMessage(content=system_prompt),
                                    HumanMessage(content=user_prompt)
                                ]
                                logger.info(f"Retry: refreshed file content and re-numbered for {task.file_path}")
                            except Exception as refresh_err:
                                logger.warning(f"Could not refresh file on retry: {refresh_err}")
                                # Fall through with original messages
                        continue
                    continue
                    # Last attempt - try aggressive fallback
                    logger.error(f"All retry attempts exhausted for task {task.id}")
                    logger.error(f"Code generation failed: {e}", exc_info=True)
                    raise RuntimeError(f"Failed to generate code for task {task.id}: {e}")
            except Exception as e:
                logger.error(f"Code generation failed: {e}", exc_info=True)
                raise RuntimeError(f"Failed to generate code for task {task.id}: {e}")
    
    # ── B10: Merge boundary validation ────────────────────────────────────────

    @staticmethod
    def _validate_merge_boundary(
        before: str, after: str, task: "DevelopmentTask"
    ) -> bool:
        """
        B10: Lightweight heuristic check that a continuation merge is syntactically sound.

        Checks:
          1. The join point is not inside an unterminated string literal
             (i.e. the before-portion has balanced single/double quotes).
          2. For code inside a SEARCH/REPLACE block, the boundary is not inside
             an indented code token (split mid-identifier or mid-comment).

        Returns True if the merge looks safe, False if it should be re-requested.

        This is intentionally conservative (may return True for some bad merges)
        to avoid excessive re-requests.  The PatchValidator/build step is the
        authoritative correctness gate.
        """
        if not before or not after:
            return True  # nothing to validate

        # Only validate the tail of `before` + head of `after`
        tail = before[-200:]
        head = after[:200]

        # Heuristic 1: unclosed string at merge boundary?
        # Count unescaped quote characters in the tail region.
        def _unbalanced_quotes(s: str, ch: str) -> bool:
            count = 0
            escaped = False
            for c in s:
                if escaped:
                    escaped = False
                    continue
                if c == "\\":
                    escaped = True
                    continue
                if c == ch:
                    count += 1
            return count % 2 != 0

        for quote_char in ('"', "'", "`"):
            if _unbalanced_quotes(tail, quote_char):
                logger.debug(
                    "B10: unbalanced %r quote at merge boundary for %s",
                    quote_char, getattr(task, "file_path", "?"),
                )
                return False

        # Heuristic 2: head starts with a continuation of an identifier (split mid-word)?
        head_stripped = head.lstrip()
        if head_stripped and head_stripped[0].isalnum() or head_stripped[:1] in ("_",):
            # Tail ends mid-word (last char is alphanumeric/underscore)
            tail_last = tail.rstrip()[-1:] if tail.rstrip() else ""
            if tail_last and (tail_last.isalnum() or tail_last == "_"):
                logger.debug(
                    "B10: mid-identifier split detected at merge boundary for %s",
                    getattr(task, "file_path", "?"),
                )
                return False

        return True

    def _build_generation_prompt(self, language: ProgrammingLanguage) -> str:
        
        language_guidelines = {
            ProgrammingLanguage.CSHARP: self._csharp_guidelines(),
            ProgrammingLanguage.TYPESCRIPT: self._typescript_guidelines(),
            ProgrammingLanguage.PYTHON: self._python_guidelines(),
            ProgrammingLanguage.JAVA: self._java_guidelines(),
            ProgrammingLanguage.HTML: self._html_guidelines(),
            ProgrammingLanguage.SCSS: self._scss_guidelines(),
            ProgrammingLanguage.JSON: self._json_guidelines(),
            ProgrammingLanguage.XML: self._xml_guidelines(),
            ProgrammingLanguage.YAML: self._yaml_guidelines(),
            ProgrammingLanguage.PROPERTIES: self._properties_guidelines(),
        }
        
        lang_guide = language_guidelines.get(
            language, 
            "Follow language best practices."
        )
        
        return f"""You are a Senior Maintainer working on an enterprise production codebase.

YOUR ROLE IS NOT ARCHITECT. YOUR ROLE IS SURGICAL MODIFIER.

ABSOLUTE RULES — NEVER VIOLATE:
1. ONLY operate on the ONE file explicitly specified in the FROZEN MODIFICATION SCOPE. Period.
2. For MODIFY tasks: Apply the SMALLEST change that satisfies the requirement.
3. For CREATE tasks: Generate the COMPLETE content for the EXACT file path specified.
4. NEVER remove existing methods, classes, or logic unless explicitly required by the ticket. Preserve surrounding code exactly.
5. NEVER rewrite existing methods to hardcode return values if the requirement is to update a configuration constant or version string.
6. PREFER modifying variables, constants, and configuration objects over modifying business logic methods.
7. If constructors have existing parameters, PRESERVE ALL of them — only ADD new ones.

CRITICAL — PLANNER TASK ADHERENCE:
- The PLANNER DIRECTIVE section below contains the exact task assigned to you by the planning agent.
- You MUST execute EXACTLY what the planner requested — no more, no less.
- The planner's task is your contract. Deviating from it is a failure.

MINDSET:
- Think diff, not rewrite.
- Touch only what must change.
- Leave everything else byte-for-byte identical.
- When in doubt: copy it unchanged. Never delete.

PRESERVE ENCODING: If you are asked to update text, preserve any special characters (like trademark symbols) in the surrounding code. Do NOT output invalid or replacement characters (e.g. \\ufffd) for symbols.

{lang_guide}

RESPOND using EXACTLY this delimiter format:

<<<AVIATOR_CODE_START>>>
[Output one or more EDIT blocks for MODIFY tasks, or the full file for CREATE tasks]

── FOR MODIFY TASKS: Use SEARCH/REPLACE edits ───────────────────────────

Each edit replaces an exact unique substring of the existing file.
Use git-merge-conflict-style delimiters (7 angle brackets + keyword):

<<<<<<< SEARCH
exact existing code to find (must match EXACTLY ONCE in the file)
=======
replacement code
>>>>>>> REPLACE

You may output MULTIPLE edits in a single response:

<<<<<<< SEARCH
import {{ ExistingService }} from './existing.service';
=======
import {{ ExistingService }} from './existing.service';
import {{ NewService }} from './new.service';
>>>>>>> REPLACE

<<<<<<< SEARCH
  onMemberAdd(member: any) {{
    this.displayedMembers.push(member);
  }}
=======
  onMemberAdd(member: any) {{
    if (this.isDuplicate(member)) {{
      return;
    }}
    this.displayedMembers.push(member);
  }}
>>>>>>> REPLACE

RULES FOR SEARCH/REPLACE EDITS:
- The SEARCH block must be an EXACT substring of the EXISTING FILE shown below — copy it character-for-character including indentation
- The SEARCH block must match EXACTLY ONCE in the file. If it could match multiple places, include more surrounding lines to make it unique
- If the SEARCH block is not found → the edit FAILS and you will be asked to retry
- For ADDING new code (new method, new property): use a small anchor from existing code as SEARCH, and include anchor + new code as REPLACE
- For INSERTING a new method after an existing method: use the closing brace + any trailing lines of the preceding method as SEARCH, include that same closing brace + the new method as REPLACE
- For ADDING imports: use the last existing import line as SEARCH, include that import + new import as REPLACE
- NEVER output the full file for a MODIFY task — only the SEARCH/REPLACE edits

⛔ CRITICAL — FULL FILE OUTPUT = INSTANT REJECTION:
- For MODIFY tasks, if you output a full file (starting with 'package ', 'import ', class declarations, etc.)
  instead of SEARCH/REPLACE blocks, your output will be AUTOMATICALLY REJECTED and you will waste a retry.
- ALWAYS use <<<<<<< SEARCH / ======= / >>>>>>> REPLACE format for MODIFY tasks.
- Copy 3-5 lines EXACTLY from the existing file as your SEARCH anchor.
- Only include the lines that are changing plus minimal surrounding context.
- Even if the file is very large, you MUST use targeted SEARCH/REPLACE edits — never rewrite the whole file.

── FOR CREATE TASKS: Output the entire new file ────────────────────────

For CREATE tasks, output the complete file content directly (no SEARCH/REPLACE blocks).

<<<AVIATOR_CODE_END>>>
<<<AVIATOR_META_START>>>
{{"imports": [], "documentation": "One sentence: what exact change was made and why"}}
<<<AVIATOR_META_END>>>

STRATEGY SELECTION RULES:
- For MODIFY tasks: ALWAYS use SEARCH/REPLACE blocks with <<<<<<< SEARCH / ======= / >>>>>>> REPLACE delimiters
- For CREATE tasks: output the entire new file content inside <<<AVIATOR_CODE_START>>> without any SEARCH/REPLACE blocks
- The META block must be valid JSON with "imports" (array) and "documentation" (string)
- No text outside the two blocks"""


    def _java_guidelines(self) -> str:
        """Java / Spring Boot specific guidelines"""
        return """
JAVA GUIDELINES:
- Use camelCase for methods/variables, PascalCase for classes
- Annotate Spring beans with @Service, @Component, @RestController, @Repository as appropriate
- Use constructor injection (not @Autowired on fields)
- Add Javadoc for public methods
- Use SLF4J (private static final Logger log = LoggerFactory.getLogger(Foo.class))
- Prefer Optional<> over returning null
- Use final fields where possible
- Follow existing package/import structure already in the file
"""

    def _csharp_guidelines(self) -> str:
        """C# specific guidelines"""
        return """
C# GUIDELINES:
- Use PascalCase for public members, camelCase for private
- Add XML documentation (///) for public members
- Use async/await for I/O operations
- Implement IDisposable where needed
- Use nullable reference types (?)
- Follow Microsoft coding conventions
- Use LINQ where appropriate
- Prefer expression body for simple methods
"""
    
    def _typescript_guidelines(self) -> str:
        """TypeScript specific guidelines"""
        return """
TYPESCRIPT GUIDELINES:
- Use strict type checking
- Define interfaces for all data structures
- Use async/await for promises
- Use arrow functions for callbacks
- Follow React hooks rules (if applicable)
- Use TypeScript generics where appropriate
- Export types and interfaces
- Use const for immutable values
"""
    
    def _python_guidelines(self) -> str:
        """Python specific guidelines"""
        return """
PYTHON GUIDELINES:
- Follow PEP 8 style guide
- Use type hints for function signatures
- Use docstrings (Google or NumPy style)
- Use async/await for I/O operations
- Use dataclasses or Pydantic models where appropriate
- Handle exceptions specifically (avoid bare except)
- Use context managers (with statement)
"""

    def _html_guidelines(self) -> str:
        """HTML / Angular / Vue / JSX template guidelines"""
        return """
HTML / TEMPLATE GUIDELINES:
- This is a markup template, not a script. Preserve existing indentation and tag structure exactly.
- Respect the framework already in use (detect from surrounding syntax): Angular ([disabled], *ngIf, {{ 'key' | translate }}), Vue (:disabled, v-if), or JSX (disabled={...}).
- To disable/lock a control, bind the framework's disabled/readonly attribute to the component flag rather than removing the element.
- For localized text, reference the existing i18n key via the framework's translate mechanism; do not hardcode user-facing strings.
- Keep attribute ordering and quoting style consistent with the surrounding markup.
- Never convert template syntax into script; do not add <script> blocks.

ANGULAR PROPERTY NAME RULE (CRITICAL — causes TS2339 build failures if violated):
Before writing ANY new Angular binding (*ngIf, {{ }}, [attr]), you MUST look at the
SESSION CONTEXT section below (the current content of the sibling .ts controller file).
Extract the EXACT class property names declared there and use ONLY those names.

DO NOT invent new property names from the ticket description.
INSTEAD: read what the .ts controller already declares (or what the TS task will add based
on its description), then use those EXACT property names in the template.

Example:
  TS declares: `isExistingMemberInProject: boolean = false;`
  HTML MUST use: `*ngIf="dmember.isExistingMemberInProject"` — NOT `*ngIf="isUserProjectMember"`

If the SESSION CONTEXT does not yet show the .ts, look at the TS task description in the
PLANNER DIRECTIVE — it describes what properties the controller will add. Use those names exactly.

NEVER use a condition like *ngIf="obj.organization" to represent "existing org" when
'organization' is ALWAYS set by the controller. Use the dedicated flag property instead.
"""

    def _scss_guidelines(self) -> str:
        """SCSS / CSS / LESS stylesheet guidelines"""
        return """
SCSS / CSS GUIDELINES:
- Modify only the selectors/rules required. Preserve nesting, variables, mixins, and ordering.
- Reuse existing variables and mixins instead of hardcoding values already defined.
- Prefer flexible layout properties over fixed pixel heights when addressing layout/alignment.
- Do not restructure unrelated rules or reformat the whole file.
"""

    def _json_guidelines(self) -> str:
        """JSON / i18n / config guidelines"""
        return """
JSON GUIDELINES:
- Output MUST remain strictly valid JSON (double-quoted keys/strings, no comments, no trailing commas).
- Preserve existing key ordering and indentation; insert new keys in the correct nested location.
- For i18n bundles, add the key in the same nesting convention as sibling keys.
- Do not change value types (string/number/bool) unless the task explicitly requires it.
"""

    def _xml_guidelines(self) -> str:
        """XML / POM / config guidelines"""
        return """
XML GUIDELINES:
- Output MUST remain well-formed XML with correctly closed and nested tags.
- Preserve namespaces, attribute ordering, and indentation of the surrounding elements.
- Insert new elements in the schema-appropriate location; do not reorder unrelated nodes.
"""

    def _yaml_guidelines(self) -> str:
        """YAML / manifest guidelines"""
        return """
YAML GUIDELINES:
- Indentation is significant: use spaces (never tabs) and match the existing indent width exactly.
- Preserve key ordering and existing anchors/aliases; insert new keys under the correct parent.
- Quote values only where the surrounding file quotes them; keep list/map style consistent.
- Output MUST parse as valid YAML.
"""

    def _properties_guidelines(self) -> str:
        """.properties / .env key=value guidelines"""
        return """
PROPERTIES / ENV GUIDELINES:
- Each line is a single key=value (or key: value) pair; preserve the existing separator style.
- Do not quote values unless the surrounding file already quotes them.
- Add or update only the specified keys; keep unrelated keys and comments untouched.
"""
    
    def _build_user_prompt(
        self,
        task: DevelopmentTask,
        requirements: StructuredRequirements,
        context: List[CodeChunk],
        existing_content: str = None,
        allowed_files: List[str] = None,
        readonly_files: List[str] = None,
    ) -> str:
        """Build user prompt with task, requirements, and context"""
        
        context_str = self._format_context(context)
        
        # ── REUSE-FIRST directive (set by plan_node's capability discovery) ──
        # Tells the generator which existing service methods to CALL instead of
        # inventing new ones or extending the service under the ticket.
        # The directive is set on the code_generator MODULE (not per-instance)
        # to avoid race conditions during concurrent ticket runs.
        # We import sys here to ensure we get the module-level attribute correctly.
        import sys as _sys
        _reuse_directive_module = getattr(_sys.modules[__name__], "_reuse_directive", "")
        if not _reuse_directive_module:
            # fallback for legacy/debug scenarios (module attribute missing)
            _reuse_directive_module = getattr(self, "_reuse_directive", "")
        _reuse_block = f"{_reuse_directive_module}\n" if _reuse_directive_module else ""

        change_type = task.task_type.value  # "modify" or "create"

        # ── FROZEN SCOPE BLOCK ───────────────────────────────────────────────
        # Fix 1: Validate allowed_methods against actual file content.
        # Hallucinated method names cause every SEARCH block to fail → silent skip.
        if existing_content and task.allowed_methods:
            validated = [m for m in task.allowed_methods
                         if re.search(r'\b' + re.escape(m) + r'\b', existing_content)]
            dropped = set(task.allowed_methods) - set(validated)
            if dropped:
                import logging as _lg
                _lg.getLogger(__name__).warning(
                    "allowed_methods validation: removing non-existent methods %s from task %s — "
                    "these would cause SEARCH/REPLACE to fail silently", sorted(dropped), task.id
                )
            task.allowed_methods = validated

        allowed_methods_str = (
            ", ".join(task.allowed_methods) if task.allowed_methods else "NOT SPECIFIED — keep change minimal"
        )
        target_method_str = task.target_method or "NOT SPECIFIED — use keyword context to find it"
        target_class_str  = task.target_class  or "NOT SPECIFIED"
        new_file_str      = "ALLOWED (this is a create task)" if task.new_file_creation_allowed else "FORBIDDEN"

        scope_block = f"""
╔══════════════════════════════════════════════════════════════╗
║  FROZEN MODIFICATION SCOPE — DO NOT DEVIATE                  ║
╠══════════════════════════════════════════════════════════════╣
║  ALLOWED FILE    : {task.file_path:<43}║
║  OPERATION       : {change_type.upper():<43}║
║  TARGET CLASS    : {target_class_str:<43}║
║  TARGET METHOD   : {target_method_str:<43}║
║  ALLOWED METHODS : {allowed_methods_str:<43}║
║  NEW FILE CREATE : {new_file_str:<43}║
╚══════════════════════════════════════════════════════════════╝

ENFORCEMENT:
- If ALLOWED METHODS is listed: ONLY modify those methods. Touch NOTHING else.
- If TARGET METHOD is listed: go directly to that method. Do not touch neighbors.
- NEW FILE CREATE = FORBIDDEN means: do NOT create any new file anywhere.
- If you find yourself writing code outside the listed method(s), STOP."""

        # ── Phase 2: Inject edit_anchors into prompt ──────────────────────────
        _edit_anchor_block = ""
        _anchors = getattr(task, "edit_anchors", []) or []
        if _anchors:
            _anchor_lines = []
            for _a in _anchors:
                _action = _a.get("action", "?")
                _method = _a.get("method_name", _a.get("anchor_method", "?"))
                _placement = _a.get("placement", "?")
                _anchor = _a.get("anchor_method", "")
                _desc = _a.get("description", "")
                _anchor_lines.append(
                    f"  • {_action}: {_method} — placement={_placement}"
                    + (f", scope={_anchor}" if _anchor else "")
                    + (f" — {_desc}" if _desc else "")
                )
            _edit_anchor_block = (
                "\n\n🎯 EDIT PLACEMENT ANCHORS (from planner — tells you WHERE to place code):\n"
                + "\n".join(_anchor_lines)
                + "\n\n⚠️  Follow these anchors exactly. If the planner says 'sibling_after findById', "
                "add the method AFTER findById's closing brace, NOT inside findById's body."
            )

        # ── TICKET-WIDE SCOPE LOCK ────────────────────────────────────────────
        scope_lock_block = ""
        if allowed_files or readonly_files:
            writable_lines = "\n".join(f"  ✅  {p}" for p in (allowed_files or []))
            ro_lines       = "\n".join(f"  🚫  {p}" for p in (readonly_files  or []))
            scope_lock_block = f"""

╔══════════════════════════════════════════════════════════════════╗
║  TICKET-WIDE FILE SCOPE LOCK                                     ║
╠══════════════════════════════════════════════════════════════════╣
║  YOU MAY MODIFY ONLY (writable):                                 ║
{writable_lines}
║                                                                  ║
║  READ-ONLY (for reference only — DO NOT WRITE):                  ║
{ro_lines}
╠══════════════════════════════════════════════════════════════════╣
║  ANY modification to a file not listed above = SCOPE VIOLATION   ║
╚══════════════════════════════════════════════════════════════════╝"""

        # ── Change Surface Targeting ─────────────────────────────────────────
        # If the task carries ownership_analysis from the Ownership Analysis Agent,
        # surface the exact symbols to change so the LLM targets them precisely.
        change_surface_block = ""
        oa = getattr(task, "ownership_analysis", None) or {}
        if isinstance(oa, dict) and oa.get("change_surface"):
            surfaces = oa["change_surface"]
            surface_lines = "\n".join(
                f"  - symbol={cs.get('symbol','?')}  type={cs.get('type','?')}  snippet: {cs.get('snippet','')[:120]}"
                for cs in surfaces
            )
            change_surface_block = f"""

CHANGE SURFACE (from Ownership Analysis Agent — TARGET THESE SYMBOLS PRECISELY):
{surface_lines}

INSTRUCTION: Go directly to the symbol(s) listed above. Apply the minimal change there.
Do NOT scan the entire file for other locations to change.
"""

        existing_section = ""
        modify_instruction = f"Now generate the new file content for {task.file_path}. Only create this one file."
        if existing_content:
            method_excerpt = self._extract_method_context(
                existing_content, task.target_method, task.allowed_methods
            )
            
            # ── Skeleton + Verbatim Architecture (AI IDE technique) ─────────────
            # Two distinct content roles that must NEVER be mixed:
            #
            #   SKELETON (smart_extract): structural outline for LLM awareness.
            #     The LLM sees method signatures, class structure, line counts.
            #     This content is explicitly marked "DO NOT copy from this section"
            #     because it contains markers (// ... omitted) that don't exist in
            #     the original file.
            #
            #   VERBATIM (extract_exact_methods): exact uncompressed source bytes.
            #     The LLM's SEARCH blocks must be substrings of THIS content.
            #     Uses tree-sitter for grammar-correct method boundaries.
            #     Guaranteed to match _apply_str_replace_edits() because it IS
            #     the original file content, zero transformations.
            #
            # This mirrors Aider's architecture:
            #   - repo map (tree-sitter) = structural awareness, never edit source
            #   - edit format (SEARCH/REPLACE) = operates on verbatim file content

            display_content = existing_content
            # Target-First Generation: When modifying, drive prompt content by ChangeTarget / anchor_methods
            # rather than arbitrary file size thresholds.
            anchor_methods = _get_anchor_methods(task)
            should_target_extract = (
                task.task_type.value == "modify"
                and (bool(anchor_methods) or len(existing_content) > 1000)
            )
            if should_target_extract:
                try:
                    from ticket_to_code.agents.smart_extract import (
                        smart_extract, extract_exact_methods
                    )

                    # Section 1: SKELETON — structural outline (awareness only)
                    skeleton_content = smart_extract(
                        content=existing_content,
                        file_path=task.file_path,
                        allowed_methods=anchor_methods or task.allowed_methods,
                        target_method=task.target_method,
                        edit_description=task.description,
                    )

                    # Section 2: VERBATIM — exact method bodies (edit source)
                    verbatim_content, matched_boundaries = extract_exact_methods(
                        content=existing_content,
                        file_path=task.file_path,
                        anchor_methods=anchor_methods,
                    )

                    if matched_boundaries:
                        # Two-section prompt: skeleton for awareness, verbatim for editing
                        display_content = (
                            f"=== FILE STRUCTURE (context only — DO NOT copy text from this section) ===\n"
                            f"{skeleton_content}\n\n"
                            f"=== EXACT SOURCE (your SEARCH block MUST be a verbatim substring of THIS section) ===\n"
                            f"{verbatim_content}"
                        )
                        logger.info(
                            f"Target-First Skeleton+Verbatim: {task.file_path} "
                            f"({len(existing_content):,} → skeleton={len(skeleton_content):,} + "
                            f"verbatim={len(verbatim_content):,} chars, "
                            f"{len(matched_boundaries)} methods matched)"
                        )
                    elif skeleton_content and len(skeleton_content) < len(existing_content):
                        # Anchor methods not matched directly — use structural skeleton outline
                        display_content = (
                            f"=== FILE STRUCTURE (context only — DO NOT copy text from this section) ===\n"
                            f"{skeleton_content}\n\n"
                            f"=== FILE CONTENT ===\n"
                            f"{existing_content[:2500]}"
                        )
                        logger.info(
                            f"Target-First Skeleton outline for {task.file_path}: "
                            f"{len(existing_content):,} → {len(skeleton_content):,} chars"
                        )
                except Exception as e:
                    logger.warning(f"Target-First extraction failed for {task.file_path}, using full content: {e}")

            # ── Unified MODIFY path: show existing file for str_replace edits ──
            existing_section = f"""

EXISTING FILE CONTENT (this is what the SEARCH block must match against):
```
{display_content}
```

INSTRUCTION: Use <<<<<<< SEARCH / ======= / >>>>>>> REPLACE blocks to make surgical changes.
Each SEARCH block must be an EXACT, UNIQUE substring of the EXACT SOURCE section above.
If you see a "FILE STRUCTURE" section, that is context ONLY — do NOT copy text from it.
For adding NEW code: use a small existing anchor as SEARCH, and include anchor + new code as REPLACE.
For modifying existing code: copy the exact lines to change as SEARCH, and provide the modified version as REPLACE.
Do NOT output the full file. Only output SEARCH/REPLACE blocks."""
            modify_instruction = f"Return SEARCH/REPLACE blocks targeting {task.file_path}."

        # ── Layer 1: Import Context (Cursor / Aider / Devin technique) ─────────
        import_context_block = self._build_import_context(
            existing_content, task, context
        )
        # Incremental cross-file awareness: inject fresh content of files this
        # task imports that were regenerated earlier in the same run.
        session_context_block = self._build_session_context(existing_content, task)

        # ── Cross-file contract (planner produces/consumes) ──────────────────
        # The planner declares what each task produces and consumes.
        # This was previously IGNORED — now we surface it so the LLM knows
        # the exact method names/signatures it must create or call.
        contract_context_block = self._build_contract_context(task)
        if contract_context_block:
            logger.info(
                f"  [Contract] Injected cross-file contract for {task.id} "
                f"({len(contract_context_block)} chars)"
            )

        # ── Cross-File Intelligence (ContextAssembler pipeline) ─────────────────
        # The ContextAssembler is the SINGLE entry point for cross-file context.
        # It runs an 8-stage pipeline: task metadata → blueprints → handoffs →
        # rolling context → dependencies → repo evidence → validation → assembly.
        # Each context block is labeled with its authority level.
        # Falls back to DependencyContextBuilder if ContextAssembler fails.
        #
        # Additionally, ImplementationState.to_prompt_context() provides
        # authoritative handoff data (verified exports from earlier tasks).
        # This was previously built but never called — now we wire it in.
        blueprint_context_block = ""
        _handoff_context_block = ""
        _impl_state_pre = getattr(self, "_impl_state", None)
        if _impl_state_pre is not None:
            try:
                _handoff_ctx = _impl_state_pre.to_prompt_context(task)
                if _handoff_ctx:
                    _handoff_context_block = _handoff_ctx
                    logger.info(
                        f"  [ImplementationState] Injected {len(_handoff_ctx)} chars "
                        f"cross-file context for {task.id} "
                        f"(handoffs={len(_impl_state_pre.get_handoffs_for_dependencies(task.dependencies or []))})"
                    )
            except Exception as _hc_exc:
                logger.debug(f"  [ImplementationState] to_prompt_context failed: {_hc_exc}")
        _impl_state = getattr(self, "_impl_state", None)
        if _impl_state is not None:
            try:
                from ticket_to_code.agents.context_assembler import ContextAssembler
                from ticket_to_code.agents.dependency_context_builder import DependencyContextBuilder
                _resolver = getattr(self, "_symbol_resolver", None)
                _ws = getattr(self, "_workspace_path", None)
                dep_builder = DependencyContextBuilder(
                    symbol_resolver=_resolver,
                    workspace_path=Path(_ws) if _ws else None,
                )
                assembler = ContextAssembler(
                    impl_state=_impl_state,
                    dep_builder=dep_builder,
                    symbol_resolver=_resolver,
                    workspace_path=Path(_ws) if _ws else None,
                )
                blueprint_context_block = assembler.assemble(task)
                if blueprint_context_block:
                    logger.info(
                        f"  [ContextAssembler] Injected {len(blueprint_context_block)} chars "
                        f"for {task.id}"
                    )
                    # Log conflicts for debugging
                    if assembler.conflicts:
                        logger.info(
                            f"  [ContextAssembler] Resolved {len(assembler.conflicts)} "
                            f"evidence conflict(s)"
                        )
            except Exception as _asm_exc:
                logger.debug(f"  [ContextAssembler] Failed, falling back: {_asm_exc}")
                # Fallback: use DependencyContextBuilder directly
                try:
                    from ticket_to_code.agents.dependency_context_builder import DependencyContextBuilder
                    _resolver = getattr(self, "_symbol_resolver", None)
                    _ws = getattr(self, "_workspace_path", None)
                    dep_builder = DependencyContextBuilder(
                        symbol_resolver=_resolver,
                        workspace_path=Path(_ws) if _ws else None,
                    )
                    blueprint_context_block = dep_builder.build_context(task, _impl_state)
                    if blueprint_context_block:
                        logger.info(
                            f"  [Blueprint] Fallback dependency context for {task.id} "
                            f"({len(blueprint_context_block)} chars)"
                        )
                except Exception as _bp_exc:
                    logger.debug(f"  [Blueprint] Fallback also failed: {_bp_exc}")

        # ── Fix 1: Constructor preservation guard ─────────────────────────────
        # When modifying a TypeScript/Java file, extract ALL existing constructor
        # params and inject them with "PRESERVE ALL" to prevent the LLM from
        # dropping injections it didn't explicitly add. This is the root cause of
        # "contractService missing from constructor" class of errors.
        _constructor_guard = ""
        _ext_for_ctor = Path(task.file_path).suffix.lower()
        if (existing_content and task.task_type.value == "modify"
                and _ext_for_ctor in (".ts", ".tsx", ".java")):
            _ctor_params = self._extract_constructor_params(existing_content, _ext_for_ctor)
            if _ctor_params:
                _constructor_guard = (
                    f"\n\n⚠️  CONSTRUCTOR PRESERVATION RULE:\n"
                    f"The existing constructor has these parameters — PRESERVE ALL OF THEM.\n"
                    f"Only ADD new parameters; never remove any existing injection:\n"
                    f"```\n{_ctor_params}\n```\n"
                )

        # ── PLANNER DIRECTIVE: surface the exact planner task so LLM cannot drift ──
        planner_directive = f"""
╔══════════════════════════════════════════════════════════════════╗
║  PLANNER DIRECTIVE — THIS IS YOUR CONTRACT                       ║
╠══════════════════════════════════════════════════════════════════╣
║  The planning agent assigned you the following task:              ║
║                                                                  ║
║  TASK: {task.title:<55}║
║  FILE: {task.file_path:<55}║
║  TYPE: {change_type.upper():<55}║
║                                                                  ║
║  DESCRIPTION:                                                    ║
║  {task.description[:120]:<63}║
╠══════════════════════════════════════════════════════════════════╣
║  You MUST implement EXACTLY this task.                            ║
║  Do NOT substitute a different file or a different change.        ║
║  If the task says CREATE a file, you CREATE that file.            ║
║  If the task says MODIFY a method, you MODIFY that method.        ║
╚══════════════════════════════════════════════════════════════════╝"""

        _prompt = f"""\
{scope_block}{scope_lock_block}{_edit_anchor_block}
{planner_directive}
{_reuse_block}

TASK ID: {task.id}
TASK: {task.title}
DESCRIPTION: {task.description}

FILE PATH: {task.file_path}
CHANGE TYPE: {change_type.upper()}
LANGUAGE: {task.language.value}

FUNCTIONAL REQUIREMENTS:
{self._format_list(requirements.functional_requirements)}

TECHNICAL REQUIREMENTS:
{self._format_list(requirements.technical_requirements)}

EDGE CASES TO HANDLE:
{self._format_list(requirements.edge_cases)}
{change_surface_block}{import_context_block}{session_context_block}{contract_context_block}{_handoff_context_block}{blueprint_context_block}{_constructor_guard}
AVAILABLE CODE CONTEXT (reference patterns only — do NOT copy wholesale):
{context_str}{existing_section}

DEPENDENCIES (from other tasks):
{self._format_list(task.dependencies) if task.dependencies else "None"}

{modify_instruction}""".strip()

        # ── Token accounting telemetry ────────────────────────────────────────
        # Store last prompt component sizes for per-file token breakdown.
        # These are observational only — never affect generation logic.
        self._last_session_context = session_context_block or ""
        self._last_contract_context = contract_context_block or ""
        self._last_impl_context = (
            (_handoff_context_block or "") + (blueprint_context_block or "")
        )

        return _prompt



    
    def _extract_method_context(
        self,
        file_content: str,
        target_method: str = None,
        allowed_methods: list = None,
        context_lines: int = 5
    ) -> str:
        """
        Extract ONLY the target method + context_lines above/below from the file.
        
        This is the MINIMAL CONTEXT principle: don't send the whole file as "context"
        when we know exactly which method to change.  Sending less context forces the
        LLM to make a targeted surgical change rather than a broad rewrite.
        
        Returns empty string if target_method is unknown (caller will use full file).
        """
        import re
        methods_to_find = []
        if target_method:
            methods_to_find.append(target_method)
        if allowed_methods:
            methods_to_find += [m for m in allowed_methods if m not in methods_to_find]

        if not methods_to_find:
            return ""

        lines = file_content.splitlines()
        excerpts = []

        for method_name in methods_to_find[:3]:  # max 3 methods
            # Find the line where this method is defined
            pattern = re.compile(
                rf"(?:def|function|async function|public|private|protected|static)?\s*"
                rf"\b{re.escape(method_name)}\s*\("
            )
            for i, line in enumerate(lines):
                if pattern.search(line):
                    start = max(0, i - context_lines)
                    # Find end: scan forward for closing brace or next method
                    depth = 0
                    end = i
                    for j in range(i, min(len(lines), i + 60)):
                        depth += lines[j].count("{") - lines[j].count("}")
                        if j > i and depth <= 0:
                            end = j + 1
                            break
                    else:
                        end = min(len(lines), i + 30)

                    excerpt_lines = lines[start:end + context_lines]
                    excerpt = "\n".join(excerpt_lines)
                    excerpts.append(f"[Method: {method_name}]\n{excerpt}")
                    break

        return "\n\n".join(excerpts) if excerpts else ""

    def _find_method_line_ranges(
        self,
        file_content: str,
        target_method: str = None,
        allowed_methods: list = None,
    ) -> dict:
        """Compute exact 1-indexed line ranges for target methods.

        Returns dict of {method_name: (start_line, end_line)} so the user prompt
        can proactively tell the LLM 'use REPLACE_LINES: 243-285' instead of
        making it scan the whole file.  This is the key difference between our
        system and Claude Code — we now give the LLM the exact range up-front.
        """
        import re
        methods_to_find = []
        if target_method:
            methods_to_find.append(target_method)
        if allowed_methods:
            methods_to_find += [m for m in allowed_methods if m not in methods_to_find]
        if not methods_to_find:
            return {}

        lines = file_content.splitlines()
        ranges: dict = {}

        for method_name in methods_to_find[:5]:
            pattern = re.compile(
                rf"(?:def|function|async function|public|private|protected|static)?\s*"
                rf"\b{re.escape(method_name)}\s*\("
            )
            for i, line in enumerate(lines):
                if pattern.search(line):
                    depth = 0
                    end = i
                    for j in range(i, min(len(lines), i + 80)):
                        depth += lines[j].count("{") - lines[j].count("}")
                        if j > i and depth <= 0:
                            end = j
                            break
                    else:
                        end = min(len(lines) - 1, i + 30)
                    # 1-indexed, inclusive
                    ranges[method_name] = (i + 1, end + 1)
                    break

        return ranges

    # ── Layer 1: Cursor / Aider / Devin — Import Context Builder ─────────────

    # Attach a sqlite_store after construction so generate_code can query symbols.
    _sqlite_store = None
    # Attach the session-generated file map (path→content) for incremental context.
    _session_files = None
    # Attach the ImplementationState for blueprint-driven context + post-gen recording.
    # Set by workflow.py before generate_code(); None when running without the new system.
    _impl_state = None

    def _build_import_context(
        self,
        existing_content: str,
        task: "DevelopmentTask",
        context: list,
    ) -> str:
        """
        Build an IMPORT CONTEXT block injected into the LLM prompt.

        Combines three techniques:
          1. Cursor / Copilot Workspace — extract the current file's import block so
             the LLM can see existing imports and extend (not replace) them.
          2. Aider repo-map — surface where new symbols live by querying the SQLite
             symbol index for classes referenced in the task description and context.
          3. Devin two-pass — explicitly tell the LLM to emit new imports as a
             dedicated SEARCH/REPLACE block targeting the import section.
        """
        if not existing_content and not self._sqlite_store:
            return ""

        lines: list[str] = []

        # ── Part A: Existing import block (Cursor / Copilot Workspace style) ──
        if existing_content:
            import_lines: list[str] = []
            for line in existing_content.split("\n"):
                s = line.strip()
                if s.startswith(("import ", "from ", "using ", "package ", "require(")):
                    import_lines.append(line)
                elif s.startswith(("@", "//", "/*", " *", "#")):
                    # allow annotations and comments inside import block
                    if import_lines:
                        import_lines.append(line)
                elif not s:
                    # blank line — keep if we're inside the import block
                    if import_lines:
                        import_lines.append(line)
                elif s:
                    break  # first non-import, non-comment, non-blank content line
            if import_lines:
                block = "\n".join(import_lines).strip()
                lines.append(
                    f"\nEXISTING IMPORTS — PRESERVE EVERY LINE BELOW; only ADD new ones:\n```\n{block}\n```"
                )

        # ── Part B: Repo symbol map (Aider repo-map / Devin symbol index) ────
        if self._sqlite_store:
            # Collect candidate symbol names from: task description + context filenames
            candidate_symbols: list[str] = []
            desc_text = f"{task.title} {task.description}"
            # PascalCase words are likely class/interface names
            import re
            candidate_symbols += re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}\b", desc_text)
            for chunk in context:
                fp = chunk.get("file_path", "") if isinstance(chunk, dict) else getattr(chunk, "file_path", "")
                # e.g.  "ContractMemberModel.java" → "ContractMemberModel"
                stem = Path(fp).stem
                if stem and stem[0].isupper():
                    candidate_symbols.append(stem)
            # Deduplicate, skip very generic names
            skip = {"String", "List", "Map", "Set", "Optional", "Boolean", "Integer",
                    "Object", "Array", "Component", "Service", "Module", "Type"}
            seen: set[str] = set()
            unique_symbols = [s for s in candidate_symbols
                              if s not in skip and s not in seen and not seen.add(s)]  # type: ignore[func-returns-value]

            symbol_map_lines: list[str] = []
            for sym in unique_symbols[:20]:
                try:
                    rows = self._sqlite_store.search_symbols(
                        sym, kinds=["class", "interface", "enum", "type"], limit=3
                    )
                    for row in rows:
                        row_path = row["path"] if isinstance(row, dict) else getattr(row, "path", "")
                        row_name = row["name"] if isinstance(row, dict) else getattr(row, "name", sym)
                        if row_path:
                            import_hint = _build_import_hint(
                                row_name, row_path,
                                source_file_path=task.file_path,
                                workspace_path=str(getattr(self, "_workspace_path", "")),
                            )
                            if import_hint:
                                symbol_map_lines.append(f"  {row_name} → {import_hint}  (source: {row_path})")
                            break
                except Exception:
                    pass

            if symbol_map_lines:
                lines.append(
                    "\nREPO SYMBOL MAP — use these exact import paths for any new symbol references:\n"
                    + "\n".join(symbol_map_lines)
                )

        if not lines:
            return ""

        return (
            "\n"
            + "\n".join(lines)
            + "\n\nIMPORT RULES:\n"
            "  1. NEVER remove any existing import line shown above.\n"
            "  2. When you add a new symbol reference, add its import as a SEPARATE SEARCH/REPLACE\n"
            "     block that targets the import section (the first import line shown above).\n"
            "  3. Use the REPO SYMBOL MAP paths verbatim — do NOT invent package paths.\n"
        )

    # ── Constructor preservation guard (Fix 2 root cause) ──────────────────
    @staticmethod
    def _extract_constructor_params(content: str, ext: str) -> str:
        """Extract existing constructor parameter lines so LLM cannot drop them.

        Returns the full constructor block (signature + body) so the LLM sees
        the complete context and cannot regenerate the constructor from memory
        with missing injections.
        """
        if ext not in (".ts", ".tsx", ".java"):
            return ""
        # Try to extract the full constructor block including body
        m = re.search(
            r'(constructor\s*\([^)]*\)[^{]*\{)',
            content, re.DOTALL
        )
        if not m:
            return ""
        # Find the closing brace of the constructor
        start = m.start()
        depth = 0
        end = m.end()
        for i in range(m.end() - 1, len(content)):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        ctor_block = content[start:end].strip()
        if not ctor_block:
            return ""
        return ctor_block

    def _build_contract_context(
        self,
        task: "DevelopmentTask",
    ) -> str:
        """Build a prompt block from cross-file contracts.

        Priority:
        1. VERIFIED facts from RelationshipRegistry (actual code evidence)
        2. Planner's semantic contract (intent-level, may not match reality)

        VERIFIED facts override planner predictions. PLANNED relationships
        are clearly labeled as suggestions, never as facts.
        """
        # ── 1. Try RelationshipRegistry for verified facts ──
        registry_context = ""
        _impl_state = getattr(self, "_impl_state", None)
        if _impl_state and _impl_state.relationship_registry:
            try:
                registry = _impl_state.relationship_registry
                registry_context = registry.get_context_for_task(task)
            except Exception as _reg_exc:
                logger.debug(f"  Registry context failed (non-fatal): {_reg_exc}")

        # ── 2. Planner's semantic contract (existing logic, preserved) ──
        contract = getattr(task, "cross_file_contract", None)
        planner_context = ""
        if contract:
            blocks: list[str] = []

            # ── PRODUCES: what this task MUST create ──
            if contract.produces:
                produce_lines = []
                for bp in contract.produces:
                    line = f"    • {bp.capability}"
                    if bp.data_shape:
                        line += f"\n      data shape: {bp.data_shape}"
                    if bp.relationship_type and bp.relationship_type != "data":
                        line += f"  ({bp.relationship_type})"
                    produce_lines.append(line)
                blocks.append(
                    "  🔧 THIS TASK MUST PRODUCE (create these capabilities):\n"
                    + "\n".join(produce_lines)
                )

            # ── CONSUMES: what this task can USE from earlier tasks ──
            if contract.consumes:
                consume_lines = []
                for bp in contract.consumes:
                    from_tag = f"[from {bp.from_task}] " if bp.from_task else ""
                    line = f"    • {from_tag}{bp.capability}"
                    if bp.data_shape:
                        line += f"\n      data shape: {bp.data_shape}"
                    if bp.relationship_type and bp.relationship_type != "data":
                        line += f"  ({bp.relationship_type})"
                    consume_lines.append(line)
                blocks.append(
                    "  📥 THIS TASK CONSUMES (use these — already created by earlier tasks):\n"
                    + "\n".join(consume_lines)
                )

            if blocks:
                planner_context = (
                    "\n\n╔══════════════════════════════════════════════════════════════╗\n"
                    "║  CROSS-FILE CONTRACT (from planner — semantic obligations)   ║\n"
                    "╠══════════════════════════════════════════════════════════════╣\n"
                    + "\n".join(blocks)
                    + "\n╠══════════════════════════════════════════════════════════════╣\n"
                    "║  PRODUCES = you MUST create these capabilities                ║\n"
                    "║  CONSUMES = you MUST integrate with these, NOT reinvent them  ║\n"
                    "╚══════════════════════════════════════════════════════════════╝\n"
                )

        # ── 3. Combine: verified facts first, planner intent second ──
        if registry_context and planner_context:
            return registry_context + "\n" + planner_context
        elif registry_context:
            return registry_context
        elif planner_context:
            return planner_context
        return ""

    def _build_session_context(
        self,
        existing_content: str,
        task: "DevelopmentTask",
    ) -> str:
        """
        Cursor / Devin incremental technique: inject the FRESH content of files
        that were generated earlier in THIS run and are imported by the current
        file.  The stale SQLite/RAG index does not reflect interfaces that a
        prior task just changed (e.g. DisplayedMember.organization changed from
        string → object).  Showing the up-to-date shape prevents type mismatches
        (TS2322/TS2551) and dropped-export cascades (TS2305).
        """
        session_files: dict = getattr(self, "_session_files", None) or {}
        if not session_files:
            return ""

        import os as _os
        ext = Path(task.file_path).suffix.lower()
        src_dir = _os.path.dirname(task.file_path.replace("\\", "/"))
        blocks: list[str] = []
        seen: set[str] = set()

        # ── For HTML templates: inject sibling .ts and extract EXACT property list ──
        # This mirrors Cursor's LSP approach: give the HTML generator the exact declared
        # property names so it cannot invent names like `dmember.organizationName`.
        if ext in (".html", ".htm"):
            _stem = task.file_path.rsplit(".", 1)[0].replace("\\", "/").lower()
            for _ctrl_ext in (".ts", ".tsx"):
                _ctrl_key = _stem + _ctrl_ext
                if _ctrl_key in session_files:
                    seen.add(_ctrl_key)
                    _ctrl_content = session_files[_ctrl_key]

                    # Extract class-level property declarations (2-4 space indent, not inside methods)
                    _props: list[str] = []
                    for _pm in re.finditer(
                        r'^  (\w+)(?:\s*:\s*\S|\s*=\s*\S)',
                        _ctrl_content, re.MULTILINE
                    ):
                        _pname = _pm.group(1)
                        # Skip keywords and very short names
                        if _pname not in ('if', 'for', 'return', 'const', 'let', 'var', 'new') and len(_pname) > 1:
                            _props.append(_pname)
                    _props_unique = list(dict.fromkeys(_props))

                    _allowed_block = ""
                    if _props_unique:
                        _allowed_block = (
                            f"\n\n⚡ ALLOWED ANGULAR BINDINGS (from TypeScript LSP — use ONLY these names):\n"
                            f"  {', '.join(_props_unique[:40])}\n"
                            f"FORBIDDEN: any property name NOT in this list.\n"
                            f"FORBIDDEN: sub-object paths like `dmember.organizationName` when the property "
                            f"lives on the component class (e.g. `isExistingMemberInProject`).\n"
                        )

                    blocks.append(
                        f"SIBLING CONTROLLER — your template MUST use these EXACT property names:\n"
                        f"{_ctrl_key}\n{_ctrl_content}"
                        + _allowed_block
                    )
                    break

        # existing_content may be None for HTML tasks (template) — guard it
        if not existing_content:
            if blocks:
                return (
                    "\n\nRELATED FILES MODIFIED EARLIER IN THIS RUN:\n```\n"
                    + "\n\n".join(blocks[:4])
                    + "\n```\n"
                )
            return ""

        # Resolve each import in the current file; if it points to a
        # session-generated file, show that file's NEW content.
        if ext in (".ts", ".tsx", ".js", ".jsx"):
            _imp_re = re.compile(r"""from\s+['"](\.[^'"]+)['"]""")
            for m in _imp_re.finditer(existing_content):
                rel = m.group(1)
                for cand_ext in (".ts", ".tsx", "/index.ts", ".d.ts"):
                    cand = _os.path.normpath(_os.path.join(src_dir, rel + cand_ext)).replace("\\", "/").lower()
                    if cand in session_files and cand not in seen:
                        seen.add(cand)
                        blocks.append(f"FILE (just modified this run): {cand}\n{session_files[cand]}")
                        break
        elif ext == ".java":
            # Java: match imports whose class was regenerated this run
            for path_key, content in session_files.items():
                if not path_key.endswith(".java"):
                    continue
                cls = Path(path_key).stem
                if re.search(r'\b' + re.escape(cls) + r'\b', existing_content) and path_key not in seen:
                    seen.add(path_key)
                    blocks.append(f"FILE (just modified this run): {path_key}\n{content}")
        elif ext == ".py":
            _imp_re = re.compile(r"from\s+(\.[.\w]+)\s+import")
            for m in _imp_re.finditer(existing_content):
                mod = m.group(1).lstrip(".").replace(".", "/")
                cand = _os.path.normpath(_os.path.join(src_dir, mod + ".py")).replace("\\", "/").lower()
                if cand in session_files and cand not in seen:
                    seen.add(cand)
                    blocks.append(f"FILE (just modified this run): {cand}\n{session_files[cand]}")

        # ── Dependency-based resolution (not just existing imports) ────────
        # When this task needs to ADD a new import (e.g., component needs
        # isProjectMember() from ProjectsService), existing_content doesn't
        # have that import yet. So we also scan cross_file_contract.consumes
        # and task.dependencies to find relevant session files.
        _contract = getattr(task, "cross_file_contract", None)
        _dep_file_hints: set[str] = set()
        # From cross_file_contract.consumes: extract from_task references
        if _contract and getattr(_contract, "consumes", None):
            for _bp in _contract.consumes:
                _from = getattr(_bp, "from_task", "") or ""
                if _from:
                    _dep_file_hints.add(_from.replace("\\", "/").lower())
        # From task.dependencies: these are task IDs or file paths
        for _dep in (task.dependencies or []):
            _dep_file_hints.add(_dep.replace("\\", "/").lower())
        # Match dependency hints against session files
        for _hint in _dep_file_hints:
            for _skey in session_files:
                if _skey in seen:
                    continue
                # Match by file path (exact or basename match)
                _hint_basename = Path(_hint).name.lower() if "/" in _hint or "\\" in _hint else _hint
                _skey_basename = Path(_skey).name.lower()
                if _hint == _skey or _hint_basename == _skey_basename:
                    seen.add(_skey)
                    _scontent = session_files[_skey]
                    _import_line = ""
                    if ext in (".ts", ".tsx") and _skey.endswith((".ts", ".tsx")):
                        _import_fwd = _compute_import_path(task.file_path, _skey)
                        if _import_fwd:
                            _exports = re.findall(
                                r'export\s+(?:class|interface|enum|type|const|function)\s+(\w+)',
                                _scontent
                            )
                            _export_str = ", ".join(_exports[:5]) if _exports else "..."
                            _import_line = (
                                f"\n\U0001f4e6 IMPORT PATH: import {{ {_export_str} }} from '{_import_fwd}';\n"
                            )
                    blocks.append(
                        f"DEPENDENCY (from cross-file contract — generated this run):\n"
                        f"{_skey}\n{_import_line}{_scontent[:2000]}"
                    )
                    logger.info(
                        f"  [SessionContext] Injected dependency from contract: {_skey}"
                    )
                    break

        # Component 3a: Inject ALL session files in the same module directory.
        # Not just .service. files — also models, interfaces, and any file
        # generated this run that lives in the same Angular module / Java package.
        # This prevents calling methods on the wrong service or using stale shapes.

        # Fix 2: Helper to compute correct relative import paths
        def _compute_import_path(from_file: str, to_file: str) -> str:
            """Compute the TypeScript relative import path from `from_file` to `to_file`.
            E.g., from 'src/app/members/components/foo.component.ts'
                  to   'src/app/members/services/bar.service.ts'
                  → './services/bar.service' (no .ts extension)
            """
            from_dir = _os.path.dirname(from_file.replace("\\", "/"))
            to_path = to_file.replace("\\", "/")
            try:
                rel = _os.path.relpath(to_path, from_dir).replace("\\", "/")
            except ValueError:
                return ""
            # Remove .ts/.tsx extension for import path
            for _ext in (".ts", ".tsx"):
                if rel.endswith(_ext):
                    rel = rel[:-len(_ext)]
                    break
            # Ensure it starts with ./ or ../
            if not rel.startswith("."):
                rel = "./" + rel
            return rel

        if ext in (".ts", ".tsx") and task.task_type.value == "modify":
            _task_dir = _os.path.dirname(task.file_path.replace("\\", "/")).lower()
            # Find the module root: walk up to the first Angular module-like dir
            # (usually one level up from 'components/', 'services/', 'models/')
            _module_dir = _task_dir
            _last_seg = _task_dir.rsplit("/", 1)[-1] if "/" in _task_dir else _task_dir
            if _last_seg in ("components", "services", "models", "pipes", "directives", "guards"):
                _module_dir = _task_dir.rsplit("/", 1)[0]

            for _skey, _scontent in session_files.items():
                if _skey in seen:
                    continue
                if not _skey.endswith((".ts", ".tsx")):
                    continue
                _skey_dir = _os.path.dirname(_skey)
                # Same module directory or direct child
                if _skey_dir.startswith(_module_dir):
                    seen.add(_skey)
                    _label = "NEW SERVICE" if ".service." in _skey else "RELATED FILE"
                    # Fix 2: Compute both directions of import paths
                    _import_fwd = _compute_import_path(task.file_path, _skey)
                    _import_rev = _compute_import_path(_skey, task.file_path)
                    _import_hint = ""
                    if _import_fwd:
                        # Extract exported class/interface names from the session file
                        _exports = re.findall(
                            r'export\s+(?:class|interface|enum|type|const|function)\s+(\w+)',
                            _scontent
                        )
                        _export_str = ", ".join(_exports[:5]) if _exports else "..."
                        _import_hint += (
                            f"\n📦 IMPORT THIS FILE INTO YOUR CODE WITH:\n"
                            f"  import {{ {_export_str} }} from '{_import_fwd}';\n"
                        )
                    if _import_rev:
                        _import_hint += (
                            f"📦 THIS FILE IMPORTS FROM YOUR FILE WITH:\n"
                            f"  from '{_import_rev}'\n"
                        )
                    blocks.append(
                        f"{_label} (created/modified this run — use these exact types):\n"
                        f"{_skey}\n{_import_hint}{_scontent[:2000]}"
                    )

        # Component 3b: Inject files referenced in task description/dependencies
        # even if they're in a different module directory
        _desc_text = f"{task.description or ''} {task.title or ''}".lower()
        for _skey, _scontent in session_files.items():
            if _skey in seen:
                continue
            _stem = Path(_skey).stem.lower()
            # Check if the file stem appears in the task description
            if len(_stem) > 4 and _stem.replace("-", "").replace("_", "") in _desc_text.replace("-", "").replace("_", ""):
                seen.add(_skey)
                # Fix 2: Also compute import path for description-referenced files
                _import_fwd = ""
                if _skey.endswith((".ts", ".tsx")) and ext in (".ts", ".tsx"):
                    _import_fwd = _compute_import_path(task.file_path, _skey)
                _import_line = ""
                if _import_fwd:
                    _exports = re.findall(
                        r'export\s+(?:class|interface|enum|type|const|function)\s+(\w+)',
                        _scontent
                    )
                    _export_str = ", ".join(_exports[:5]) if _exports else "..."
                    _import_line = (
                        f"\n📦 IMPORT PATH: import {{ {_export_str} }} from '{_import_fwd}';\n"
                    )
                blocks.append(
                    f"DEPENDENCY (referenced in task description):\n"
                    f"{_skey}\n{_import_line}{_scontent[:2000]}"
                )

        if not blocks:
            return ""

        # Fix: Cap total session context to prevent context window overflow.
        # Each block is already capped at 2000 chars per file content, but with
        # 6 blocks × 2000 chars + import hints, the total can be ~15k chars.
        # Apply a hard budget: keep blocks under _MAX_SESSION_CHARS total.
        _MAX_SESSION_CHARS = 12_000  # ~3k tokens — safe alongside other prompt sections
        _selected_blocks: list = []
        _total_chars = 0
        for _blk in blocks[:6]:
            _blk_len = len(_blk)
            if _total_chars + _blk_len > _MAX_SESSION_CHARS and _selected_blocks:
                # Truncate this block to signatures/imports only
                _sig_lines = []
                for _line in _blk.split('\n'):
                    _stripped = _line.strip()
                    if any(kw in _stripped for kw in (
                        'export ', 'import ', 'class ', 'interface ', 'enum ',
                        'public ', 'private ', 'protected ', 'def ', '@',
                        '📦', 'IMPORT', 'SERVICE', 'RELATED', 'DEPENDENCY',
                    )):
                        _sig_lines.append(_line)
                _truncated = '\n'.join(_sig_lines[:30])
                if len(_truncated) > 200:
                    _selected_blocks.append(_truncated + "\n  [... truncated for context budget]")
                    _total_chars += len(_truncated) + 50
                # else skip entirely — too small to be useful
            else:
                _selected_blocks.append(_blk)
                _total_chars += _blk_len
            if _total_chars >= _MAX_SESSION_CHARS:
                break

        if not _selected_blocks:
            return ""

        return (
            "\n\nRELATED FILES MODIFIED EARLIER IN THIS RUN — these are the "
            "AUTHORITATIVE, CURRENT definitions. Your code MUST match these exact "
            "shapes (property names, types, method signatures). Do NOT use the older "
            "shapes from the RAG context if they conflict:\n```\n"
            + "\n\n".join(_selected_blocks)
            + "\n```\n"
        )

    def _format_list(self, items: List[str]) -> str:
        """Format list with bullets"""
        if not items:
            return "- None"
        return "\n".join(f"- {item}" for item in items)

    def _format_context(self, context: List[Any]) -> str:
        """Format code chunks for prompt"""
        if not context:
            return "No existing code context provided."
        
        formatted_chunks = []
        for i, chunk in enumerate(context[:3], 1):  # Limit to top 3 — minimal context principle
            if isinstance(chunk, dict):
                # Handle dictionary representation
                c_type = chunk.get("chunk_type", "unknown")
                if hasattr(c_type, "value"): c_type = c_type.value
                elif isinstance(c_type, str): pass
                elif c_type is not None: c_type = str(c_type)
                else: c_type = "unknown"
                
                c_name = chunk.get("name", "N/A")
                c_file = chunk.get("file_path", "N/A")
                c_ns = chunk.get("namespace", "N/A")
                c_content = chunk.get("content", "")
                
                formatted_chunks.append(f"""
--- Context {i}: {str(c_type).upper()} - {c_name} ---
File: {c_file}
Namespace: {c_ns or 'N/A'}

{c_content}
                """.strip())
            else:
                # Handle object representation
                try:
                    c_type = chunk.chunk_type.value if hasattr(chunk.chunk_type, "value") else str(chunk.chunk_type)
                except AttributeError:
                    c_type = getattr(chunk, "chunk_type", "unknown")
                
                formatted_chunks.append(f"""
--- Context {i}: {str(c_type).upper()} - {getattr(chunk, 'name', 'N/A')} ---
File: {getattr(chunk, 'file_path', 'N/A')}
Namespace: {getattr(chunk, 'namespace', 'N/A') or 'N/A'}

{getattr(chunk, 'content', '')}
                """.strip())
        
        return "\n\n".join(formatted_chunks)
    


    def _parse_response(
        self, 
        response_content: str, 
        task: DevelopmentTask
    ) -> GeneratedCode:
        """
        Parse LLM response into GeneratedCode.
        
        Primary: delimiter format (<<<AVIATOR_CODE_START>>> / <<<AVIATOR_CODE_END>>>)
        Fallback: JSON format for backward compatibility.
        """
        import re
        raw = response_content.strip()

        # ── PRIMARY: Delimiter-based format ──────────────────────────────────
        code_match = re.search(r'<<<\s*AVIATOR_CODE_START\s*>>>(.*?)(?:<<<\s*AVIATOR_CODE_END\s*>>>|<<<\s*AVIATOR_META_START\s*>>>)', raw, re.DOTALL)
        meta_match = re.search(r'<<<\s*AVIATOR_META_START\s*>>>(.*?)<<<\s*AVIATOR_META_END\s*>>>', raw, re.DOTALL)

        if code_match:
            code_content = code_match.group(1).strip()
            _existing = self._current_existing_content

            # ── str_replace EDIT dispatch for MODIFY tasks ─────────────────────
            if task.task_type.value == "modify" and _existing:
                # Parse SEARCH/REPLACE blocks from the LLM output
                # Primary: Aider-style git-merge-conflict delimiters (7 chars + keyword)
                #   <<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE
                _edit_pattern = re.compile(
                    r'<{7} SEARCH\n(.*?)\n={7}\n(.*?)\n>{7} REPLACE',
                    re.DOTALL
                )
                _edit_matches = list(_edit_pattern.finditer(code_content))

                # Fallback: legacy <<</ >>> format for backward compatibility
                if not _edit_matches:
                    _legacy_pattern = re.compile(
                        r'EDIT:\s*\n'
                        r'old_str:\s*\n<<<\n(.*?)\n>>>\s*\n'
                        r'new_str:\s*\n<<<\n(.*?)\n>>>',
                        re.DOTALL
                    )
                    _edit_matches = list(_legacy_pattern.finditer(code_content))
                    if _edit_matches:
                        logger.info(
                            f"  Parser: LLM used legacy <<</>>> format for {task.file_path} "
                            f"— parsed {len(_edit_matches)} edit(s) via fallback"
                        )

                if _edit_matches:
                    # Build edit list for atomic application
                    # Wire scope_method from edit_anchors for ambiguity resolution
                    _anchor_methods = [
                        a.get("anchor_method", "")
                        for a in getattr(task, "edit_anchors", []) or []
                        if a.get("anchor_method")
                    ]
                    _edits = []
                    for _idx, _em in enumerate(_edit_matches):
                        _old_s = _em.group(1)
                        _new_s = _em.group(2)

                        # Guard: if new_str contains another SEARCH/REPLACE block,
                        # the regex captured across block boundaries due to
                        # malformed LLM output.  Reject immediately so retry fires.
                        if re.search(r'<{7} SEARCH|>{7} REPLACE', _new_s):
                            raise ValueError(
                                f"Edit {_idx+1}: REPLACE block contains nested SEARCH/REPLACE markers. "
                                f"The LLM produced malformed output — retrying."
                            )
                        # Same guard for legacy format
                        if re.search(r'EDIT:\s*\nold_str:', _new_s):
                            raise ValueError(
                                f"Edit {_idx+1}: new_str contains another EDIT block. "
                                f"The LLM produced malformed output — retrying."
                            )

                        # Try to match this edit to an anchor by index
                        _scope = ""
                        if _idx < len(_anchor_methods):
                            _scope = _anchor_methods[_idx]
                        elif len(_anchor_methods) == 1:
                            # All edits scoped to the single anchor
                            _scope = _anchor_methods[0]
                        _edits.append({
                            "old_str": _old_s,
                            "new_str": _new_s,
                            "scope_method": _scope,
                        })

                    try:
                        code_content = _apply_str_replace_edits(
                            _edits, _existing, task.file_path
                        )
                        logger.info(
                            f"  str_replace: {len(_edits)} edit(s) applied "
                            f"successfully to {task.file_path}"
                        )

                        # ── Post-Generation Patch Gate Validation ─────────────
                        try:
                            from ticket_to_code.agents.patch_gate import PatchGate
                            _auth_files = getattr(self, "_current_allowed_files", None) or set()
                            
                            # Gather sibling controller content for cross-artifact validation if HTML
                            _sibling_c = None
                            _sibling_p = None
                            if Path(task.file_path).suffix.lower() in (".html", ".htm"):
                                _session = getattr(self, "_session_files", None) or {}
                                _stem = task.file_path.rsplit(".", 1)[0]
                                for _ctrl_ext in (".ts", ".tsx"):
                                    _cand_p = _stem + _ctrl_ext
                                    _ck = _cand_p.replace("\\", "/").lower()
                                    if _ck in _session:
                                        _sibling_c = _session[_ck]
                                        _sibling_p = _cand_p
                                        break
                                if not _sibling_c and getattr(self, "_workspace_path", None):
                                    _f_abs = Path(self._workspace_path) / (_stem + ".ts")
                                    if _f_abs.exists():
                                        _sibling_c = _f_abs.read_text(encoding="utf-8", errors="ignore")
                                        _sibling_p = str(_f_abs)

                            _gate_ok, _gate_reason = PatchGate.validate_patch(
                                file_path=task.file_path,
                                patch_content=code_match.group(1).strip() if code_match else "",
                                authorized_writable_files=_auth_files if _auth_files else {task.file_path},
                                change_targets=getattr(task, "change_targets", []),
                                has_migration_evidence=False,
                                sibling_content=_sibling_c,
                                sibling_path=_sibling_p,
                            )
                            if not _gate_ok:
                                logger.warning(f"  [PatchGate] Validation failed for {task.file_path}: {_gate_reason}")
                                raise ValueError(f"[PatchGate] {_gate_reason}")
                        except ImportError:
                            pass
                    except ValueError as edit_err:
                        logger.warning(
                            f"str_replace edit failed for {task.file_path}: "
                            f"{edit_err} — preserving original file"
                        )
                        code_content = _existing
                        documentation = f"FAILED: {edit_err}"

                else:
                    # No EDIT blocks found — check for legacy format or full-file output
                    # Legacy: REPLACE_LINES, INSERT_*, SEARCH/REPLACE
                    _legacy_markers = (
                        "REPLACE_LINES:", "INSERT_BEFORE_CLASS_END:",
                        "INSERT_AT_FILE_END:", "INSERT_AFTER_IMPORTS:",
                        "INSERT_BEFORE_HTML_END:", "INSERT_AFTER_LINE:",
                        "SEARCH:", "REPLACE:",
                    )
                    _has_legacy = any(m in code_content for m in _legacy_markers)

                    if _has_legacy:
                        logger.warning(
                            f"MODIFY task {task.file_path}: LLM used LEGACY format "
                            f"(REPLACE_LINES/INSERT/SEARCH). These are deprecated. "
                            f"Preserving original — retry will re-prompt for EDIT format."
                        )
                        code_content = _existing
                        documentation = (
                            "FAILED: LLM used deprecated REPLACE_LINES/INSERT/SEARCH "
                            "format instead of str_replace EDIT blocks"
                        )
                    else:
                        # No patch directives at all — detect full-file rewrite
                        _ext = (task.file_path or "").rsplit(".", 1)[-1].lower()
                        _first = code_content.lstrip()[:120]
                        _is_full_file = False
                        if _ext in ("java", "kt"):
                            _is_full_file = _first.startswith("package ")
                        elif _ext in ("ts", "tsx", "js", "jsx"):
                            _import_count = sum(
                                1 for ln in code_content.split("\n")[:15]
                                if ln.startswith("import ")
                            )
                            _is_full_file = _import_count >= 3
                        elif _ext == "py":
                            _is_full_file = len(code_content.splitlines()) > 30
                        else:
                            _is_full_file = len(code_content.splitlines()) > 40

                        if _is_full_file:
                            logger.warning(
                                f"MODIFY task {task.file_path}: LLM output detected as "
                                f"full-file rewrite (ext={_ext}, no EDIT blocks). "
                                f"Raising error to trigger retry with SEARCH/REPLACE instruction."
                            )
                            raise ValueError(
                                f"SEARCH block not found: LLM output a full-file rewrite "
                                f"instead of SEARCH/REPLACE edit blocks for MODIFY task "
                                f"on {task.file_path}. The LLM must use "
                                f"<<<<<<< SEARCH / ======= / >>>>>>> REPLACE format. "
                                f"Do NOT output the entire file."
                            )
                        # else: small snippet (property, annotation) — let it through

            documentation = "Generated code (delimiter format)"
            imports: list = []
            if meta_match:
                meta_raw = meta_match.group(1).strip()
                try:
                    meta = json.loads(meta_raw)
                    documentation = meta.get("documentation", documentation)
                    imports = meta.get("imports", [])
                except Exception:
                    pass  # meta is optional

            # ── Fix 4: Post-patch verification ───────────────────────────────
            # Verify that expected new method names appear in the result. If an
            # additive task was supposed to add a method but it's absent, the
            # INSERT strategy silently failed — escalate to let retry logic act.
            if (task.task_type.value == "modify"
                    and task.allowed_methods
                    and code_content != (_existing or "")
                    and not documentation.startswith("FAILED")):
                _absent = [m for m in task.allowed_methods
                           if not re.search(r'\b' + re.escape(m) + r'\b', code_content)]
                if _absent and len(_absent) == len(task.allowed_methods):
                    # ALL expected symbols are absent — patch almost certainly wrong
                    logger.warning(
                        f"Post-patch: ALL allowed_methods {_absent} absent from {task.file_path} "
                        f"after patch — marking as failed so fix_build can retry."
                    )
                    documentation = f"FAILED: expected methods {_absent} not found after patch"

            # ── Component 2b: Post-patch constructor verification ─────────────
            # If the original had a constructor, verify NO params were dropped.
            _ext_ctor_check = Path(task.file_path).suffix.lower()
            if (_ext_ctor_check in (".ts", ".tsx", ".java")
                    and _existing and code_content != _existing
                    and not documentation.startswith("FAILED")):
                _orig_ctor_m = re.search(
                    r'constructor\s*\(([^)]+)\)', _existing, re.DOTALL
                )
                _new_ctor_m = re.search(
                    r'constructor\s*\(([^)]+)\)', code_content, re.DOTALL
                )
                if _orig_ctor_m and _new_ctor_m:
                    # Extract param names from original and new
                    def _param_names(params_str: str) -> set:
                        # Match 'private foo: Bar' or 'foo: Bar' patterns
                        return set(re.findall(
                            r'(?:private|public|protected|readonly)?\s*(\w+)\s*[:\,)]',
                            params_str
                        ))
                    _orig_params = _param_names(_orig_ctor_m.group(1))
                    _new_params  = _param_names(_new_ctor_m.group(1))
                    _dropped = _orig_params - _new_params
                    if _dropped:
                        logger.warning(
                            f"Constructor verification FAILED for {task.file_path}: "
                            f"dropped params {_dropped} — preserving original"
                        )
                        code_content = _existing
                        documentation = f"FAILED: constructor dropped params {_dropped}"

            # ── Fix 6: Quick syntax check (brace balance) ────────────────────
            # Catch the most common corruption (unbalanced braces from bad INSERT)
            # before writing to disk.  Ignores strings/comments — intentionally
            # simple, catches gross errors not subtle ones.
            _ext_syn = (task.file_path or "").rsplit(".", 1)[-1].lower()
            if _ext_syn in ("java", "kt", "ts", "tsx", "js", "jsx", "cs") and code_content:
                _depth = 0
                for _ch in code_content:
                    if _ch == "{":
                        _depth += 1
                    elif _ch == "}":
                        _depth -= 1
                if _depth != 0:
                    logger.warning(
                        f"Syntax check: unbalanced braces in {task.file_path} "
                        f"(depth={_depth} after patch) — preserving original."
                    )
                    code_content = _existing or code_content
                    documentation = f"FAILED: unbalanced braces (depth={_depth}) in generated patch"

            logger.info(f"Parsed response via regex delimiter format: {len(code_content)} chars")
            return GeneratedCode(
                file_path=task.file_path,
                content=code_content,
                language=task.language,
                change_type=task.task_type,
                documentation=documentation,
                imports=imports,
            )
        elif re.search(r'<<<\s*AVIATOR_CODE_START\s*>>>', raw) and not re.search(r'<<<\s*AVIATOR_CODE_END\s*>>>|<<<\s*AVIATOR_META_START\s*>>>', raw):
            raise ValueError("LLM output truncated: AVIATOR_CODE_START found but AVIATOR_CODE_END missing.")

        # ── FALLBACK: JSON format ─────────────────────────────────────────────
        cleaned = raw
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        _json_err: str = ""
        try:
            data = json.loads(cleaned)
            logger.info("Parsed response via JSON format")
            return GeneratedCode(
                file_path=task.file_path,
                content=data.get("code", ""),
                language=task.language,
                change_type=task.task_type,
                documentation=data.get("documentation"),
                imports=data.get("imports", []),
            )
        except json.JSONDecodeError as e:
            _json_err = str(e)
            logger.warning(f"JSON parsing failed, attempting regex fallback: {e}")

        # ── REGEX FALLBACKS ───────────────────────────────────────────────────

        # Fallback 1: greedy extraction — everything after "code": " up to the
        # last plausible end marker (handles truncated/unescaped responses)
        code_key_pos = cleaned.find('"code"')
        if code_key_pos >= 0:
            colon_pos = cleaned.find(':', code_key_pos)
            open_quote = cleaned.find('"', colon_pos + 1)
            if open_quote >= 0:
                raw_value = cleaned[open_quote + 1:]
                # Strip known trailing meta markers
                for marker in ['", "imports"', '",\n  "imports"', '", "documentation"', '"\n}']:
                    idx = raw_value.rfind(marker)
                    if idx > 0:
                        raw_value = raw_value[:idx]
                        break
                code_content = raw_value.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                logger.info("Extracted code via greedy quote fallback")
                return GeneratedCode(
                    file_path=task.file_path,
                    content=code_content,
                    language=task.language,
                    change_type=task.task_type,
                    documentation="Generated code (greedy fallback)",
                    imports=[],
                )

        # Fallback 2: code block in raw response
        code_match = re.search(r'```(?:\w+)?\s*(.*?)\s*```', raw, re.DOTALL)
        if code_match:
            logger.info("Extracted code via backtick fallback")
            return GeneratedCode(
                file_path=task.file_path,
                content=code_match.group(1),
                language=task.language,
                change_type=task.task_type,
                documentation="Generated code (code-block fallback)",
                imports=[],
            )

        # Fallback 3: For MODIFY tasks, return original content unchanged.
        # For CREATE tasks, use the raw string.
        if task.task_type.value == "modify":
            logger.error("All parsing methods failed for MODIFY task — preserving original file")
            return GeneratedCode(
                file_path=task.file_path,
                content=self._current_existing_content or "",
                language=task.language,
                change_type=task.task_type,
                documentation="FAILED: No parseable code generated, original preserved",
                imports=[],
            )
        
        logger.info("Extracted code via raw string fallback (CREATE task)")
        return GeneratedCode(
            file_path=task.file_path,
            content=raw,
            language=task.language,
            change_type=task.task_type,
            documentation="Generated code (raw fallback)",
            imports=[],
        )


# ============================================================================
# PATCH VALIDATOR
# ============================================================================

class PatchValidationResult:
    """Result of patch scope validation"""
    def __init__(self, passed: bool, violations: list[str], metrics: dict):
        self.passed = passed
        self.violations = violations
        self.metrics = metrics  # files_modified, lines_changed, etc.

    def __repr__(self):
        return f"PatchValidation(passed={self.passed}, violations={self.violations}, metrics={self.metrics})"


class PatchValidator:
    """
    Validates that generated code stays within the frozen modification scope.

    Checks:
      CHECK 1 — File scope: generated file_path matches the allowed task file
      CHECK 2 — No new files invented beyond what planner authorised (task_type=create)
      CHECK 3 — Change budget: lines changed not disproportionately large
      CHECK 4 — No obvious cross-file contamination in the code body
    """

    # Maximum ratio of changed lines vs original for a "modify" task before warning
    MAX_CHANGE_RATIO = 0.8   # if >80% of original lines changed → suspicious full-rewrite

    def validate(
        self,
        generated: "GeneratedCode",
        task: "DevelopmentTask",
        existing_content: str = None
    ) -> PatchValidationResult:
        violations = []
        metrics = {}

        # ── CHECK 1: File path must match the frozen allowed file ────────────
        normalized_gen  = generated.file_path.replace("\\", "/").lower()
        normalized_task = task.file_path.replace("\\", "/").lower()
        if normalized_gen != normalized_task:
            violations.append(
                f"FILE SCOPE VIOLATION: generator output targets '{generated.file_path}' "
                f"but allowed file is '{task.file_path}'"
            )
        metrics["file_match"] = (normalized_gen == normalized_task)

        # ── CHECK 2: Modify task must not balloon into a full creative rewrite ─
        if existing_content and task.task_type.value == "modify":
            original_lines = existing_content.splitlines()
            new_lines      = generated.content.splitlines()
            metrics["original_line_count"] = len(original_lines)
            metrics["new_line_count"]      = len(new_lines)

            # Compute rough change ratio using difflib
            import difflib
            matcher   = difflib.SequenceMatcher(None, original_lines, new_lines)
            same_ratio = matcher.ratio()          # 1.0 = identical, 0.0 = totally different
            change_ratio = 1.0 - same_ratio
            metrics["change_ratio"] = round(change_ratio, 3)

            if change_ratio > self.MAX_CHANGE_RATIO:
                violations.append(
                    f"CHANGE BUDGET EXCEEDED: {change_ratio:.0%} of file changed "
                    f"(limit {self.MAX_CHANGE_RATIO:.0%}). Generator likely rewrote the whole file."
                )

            # Count actual changed lines
            changed_lines = sum(
                1 for tag, _, _, _, _ in matcher.get_opcodes() if tag != "equal"
            )
            metrics["changed_blocks"] = changed_lines

        # ── CHECK 3: Detect keyword evidence of unauthorized new file creation ─
        content_lower = generated.content.lower()
        suspicious_phrases = [
            "// new file", "// create this file", "// file:",
        ]
        for phrase in suspicious_phrases:
            if phrase in content_lower and task.task_type.value == "modify":
                violations.append(
                    f"SUSPICIOUS PATTERN: generated content contains '{phrase}' in a MODIFY task"
                )

        # ── CHECK 4: Method-level scope enforcement ────────────────────────
        # Only enforce when the localization agent populated allowed_methods AND
        # we have both original and new content to compare.
        if existing_content and task.allowed_methods and task.task_type.value == "modify":
            import re
            method_sig_pattern = re.compile(
                r"(?:(?:public|private|protected|static|async|override|abstract)\s+)*"
                r"(?:[\w<>\[\]]+\s+)?"
                r"(\w+)\s*\([^)]*\)\s*(?::\s*[\w<>\[\]|]+)?\s*(?:\{|=>)",
                re.MULTILINE
            )
            python_def = re.compile(r"^\s*def\s+(\w+)\s*\(", re.MULTILINE)
            _KEYWORDS = {"if", "for", "while", "switch", "catch", "try", "else", "return",
                         "new", "class", "interface", "constructor", "function"}
            # RxJS/Observable call-site names that appear as method-like patterns
            # but are calls on streams, not new class method declarations.
            _RXJS_CALLSITES = {
                "subscribe", "pipe", "map", "tap", "filter", "switchMap", "mergeMap",
                "concatMap", "exhaustMap", "catchError", "finalize", "take", "takeUntil",
                "debounceTime", "distinctUntilChanged", "share", "shareReplay",
                "of", "from", "forkJoin", "combineLatest", "zip", "throwError",
            }

            def _extract_methods(src: str) -> set:
                names = set()
                for m in method_sig_pattern.finditer(src):
                    n = m.group(1)
                    if n not in _KEYWORDS and n not in _RXJS_CALLSITES:
                        names.add(n)
                for m in python_def.finditer(src):
                    names.add(m.group(1))
                return names

            original_methods = _extract_methods(existing_content)
            new_methods      = _extract_methods(generated.content)

            # Methods that appear in NEW but NOT in ORIGINAL — truly new additions
            truly_new = new_methods - original_methods
            if truly_new and not any(m in task.allowed_methods for m in truly_new):
                # Allow new methods whose names appear in the task description or ticket
                # title — this covers feature tasks where the LLM correctly invents a
                # new helper method (e.g. checkProjectMembership) not pre-listed by planner.
                _task_text = " ".join([
                    (task.description or ""),
                    (task.title or ""),
                    " ".join(task.allowed_methods or []),
                ]).lower()
                _allowed_new = {
                    m for m in truly_new
                    if any(word in _task_text for word in [
                        m.lower()[:6],           # first 6 chars of method name
                        "membership", "organization", "project", "contract",
                        "existing", "check", "get", "fetch", "load", "is", "has",
                    ])
                }
                _blocked_new = truly_new - _allowed_new
                if _blocked_new:
                    violations.append(
                        f"METHOD SCOPE VIOLATION: generator added new method(s) "
                        f"{_blocked_new} which are not in allowed_methods {task.allowed_methods}"
                    )
                elif _allowed_new:
                    import logging as _log
                    _log.getLogger(__name__).info(
                        f"  ℹ️ Allowing new method(s) {_allowed_new} — semantically "
                        f"related to task description (feature task)"
                    )
            metrics["new_methods_added"] = list(truly_new)

            # Methods that exist in both but were MODIFIED (changed lines around them)
            # We detect this by checking if lines near those methods changed
            # This is a lightweight check — full AST diff is P2
            metrics["allowed_methods"] = task.allowed_methods

            # ── CHECK 4b: Method deletion detection ───────────────────────
            # The mirror of CHECK 4: detect methods present in original that
            # are ABSENT from generated output — direct evidence of deletion.
            deleted = original_methods - new_methods
            if deleted:
                violations.append(
                    f"METHOD DELETION DETECTED: {sorted(deleted)} exist in "
                    f"the original but are missing from generated output — "
                    f"all existing methods must be preserved verbatim"
                )
            metrics["deleted_methods"] = sorted(deleted)

        # ── CHECK 4c: Class field/property drop detection (TS/JS) ─────────────
        # Catches the "full-file amnesia" bug: a class member declared in the
        # original and still referenced via `this.X` in the rewrite, but whose
        # declaration was silently dropped — the root cause of TS2339 cascades.
        _lang = getattr(getattr(task, "language", None), "value", str(getattr(task, "language", ""))).lower()
        if (
            existing_content
            and task.task_type.value == "modify"
            and _lang in ("typescript", "javascript")
        ):
            import re as _re_fields

            def _declared_members(src: str) -> set:
                members: set = set()
                # Class field / method declarations at class-body indentation.
                for m in _re_fields.finditer(
                    r"^[ \t]+(?:(?:public|private|protected|readonly|static|abstract|async|override|get|set)\s+|@[\w.]+\([^)]*\)\s*)*"
                    r"([A-Za-z_$][\w$]*)\s*[:=(<]",
                    src, _re_fields.MULTILINE,
                ):
                    members.add(m.group(1))
                # Constructor parameter properties: constructor(private foo: Bar)
                for m in _re_fields.finditer(
                    r"(?:private|public|protected|readonly)\s+([A-Za-z_$][\w$]*)\s*:",
                    src,
                ):
                    members.add(m.group(1))
                return members

            _RESERVED_MEMBERS = {
                "constructor", "if", "for", "while", "switch", "return", "get", "set",
                "public", "private", "protected", "static", "readonly", "async",
            }
            declared_old = _declared_members(existing_content) - _RESERVED_MEMBERS
            declared_new = _declared_members(generated.content) - _RESERVED_MEMBERS
            referenced_new = set(_re_fields.findall(r"this\.([A-Za-z_$][\w$]*)", generated.content))

            dropped_used = sorted(
                x for x in referenced_new
                if x in declared_old and x not in declared_new
            )
            if dropped_used:
                # Attempt deterministic auto-repair before failing: restore declaration from original class
                repaired_content = generated.content
                repaired_declarations = []
                for dropped_var in dropped_used:
                    decl_m = _re_fields.search(
                        rf"^[ \t]*(?:(?:public|private|protected|readonly|static)\s+)*{re.escape(dropped_var)}\b[^;\n]*;",
                        existing_content,
                        _re_fields.MULTILINE
                    )
                    if decl_m:
                        repaired_declarations.append(decl_m.group(0).strip())
                    else:
                        repaired_declarations.append(f"{dropped_var}: any;")

                class_decl_m = _re_fields.search(r"class\s+\w+[^{]*\{", repaired_content)
                if class_decl_m and repaired_declarations:
                    insert_pos = class_decl_m.end()
                    insertion = "\n  " + "\n  ".join(repaired_declarations) + "\n"
                    repaired_content = repaired_content[:insert_pos] + insertion + repaired_content[insert_pos:]
                    generated.content = repaired_content
                    logger.info(
                        f"  🩹 Auto-repaired dropped members {dropped_used} in {getattr(task, 'file_path', 'TS file')}"
                    )
                else:
                    violations.append(
                        f"MEMBER DROPPED BUT STILL USED: {dropped_used} were declared in the "
                        f"original class and are still referenced via 'this.', but their "
                        f"declaration is missing from the generated output — re-add them."
                    )
            metrics["dropped_members_used"] = dropped_used

        # ── CHECK 5: Ownership-aware change budget ────────────────────────
        # Phase 2 ownership type governs how aggressively the generator may
        # change a file.  READ_ONLY files must never be written; companion
        # files (DISPLAY_OWNER / SUPPORTING) have a tighter change budget.
        ownership_type = getattr(task, "ownership_type", None)
        if ownership_type == "READ_ONLY" and existing_content:
            violations.append(
                f"OWNERSHIP VIOLATION: '{task.file_path}' is classified "
                f"READ_ONLY — no modifications are permitted"
            )
        elif ownership_type in ("SUPPORTING", "DISPLAY_OWNER") and existing_content:
            _MAX_COMPANION_RATIO = 0.40   # tighter than PRIMARY (0.80)
            cr = metrics.get("change_ratio", 0.0)
            if cr > _MAX_COMPANION_RATIO:
                violations.append(
                    f"COMPANION CHANGE BUDGET EXCEEDED: {cr:.0%} of "
                    f"{ownership_type} file changed "
                    f"(limit {_MAX_COMPANION_RATIO:.0%} for non-primary files)"
                )
        metrics["ownership_type"] = ownership_type

        passed = len(violations) == 0

        if violations:
            logger.warning(f"⚠️  PATCH VALIDATION FAILED for {task.file_path}:")
            for v in violations:
                logger.warning(f"    • {v}")
        else:
            logger.info(f"✅ Patch validation passed for {task.file_path} | metrics={metrics}")

        return PatchValidationResult(passed=passed, violations=violations, metrics=metrics)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_code(
    task: DevelopmentTask,
    requirements: StructuredRequirements,
    context: List[CodeChunk]
) -> GeneratedCode:
    """
    Convenience function to generate code.
    
    Args:
        task: Development task
        requirements: Structured requirements
        context: Code context
        
    Returns:
        Generated code
    """
    agent = CodeGeneratorAgent()
    return agent.generate_code(task, requirements, context)
