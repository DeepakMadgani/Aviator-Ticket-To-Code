import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from ticket_to_code.llm_utils import llm_invoke

logger = logging.getLogger(__name__)

class ErrorResolutionAgent:
    """
    An agentic loop that resolves build/compiler errors.
    Instead of hardcoded regexes, it gives the LLM tools to investigate and fix errors dynamically.
    """

    def __init__(self, llm, workspace_path: Path, sqlite_store=None):
        self.llm = llm
        self.workspace_path = workspace_path
        self.sqlite_store = sqlite_store
        
        # Keep track of which files we've modified during this loop
        self.modified_files: dict[str, str] = {}
        # Structured error-to-method map for patch target validation
        # Maps normalized file path → list of (error_line, method_name, method_start, method_end)
        self._error_method_map: dict[str, list[tuple[int, str, int, int]]] = {}

        # ── v2 Context (set by fix_build_errors_node before calling resolve_errors) ──
        # Pre-edit file snapshots (from state["original_file_contents"])
        self.pre_edit_snapshots: dict[str, str] = {}
        # Set of file paths (lowercase, forward-slashed) that our workflow modified
        self.our_modified_files: set[str] = set()
        # Original ticket description so the agent understands intent
        self.ticket_description: str = ""
        # Root directory for running tsc/compiler (e.g. the Angular project root)
        self.compile_root: Optional[Path] = None

    def resolve_errors(self, build_errors: list[str], max_iter: int = 0) -> dict[str, str]:
        """
        Takes raw compiler errors, extracts file contexts, and loops with the LLM until FINISHED.
        Returns a dict mapping relative file paths to their new fixed content.

        max_iter=0 means auto-scale based on error count.
        """
        # ── Dynamic iteration count ───────────────────────────────────────────
        if max_iter <= 0:
            max_iter = min(max(10, len(build_errors) * 2), 25)

        logger.info("\n" + "=" * 70)
        logger.info(" ERROR RESOLUTION AGENT v2: Compile-in-the-loop fix")
        logger.info(f"   Errors Count: {len(build_errors)}")
        logger.info(f"   Max Iterations: {max_iter}")
        logger.info(f"   Pre-edit snapshots: {len(self.pre_edit_snapshots)} file(s)")
        logger.info(f"   Our modified files: {len(self.our_modified_files)} file(s)")
        logger.info(f"   Compile root: {self.compile_root}")
        logger.info("=" * 70)

        error_text = "\n".join(build_errors)

        # 1. Preload Error Files (Extract paths from the compiler output)
        preloaded_files = self._preload_error_files(error_text)
        
        # 2. Build the initial prompt
        system_prompt = self._build_system_prompt()
        
        context_str = "FILES MENTIONED IN ERRORS:\n"
        for path, content in preloaded_files.items():
            context_str += f"\n--- {path} ---\n```\n{content}\n```\n"

        if not preloaded_files:
            context_str += "(No files could be automatically extracted from the error messages. You must use SEARCH_WORKSPACE to find them.)\n"

        # 2b. Map error line numbers to containing methods
        error_method_map = self._map_errors_to_methods(error_text, preloaded_files)

        # 2c. Build context about which files we modified and the ticket intent
        context_meta = ""
        if self.our_modified_files:
            context_meta += "\nFILES MODIFIED BY OUR WORKFLOW (we changed these):\n"
            for fp in sorted(self.our_modified_files):
                context_meta += f"  - {fp}\n"
        if self.ticket_description:
            context_meta += f"\nORIGINAL TICKET INTENT:\n{self.ticket_description[:1000]}\n"
        if self.pre_edit_snapshots:
            context_meta += f"\nPRE-EDIT SNAPSHOTS AVAILABLE ({len(self.pre_edit_snapshots)} files) — use READ_ORIGINAL to see what a file looked like before our edits.\n"

        user_content = f"COMPILER ERRORS:\n{error_text}\n\n{error_method_map}{context_meta}\n{context_str}\nWhat action do you want to take? Output ONLY JSON."
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content)
        ]

        # 3. Enter Tool-Calling Loop
        for attempt in range(max_iter):
            logger.info(f"\n--- Error Resolution Iteration {attempt + 1}/{max_iter} ---")
            
            try:
                response = llm_invoke(self.llm, messages)
                response_text = response.content if hasattr(response, 'content') else str(response)
            except Exception as e:
                logger.error(f"LLM invocation failed: {e}")
                break

            logger.info(f"LLM Response:\n{response_text[:500]}...")
            
            # Append LLM's response to history
            messages.append(AIMessage(content=response_text))

            # Parse JSON action blocks
            actions = self._parse_json_actions(response_text)
            if not actions:
                logger.warning("No JSON actions found in response. Prompting LLM to use proper format.")
                messages.append(HumanMessage(content="I did not find a valid JSON action block. Please output your command in a JSON block exactly as specified in the instructions."))
                continue

            tool_responses = []
            finished = False

            for action in actions:
                cmd = action.get("action")
                args = action.get("args", {})
                
                if cmd == "FINISHED":
                    logger.info("Agent declared FINISHED.")
                    finished = True
                    break
                elif cmd == "SEARCH_WORKSPACE":
                    res = self._tool_search_workspace(args.get("query", ""))
                    tool_responses.append(f"Result for SEARCH_WORKSPACE('{args.get('query')}'):\n{res}")
                elif cmd == "READ_FILE":
                    res = self._tool_read_file(args.get("file_path", ""))
                    tool_responses.append(f"Result for READ_FILE('{args.get('file_path')}'):\n{res}")
                elif cmd == "READ_ORIGINAL":
                    res = self._tool_read_original(args.get("file_path", ""))
                    tool_responses.append(f"Result for READ_ORIGINAL('{args.get('file_path')}'):\n{res}")
                elif cmd == "CREATE_FILE":
                    res = self._tool_edit_file(args.get("file_path", ""), args.get("content", ""))
                    tool_responses.append(f"Result for CREATE_FILE('{args.get('file_path')}'):\n{res}")
                elif cmd == "REPLACE_CONTENT":
                    file_path = args.get("file_path", "")
                    target_content = args.get("target_content", "")
                    replacement_content = args.get("replacement_content", "")
                    
                    # Validate patch targets the correct method
                    validation = self._validate_patch_target(file_path, target_content)
                    if validation:  # validation is non-empty string = rejection
                        logger.warning(f"  ⛔ Patch target validation FAILED: {validation[:200]}")
                        tool_responses.append(validation)
                    else:
                        res = self._tool_replace_content(file_path, target_content, replacement_content)
                        tool_responses.append(f"Result for REPLACE_CONTENT('{file_path}'):\n{res}")
                elif cmd == "COMPILE":
                    res = self._tool_compile()
                    tool_responses.append(f"Result for COMPILE:\n{res}")
                else:
                    tool_responses.append(f"Error: Unknown action '{cmd}'. Allowed actions: SEARCH_WORKSPACE, READ_FILE, READ_ORIGINAL, REPLACE_CONTENT, CREATE_FILE, COMPILE, FINISHED.")

            if finished:
                break
                
            if tool_responses:
                feedback = "\n\n".join(tool_responses) + "\n\nWhat action do you want to take next? Output ONLY JSON."
                messages.append(HumanMessage(content=feedback))

        logger.info(f"Agent finished. Total files modified: {len(self.modified_files)}")
        return self.modified_files

    def _build_system_prompt(self) -> str:
        return """You are an expert autonomous software engineer resolving compiler/build errors.
You will be provided with the compiler output and the contents of the files mentioned in the errors.

Your job is to investigate the errors and fix them by editing or creating files.
You MUST write correct, production-quality code — not quick hacks.
If a module or class is missing (e.g. "Cannot find module 'X'"), you MUST search the workspace to find where it is located to fix the import path, or CREATE the file if it truly does not exist.

You interact with the workspace by outputting JSON blocks.
You can use the following tools:

1. SEARCH_WORKSPACE
Search for a class, interface, or file name.
```json
{
  "action": "SEARCH_WORKSPACE",
  "args": {
    "query": "MemberService"
  }
}
```

2. READ_FILE
Read the CURRENT contents of a file (including any modifications made during this session).
```json
{
  "action": "READ_FILE",
  "args": {
    "file_path": "path/to/file.ts"
  }
}
```

3. READ_ORIGINAL
Read the ORIGINAL contents of a file BEFORE our workflow modified it. Use this to understand what changed and decide whether our edits were correct.
```json
{
  "action": "READ_ORIGINAL",
  "args": {
    "file_path": "path/to/file.ts"
  }
}
```

4. REPLACE_CONTENT
Replace a specific block of code in an existing file.
```json
{
  "action": "REPLACE_CONTENT",
  "args": {
    "file_path": "path/to/file.ts",
    "target_content": "    private translateService: TranslateService,",
    "replacement_content": "    private translateService: TranslateService,\n    private newService: NewService,"
  }
}
```
*Rule: `target_content` must be an exact, unique string match from the existing file.*

5. CREATE_FILE
Create a completely new file (use only if the file does not exist).
```json
{
  "action": "CREATE_FILE",
  "args": {
    "file_path": "path/to/file.ts",
    "content": "// COMPLETE new file content here..."
  }
}
```

6. COMPILE
Run the project compiler (e.g. tsc --noEmit) to get FRESH error output. Use this after applying fixes to verify your progress. This is your most important tool — always compile after a batch of fixes.
```json
{
  "action": "COMPILE"
}
```

7. FINISHED
When the last COMPILE returned 0 errors, declare done.
```json
{
  "action": "FINISHED"
}
```

Rules:
- You may output multiple JSON action blocks in a single response.
- Do not use markdown wrappers around the JSON unless it is exactly ```json ... ```.
- Use REPLACE_CONTENT to modify existing files. Provide enough lines in `target_content` to make it unique.
- Only use CREATE_FILE for brand new files, and provide the FULL file content.
- If you see an error about a missing file, SEARCH for it before blindly creating it!
- IMPORTANT: When the ERROR LINE MAPPING section identifies which method contains an error, FIX THAT SPECIFIC METHOD. Do NOT fix a different method that calls it.
- CRITICAL WORKFLOW: Fix errors → COMPILE → check remaining errors → fix more → COMPILE → repeat until 0 errors.
- When many errors reference the same type/interface/class, fix the TYPE DEFINITION first — a single fix there may resolve many errors at once. Then COMPILE to verify.
- When you see errors in files you did NOT modify, check if a file you DID modify broke them (use READ_ORIGINAL to compare). Fix your file correctly rather than editing many consumer files.
- When fixing a shared model/interface file, ensure your fix preserves backward compatibility with existing consumers unless the ticket explicitly requires removing something."""

    def _parse_json_actions(self, text: str) -> list[dict]:
        actions = []
        # Find all JSON blocks
        matches = re.findall(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if not matches:
            # Fallback: try to find anything that looks like a JSON object
            matches = re.findall(r'(\{\s*"action"\s*:.*?\})', text, re.DOTALL)
            
        for match in matches:
            try:
                parsed = json.loads(match)
                if isinstance(parsed, list):
                    actions.extend(parsed)
                elif isinstance(parsed, dict):
                    actions.append(parsed)
            except json.JSONDecodeError:
                pass
        return actions

    def _preload_error_files(self, error_text: str) -> dict[str, str]:
        """Extract file paths from compiler errors and preload their contents."""
        preloaded = {}
        # Regex to catch paths with line numbers (e.g. src/app/file.ts(12,3) or src/app/file.java:[12,3])
        path_rx = re.compile(
            r"(?P<path>"
            r"(?:[A-Za-z]:[/\\]|/[A-Za-z]:/)[^\n\r\s]*?"  # absolute Windows
            r"|"
            r"[^:\n\r\[\s]+?"                              # relative
            r")"
            r"\.(?:ts|tsx|js|jsx|html|scss|css|cs|java|py|kt|scala)"
            r"(?:\(|\:\[|\:)",
            re.IGNORECASE
        )
        
        matches = path_rx.findall(error_text)
        # Deduplicate while preserving order
        unique_paths = list(dict.fromkeys(matches))
        
        for p in unique_paths[:10]:  # Limit to avoid massive context
            fp, content = self._resolve_and_read(p)
            if content:
                preloaded[fp] = content
        return preloaded

    def _map_errors_to_methods(self, error_text: str, preloaded_files: dict[str, str]) -> str:
        """Parse error line numbers and map each to the method that contains it.

        Compiler errors like 'File.java:[952,57] unreported exception' tell us
        the line number but not which method that line belongs to.  The LLM may
        then edit the wrong method (e.g. a caller instead of the method that
        actually contains line 952).

        This method builds an explicit mapping:
            ERROR LINE MAPPING:
              File.java:952 → inside method `isDeliverableNameUnique` (lines 943-961)
              File.java:945 → inside method `isDeliverableNameUnique` (lines 943-961)

        so the LLM knows exactly which method to target its fix on.
        """
        from ticket_to_code.agents.smart_extract import _parse_java_ts_boundaries, _parse_python_boundaries

        # Parse file:line pairs from error text
        # Handles Maven:      File.java:[952,57]
        # Handles TypeScript:  File.ts(12,3)
        # Handles simple:     File.java:952
        line_rx = re.compile(
            r'(?P<path>[^\s]+\.(?:java|ts|tsx|js|jsx|py|kt|scala))'
            r'(?:'
            r':\[(?P<line1>\d+)'          # Maven format:  file.java:[952,57]
            r'|'
            r'\((?P<line2>\d+)'           # TS format:     file.ts(12,3)
            r'|'
            r':(?P<line3>\d+)'            # Simple format: file.java:952
            r')',
            re.IGNORECASE
        )

        error_locations: list[tuple[str, int]] = []
        for m in line_rx.finditer(error_text):
            raw_path = m.group('path')
            line_str = m.group('line1') or m.group('line2') or m.group('line3')
            if line_str:
                error_locations.append((raw_path, int(line_str)))

        if not error_locations:
            return ""

        # Build method boundaries for each file we have preloaded
        file_boundaries: dict[str, list] = {}
        for fp, content in preloaded_files.items():
            file_lines = content.splitlines()
            ext = fp.rsplit('.', 1)[-1].lower() if '.' in fp else ''
            if ext in ('java', 'ts', 'tsx', 'js', 'jsx', 'kt', 'scala'):
                file_boundaries[fp] = _parse_java_ts_boundaries(file_lines)
            elif ext == 'py':
                file_boundaries[fp] = _parse_python_boundaries(file_lines)

        # Map each error location to its containing method
        mappings: list[str] = []
        seen = set()
        for raw_path, line_num in error_locations:
            # Normalize path for matching
            norm = raw_path.replace('\\', '/').lower()
            matched_fp = None
            for fp in file_boundaries:
                if norm.endswith(fp.lower()) or fp.lower().endswith(norm):
                    matched_fp = fp
                    break
                # Also try basename match
                if norm.split('/')[-1] == fp.split('/')[-1]:
                    matched_fp = fp
                    break

            if not matched_fp:
                continue

            boundaries = file_boundaries[matched_fp]
            # Find which method contains this line (0-indexed internally, errors are 1-indexed)
            error_line_0 = line_num - 1
            containing_method = None
            for mb in boundaries:
                if mb.kind == "class":
                    continue
                if mb.start_line <= error_line_0 <= mb.end_line:
                    containing_method = mb
                    break

            if containing_method:
                key = (matched_fp, containing_method.name, line_num)
                if key not in seen:
                    seen.add(key)
                    sig_preview = containing_method.signature[:200] if containing_method.signature else ""
                    mappings.append(
                        f"  {raw_path}:{line_num} → inside method `{containing_method.name}` "
                        f"(lines {containing_method.start_line+1}-{containing_method.end_line+1})"
                        f"\n    Signature: {sig_preview}"
                    )
                    # Store structured data for patch target validation
                    norm_fp = matched_fp.replace('\\', '/').lower()
                    if norm_fp not in self._error_method_map:
                        self._error_method_map[norm_fp] = []
                    self._error_method_map[norm_fp].append(
                        (line_num, containing_method.name,
                         containing_method.start_line, containing_method.end_line)
                    )

        if not mappings:
            return ""

        return (
            "ERROR LINE MAPPING (which method contains each error line — "
            "fix THESE methods, not their callers):\n"
            + "\n".join(mappings) + "\n\n"
        )

    def _validate_patch_target(self, file_path: str, target_content: str) -> str:
        """Validate that a REPLACE_CONTENT patch targets a method containing an error line.

        Returns empty string if valid (or if validation is not applicable).
        Returns rejection message string if the patch targets the wrong method.

        This is defense-in-depth: even if the LLM ignores the prompt instruction
        to fix the correct method, this deterministic check catches it.
        """
        if not self._error_method_map:
            return ""  # No mappings available, skip validation

        # Normalize the patch file path
        norm_fp = file_path.replace('\\', '/').lower()

        # Find matching file in our error map
        matched_key = None
        for key in self._error_method_map:
            if norm_fp.endswith(key) or key.endswith(norm_fp):
                matched_key = key
                break
            # Basename match
            if norm_fp.split('/')[-1] == key.split('/')[-1]:
                matched_key = key
                break

        if not matched_key:
            return ""  # This file has no error mappings, allow the patch

        # Read the current file content to find WHERE the target_content falls
        fp_resolved, content = self._resolve_and_read(file_path)
        if not content or target_content not in content:
            return ""  # Can't validate if we can't read or find the target

        # Find the line number where target_content starts
        lines_before = content[:content.index(target_content)].count('\n')
        target_start_line = lines_before  # 0-indexed

        # Get the error-containing methods for this file
        error_methods = self._error_method_map[matched_key]
        expected_method_names = set()
        for error_line, method_name, method_start, method_end in error_methods:
            expected_method_names.add(method_name)

        # Check: does target_content fall inside ANY of the error-containing methods?
        target_in_error_method = False
        for error_line, method_name, method_start, method_end in error_methods:
            if method_start <= target_start_line <= method_end:
                target_in_error_method = True
                break

        if target_in_error_method:
            return ""  # Patch targets the correct method

        # Determine what method the patch IS targeting (for the rejection message)
        from ticket_to_code.agents.smart_extract import _parse_java_ts_boundaries, _parse_python_boundaries
        file_lines = content.splitlines()
        ext = file_path.rsplit('.', 1)[-1].lower() if '.' in file_path else ''
        if ext in ('java', 'ts', 'tsx', 'js', 'jsx', 'kt', 'scala'):
            boundaries = _parse_java_ts_boundaries(file_lines)
        elif ext == 'py':
            boundaries = _parse_python_boundaries(file_lines)
        else:
            return ""  # Can't validate unknown file types

        actual_target_method = "unknown"
        for mb in boundaries:
            if mb.kind == "class":
                continue
            if mb.start_line <= target_start_line <= mb.end_line:
                actual_target_method = mb.name
                break

        expected_names = ", ".join(f"`{n}`" for n in sorted(expected_method_names))
        return (
            f"PATCH TARGET VALIDATION FAILED.\n\n"
            f"Your patch modifies method `{actual_target_method}`, but the compiler error "
            f"is inside method {expected_names}.\n\n"
            f"You must fix {expected_names} directly — for example by adding a `throws` "
            f"clause to its signature, or wrapping the call in a try-catch. "
            f"Do NOT propagate the exception to callers.\n\n"
            f"Please generate a new REPLACE_CONTENT that targets {expected_names}."
        )


    def _resolve_and_read(self, raw_path: str) -> tuple[str, Optional[str]]:
        fp = raw_path.replace("\\", "/")
        
        # Try absolute
        abs_path = Path(fp)
        if not abs_path.is_absolute():
            abs_path = self.workspace_path / fp
            
        # Try finding it if the compiler used a relative path not rooted at workspace
        if not abs_path.exists():
            target_name = Path(fp).name.lower()
            import os
            for root, dirs, files in os.walk(str(self.workspace_path)):
                dirs[:] = [d for d in dirs if d not in {"node_modules", "dist", ".git", "build", "target", ".venv"}]
                for f in files:
                    if f.lower() == target_name:
                        rel = str(Path(root, f).relative_to(self.workspace_path)).replace("\\", "/")
                        if fp.endswith(rel.split("/", 1)[-1] if "/" in rel else rel):
                            abs_path = self.workspace_path / rel
                            fp = rel
                            break
        
        if abs_path.exists():
            # If we've already modified it in this run, return our modified version
            if fp.lower() in self.modified_files:
                return fp, self.modified_files[fp.lower()]
            try:
                return fp, abs_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass
        return fp, None

    def _tool_search_workspace(self, query: str) -> str:
        logger.info(f"Tool SEARCH_WORKSPACE: {query}")
        matches = set()
        
        # Tier 1: SQLite Store
        if self.sqlite_store:
            try:
                rows = self.sqlite_store._conn.execute(
                    "SELECT DISTINCT path FROM symbols WHERE name LIKE ? OR path LIKE ? LIMIT 10",
                    (f"%{query}%", f"%{query}%")
                ).fetchall()
                for (path,) in rows:
                    if path:
                        matches.add(path.replace("\\", "/"))
            except Exception as e:
                logger.debug(f"SQLite search failed: {e}")
                
        # Tier 2: rglob (if not enough matches)
        if not matches:
            try:
                # Basic kebab conversion for Angular paths
                kebab = re.sub(r'(?<=[a-z])(?=[A-Z])', '-', query).lower()
                stem = kebab.replace('-', '')
                
                skip_dirs = {'node_modules', 'dist', '.git', 'build', 'target'}
                for p in self.workspace_path.rglob('*'):
                    if not p.is_file(): continue
                    if skip_dirs.intersection(p.parts): continue
                    
                    p_stem = p.stem.replace('-', '').replace('.', '').lower()
                    if stem in p_stem or query.lower() in p_stem:
                        rel = str(p.relative_to(self.workspace_path)).replace('\\', '/')
                        matches.add(rel)
                        if len(matches) > 10:
                            break
            except Exception:
                pass
                
        if not matches:
            return f"No matches found in workspace for '{query}'."
        return "Found the following matching files:\n" + "\n".join(sorted(matches))

    def _tool_read_file(self, file_path: str) -> str:
        logger.info(f"Tool READ_FILE: {file_path}")
        fp, content = self._resolve_and_read(file_path)
        if content is None:
            return f"Error: Could not read file '{file_path}'. It may not exist."
        return content

    def _tool_read_original(self, file_path: str) -> str:
        """Read the ORIGINAL content of a file before our workflow modified it.

        Uses pre_edit_snapshots (captured from state['original_file_contents'])
        which are taken before any code generation happens — no git needed.
        """
        logger.info(f"Tool READ_ORIGINAL: {file_path}")
        if not self.pre_edit_snapshots:
            return "No pre-edit snapshots available. Cannot compare with original."

        fp_norm = file_path.replace("\\", "/").lower()
        # Try exact match first
        for snap_path, snap_content in self.pre_edit_snapshots.items():
            if snap_path.replace("\\", "/").lower() == fp_norm:
                return snap_content
        # Try basename match
        fp_basename = fp_norm.split("/")[-1]
        for snap_path, snap_content in self.pre_edit_snapshots.items():
            snap_basename = snap_path.replace("\\", "/").split("/")[-1].lower()
            if snap_basename == fp_basename:
                return snap_content
        # Try suffix match (handles relative vs absolute paths)
        for snap_path, snap_content in self.pre_edit_snapshots.items():
            snap_norm = snap_path.replace("\\", "/").lower()
            if fp_norm.endswith(snap_norm) or snap_norm.endswith(fp_norm):
                return snap_content

        return f"No pre-edit snapshot found for '{file_path}'. This file may not have been modified by our workflow."

    def _tool_edit_file(self, file_path: str, content: str) -> str:
        logger.info(f"Tool EDIT_FILE: {file_path} ({len(content)} chars)")
        if not content.strip():
            return "Error: Content provided was empty."
            
        fp = file_path.replace("\\", "/")
        self.modified_files[fp.lower()] = content
        return f"Successfully updated (in memory): {fp}"

    def _tool_replace_content(self, file_path: str, target_content: str, replacement_content: str) -> str:
        logger.info(f"Tool REPLACE_CONTENT: {file_path}")
        fp, content = self._resolve_and_read(file_path)
        if content is None:
            return f"Error: Could not read file '{file_path}'. It may not exist."
        
        # Exact match required
        if target_content not in content:
            return "Error: target_content not found in the file. Ensure you copied the target lines EXACTLY, including all leading whitespace, indentation, and special characters."
            
        count = content.count(target_content)
        if count > 1:
            return f"Error: target_content matches {count} places in the file. Please provide a larger block of code in target_content so it uniquely identifies the code to replace."
            
        new_content = content.replace(target_content, replacement_content)
        self.modified_files[fp.lower()] = new_content
        return f"Successfully updated (in memory): {fp}"

    def _tool_compile(self) -> str:
        """Run the project compiler and return fresh error output.

        First writes all in-memory modifications to disk so the compiler sees them,
        runs `tsc --noEmit`, then returns the result.
        """
        logger.info("Tool COMPILE: Running project compiler...")

        if not self.compile_root:
            return "Error: No compile root configured. Cannot run compiler."

        # Write in-memory modifications to disk before compiling
        for fp_lower, content in self.modified_files.items():
            fp = fp_lower.replace("/", "\\" if sys.platform == "win32" else "/")
            abs_path = self.workspace_path / fp
            if not abs_path.is_absolute():
                abs_path = self.workspace_path / fp
            try:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(content, encoding="utf-8")
                logger.info(f"  COMPILE: Wrote {fp} to disk")
            except Exception as e:
                logger.warning(f"  COMPILE: Failed to write {fp}: {e}")

        # Run tsc --noEmit
        use_shell = sys.platform == "win32"
        tsconfig_app = self.compile_root / "tsconfig.app.json"
        if tsconfig_app.exists():
            cmd: Any = (
                "npx --no-install tsc --noEmit --pretty false -p tsconfig.app.json"
                if use_shell
                else ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false", "-p", "tsconfig.app.json"]
            )
        else:
            cmd = (
                "npx --no-install tsc --noEmit --pretty false"
                if use_shell
                else ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"]
            )

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.compile_root),
                capture_output=True,
                text=True,
                timeout=180,
                shell=use_shell,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return "COMPILE: Timed out after 180 seconds."
        except Exception as e:
            return f"COMPILE ERROR: {e}"

        if result.returncode == 0:
            logger.info("  COMPILE: ✅ BUILD SUCCESS — 0 errors!")
            return "✅ BUILD SUCCESS — 0 errors! You can now call FINISHED."

        # Parse errors from output
        raw_output = (result.stdout + "\n" + result.stderr).strip()
        # Strip ANSI codes
        ansi_rx = re.compile(r'\x1b\[[0-9;]*m')
        clean_output = ansi_rx.sub('', raw_output)
        error_lines = [line for line in clean_output.splitlines() if line.strip()]

        logger.info(f"  COMPILE: ❌ {len(error_lines)} error line(s) remaining")
        # Return up to 40 error lines so the LLM has enough context
        truncated = error_lines[:40]
        remaining_msg = ""
        if len(error_lines) > 40:
            remaining_msg = f"\n... and {len(error_lines) - 40} more errors (fix the above first)"

        return (
            f"❌ BUILD FAILED — {len(error_lines)} error(s) remaining:\n"
            + "\n".join(truncated)
            + remaining_msg
            + "\n\nFix these errors and COMPILE again."
        )

