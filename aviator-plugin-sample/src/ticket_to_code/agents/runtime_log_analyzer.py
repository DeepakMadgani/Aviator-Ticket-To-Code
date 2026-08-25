"""
Runtime Log Analyzer — Enhancement 1

Reads workspace log files, parses stack traces, and maps errors
to indexed source files. Produces a RuntimeDiagnosis that enriches
the investigation result.

Safety: This agent is READ-ONLY. It only reads log files.
It never writes to the filesystem or modifies any code.

Author: Deepak Madgani
Date: July 2026
"""

import re
import os
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ============================================================================
# MODELS
# ============================================================================

class StackTraceEntry(BaseModel):
    """One frame from a parsed stack trace."""
    file_path: str = Field(..., description="File path from stack trace")
    line_number: int = Field(..., description="Line number")
    function_name: Optional[str] = Field(None, description="Function/method name")
    code_snippet: Optional[str] = Field(None, description="Code at that line if available")


class RuntimeDiagnosis(BaseModel):
    """Structured diagnosis from runtime log analysis."""
    has_runtime_data: bool = Field(False, description="Whether any logs were found and parsed")
    error_type: Optional[str] = Field(None, description="e.g. NameError, NullPointerException")
    error_message: Optional[str] = Field(None, description="The error message text")
    stack_trace: List[StackTraceEntry] = Field(default_factory=list)
    root_file: Optional[str] = Field(None, description="Primary file where error originated")
    root_line: Optional[int] = Field(None, description="Line number of root error")
    diagnosis: Optional[str] = Field(None, description="LLM-generated diagnosis summary")
    log_source: Optional[str] = Field(None, description="Which log file this came from")
    confidence: float = Field(0.0, ge=0.0, le=1.0)


# ============================================================================
# STACK TRACE PARSERS
# ============================================================================

# Python:  File "app.py", line 84, in handle_request
_PYTHON_FRAME = re.compile(
    r'File "([^"]+)", line (\d+)(?:, in (\w+))?'
)

# Java:    at com.example.Service.method(Service.java:142)
_JAVA_FRAME = re.compile(
    r'at\s+([\w.$]+)\(([\w.]+):(\d+)\)'
)

# C#:      at Namespace.Class.Method() in C:\path\File.cs:line 234
_CSHARP_FRAME = re.compile(
    r'in\s+(.+?):line\s+(\d+)'
)

# Node.js: at functionName (/path/to/file.js:42:10)
_NODE_FRAME = re.compile(
    r'at\s+(?:\S+\s+)?\(?([^:)]+):(\d+):\d+\)?'
)

# Error type + message extractors
_PYTHON_ERROR = re.compile(
    r'^(\w+Error|\w+Exception|\w+Warning):\s*(.+)$', re.MULTILINE
)
_JAVA_ERROR = re.compile(
    r'^([\w.]*(?:Error|Exception)):\s*(.*)$', re.MULTILINE
)
_CSHARP_ERROR = re.compile(
    r'^(System\.[\w.]*(?:Exception)):\s*(.+)$', re.MULTILINE
)
_GENERIC_ERROR = re.compile(
    r'((?:Error|Exception|FATAL|CRITICAL|Traceback)[^\n]{0,200})',
    re.IGNORECASE
)


# ============================================================================
# RUNTIME LOG ANALYZER
# ============================================================================

class RuntimeLogAnalyzer:
    """
    Reads application logs, parses stack traces, and maps errors
    to source files already in the SQLite index.

    Safety: This agent is READ-ONLY. It only reads log files.
    It never writes to the filesystem or modifies any code.
    """

    LOG_GLOBS = [
        "*.log",
        "logs/*.log",
        "logs/**/*.log",
        "output/*.log",
        "target/*.log",
        "build/*.log",
        "nohup.out",
        ".aviator/*.log",
    ]

    # Skip directories that are never useful for logs
    SKIP_DIRS = {
        "node_modules", ".git", "__pycache__", ".idea",
        ".vscode", "bin", "obj", "dist", ".angular",
    }

    def __init__(self, workspace_path: str):
        self.workspace = Path(workspace_path)

    # ------------------------------------------------------------------
    # 1. Find log files
    # ------------------------------------------------------------------

    def find_logs(self, max_age_hours: int = 48) -> List[Path]:
        """
        Discover log files in workspace, sorted by most recently modified.

        Args:
            max_age_hours: Ignore logs older than this many hours.

        Returns:
            List of Path objects, newest first. Empty list if none found.
        """
        found: List[Tuple[float, Path]] = []
        cutoff = time.time() - (max_age_hours * 3600)

        if not self.workspace.exists():
            logger.debug(f"Workspace does not exist: {self.workspace}")
            return []

        for glob_pattern in self.LOG_GLOBS:
            try:
                for p in self.workspace.glob(glob_pattern):
                    if not p.is_file():
                        continue
                    # Skip files inside excluded directories
                    if any(skip in p.parts for skip in self.SKIP_DIRS):
                        continue
                    try:
                        mtime = p.stat().st_mtime
                        if mtime >= cutoff:
                            found.append((mtime, p))
                    except OSError:
                        continue
            except Exception as exc:
                logger.debug(f"Glob {glob_pattern} failed: {exc}")

        # Newest first
        found.sort(key=lambda t: t[0], reverse=True)
        result = [p for _, p in found]
        logger.info(f"RuntimeLogAnalyzer: found {len(result)} log files in {self.workspace}")
        return result

    # ------------------------------------------------------------------
    # 2. Parse stack traces
    # ------------------------------------------------------------------

    def parse_stack_trace(self, log_content: str) -> List[StackTraceEntry]:
        """
        Extract structured stack trace entries from log text.
        Handles Python, Java, C#, and Node.js formats.

        Returns:
            List of StackTraceEntry objects (may be empty).
        """
        entries: List[StackTraceEntry] = []

        # Python frames
        for m in _PYTHON_FRAME.finditer(log_content):
            entries.append(StackTraceEntry(
                file_path=m.group(1),
                line_number=int(m.group(2)),
                function_name=m.group(3),
            ))

        # Java frames (only if no Python frames found — avoid double-matching)
        if not entries:
            for m in _JAVA_FRAME.finditer(log_content):
                full_class = m.group(1)
                file_name = m.group(2)
                line_no = int(m.group(3))
                # Extract method name from fully qualified class path
                func_name = full_class.rsplit(".", 1)[-1] if "." in full_class else None
                entries.append(StackTraceEntry(
                    file_path=file_name,
                    line_number=line_no,
                    function_name=func_name,
                ))

        # C# frames
        if not entries:
            for m in _CSHARP_FRAME.finditer(log_content):
                entries.append(StackTraceEntry(
                    file_path=m.group(1).strip(),
                    line_number=int(m.group(2)),
                ))

        # Node.js frames
        if not entries:
            for m in _NODE_FRAME.finditer(log_content):
                filepath = m.group(1)
                # Skip internal Node frames
                if "node_modules" in filepath or filepath.startswith("internal/"):
                    continue
                entries.append(StackTraceEntry(
                    file_path=filepath,
                    line_number=int(m.group(2)),
                ))

        return entries

    # ------------------------------------------------------------------
    # 3. Extract error type + message
    # ------------------------------------------------------------------

    def extract_error_info(self, log_content: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract error type and message from log text.

        Returns:
            (error_type, error_message) — either may be None.
        """
        # Try Python-style errors first
        m = _PYTHON_ERROR.search(log_content)
        if m:
            return m.group(1), m.group(2).strip()

        # Java-style
        m = _JAVA_ERROR.search(log_content)
        if m:
            return m.group(1).rsplit(".", 1)[-1], m.group(2).strip()

        # C#-style
        m = _CSHARP_ERROR.search(log_content)
        if m:
            return m.group(1).rsplit(".", 1)[-1], m.group(2).strip()

        # Generic fallback
        m = _GENERIC_ERROR.search(log_content)
        if m:
            text = m.group(1).strip()
            return "RuntimeError", text

        return None, None

    # ------------------------------------------------------------------
    # 4. Map to indexed files
    # ------------------------------------------------------------------

    def map_to_indexed_files(
        self, entries: List[StackTraceEntry], sqlite_store
    ) -> List[StackTraceEntry]:
        """
        Map stack trace file references to files in the SQLite index.

        For each entry, check if the file_path (or its basename) exists
        in the SQLite index. If found, update file_path to the indexed path.

        Args:
            entries: Stack trace entries to resolve.
            sqlite_store: SqliteStore instance (or None to skip mapping).

        Returns:
            Same entries list (mutated in-place for resolved paths).
        """
        if sqlite_store is None or not entries:
            return entries

        try:
            # Get all indexed file paths
            indexed_files = []
            try:
                indexed_files = sqlite_store.get_all_files()
            except Exception:
                # Method might be named differently — try alternatives
                try:
                    indexed_files = sqlite_store.list_files()
                except Exception:
                    logger.debug("Could not retrieve indexed file list from SQLite store")
                    return entries

            if not indexed_files:
                return entries

            # Build basename → full path lookup
            basename_map = {}
            for fp in indexed_files:
                bn = Path(fp).name.lower()
                if bn not in basename_map:
                    basename_map[bn] = []
                basename_map[bn].append(fp)

            # Resolve each entry
            for entry in entries:
                basename = Path(entry.file_path).name.lower()
                if basename in basename_map:
                    # Use the first match (most common case: unique basename)
                    entry.file_path = basename_map[basename][0]

        except Exception as exc:
            logger.warning(f"Error mapping stack trace to indexed files: {exc}")

        return entries

    # ------------------------------------------------------------------
    # 5. LLM-powered diagnosis
    # ------------------------------------------------------------------

    def _build_diagnosis(
        self,
        error_type: Optional[str],
        error_message: Optional[str],
        entries: List[StackTraceEntry],
        ticket_text: str,
    ) -> str:
        """
        Use LLM to generate a human-readable diagnosis from error data.
        Falls back to a template-based diagnosis if LLM is unavailable.
        """
        # Build a summary without LLM (fast, always works)
        parts = []
        if error_type:
            parts.append(f"Error type: {error_type}")
        if error_message:
            parts.append(f"Message: {error_message}")
        if entries:
            root = entries[0]
            parts.append(f"Origin: {root.file_path}:{root.line_number}")
            if root.function_name:
                parts.append(f"Function: {root.function_name}")

        template_diagnosis = " | ".join(parts) if parts else "No diagnosis available"

        # Try LLM for richer diagnosis
        try:
            from ticket_to_code.llm_utils import llm_invoke
            from langchain_core.messages import SystemMessage, HumanMessage

            prompt = f"""You are a senior software engineer analyzing a runtime error.

Error Type: {error_type or 'Unknown'}
Error Message: {error_message or 'Unknown'}
Stack Trace (top frames):
{chr(10).join(f'  {e.file_path}:{e.line_number} in {e.function_name or "?"}' for e in entries[:5])}

Related ticket context: {ticket_text[:500]}

Provide a concise 2-3 sentence diagnosis explaining:
1. What went wrong
2. The most likely root cause
3. Which file/line to investigate first

Be specific and actionable. No generic advice."""

            result = llm_invoke(
                messages=[
                    SystemMessage(content="You are a runtime error diagnostician."),
                    HumanMessage(content=prompt),
                ],
                label="runtime_diagnosis",
            )
            if result and result.content:
                return result.content.strip()

        except Exception as exc:
            logger.debug(f"LLM diagnosis failed (using template): {exc}")

        return template_diagnosis

    # ------------------------------------------------------------------
    # 6. Main entry point
    # ------------------------------------------------------------------

    def analyze(
        self, ticket_text: str, sqlite_store=None
    ) -> RuntimeDiagnosis:
        """
        Main entry point. Finds logs, parses stack traces, produces diagnosis.

        Args:
            ticket_text: The ticket description (for context in LLM diagnosis).
            sqlite_store: Optional SqliteStore for file resolution.

        Returns:
            RuntimeDiagnosis — always returns a valid object.
            If no logs found, has_runtime_data will be False.
        """
        logger.info(f"RuntimeLogAnalyzer: scanning {self.workspace} for logs...")

        # 1. Find log files
        logs = self.find_logs()
        if not logs:
            logger.info("RuntimeLogAnalyzer: no log files found. Returning empty diagnosis.")
            return RuntimeDiagnosis(has_runtime_data=False)

        # 2. Read logs (most recent first, stop at first stack trace)
        best_entries: List[StackTraceEntry] = []
        best_error_type: Optional[str] = None
        best_error_message: Optional[str] = None
        best_log_source: Optional[str] = None

        for log_path in logs[:5]:  # Check at most 5 most recent logs
            try:
                # Read last 50KB of the log (tail, where errors usually are)
                file_size = log_path.stat().st_size
                read_start = max(0, file_size - 50_000)

                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    if read_start > 0:
                        f.seek(read_start)
                        f.readline()  # skip partial line
                    content = f.read()

                # 3. Parse stack trace
                entries = self.parse_stack_trace(content)
                error_type, error_message = self.extract_error_info(content)

                if entries:
                    best_entries = entries
                    best_error_type = error_type
                    best_error_message = error_message
                    best_log_source = str(log_path)
                    break  # Use the first log that has a stack trace

                # Even without stack trace, capture the error type/message
                if error_type and not best_error_type:
                    best_error_type = error_type
                    best_error_message = error_message
                    best_log_source = str(log_path)

            except Exception as exc:
                logger.debug(f"Error reading {log_path}: {exc}")
                continue

        # 4. If no stack trace found anywhere
        if not best_entries and not best_error_type:
            logger.info("RuntimeLogAnalyzer: logs found but no errors/stack traces detected.")
            return RuntimeDiagnosis(has_runtime_data=False)

        # 5. Map to indexed files
        if best_entries:
            best_entries = self.map_to_indexed_files(best_entries, sqlite_store)

        # 6. Determine root file
        root_file = None
        root_line = None
        if best_entries:
            root = best_entries[0]
            root_file = Path(root.file_path).name
            root_line = root.line_number

        # 7. Generate diagnosis
        diagnosis_text = self._build_diagnosis(
            best_error_type, best_error_message, best_entries, ticket_text
        )

        # 8. Compute confidence
        confidence = 0.0
        if best_entries and best_error_type:
            confidence = 0.9  # Strong: both stack trace and error type
        elif best_entries:
            confidence = 0.7  # Stack trace but no clear error type
        elif best_error_type:
            confidence = 0.5  # Error type but no stack trace

        result = RuntimeDiagnosis(
            has_runtime_data=True,
            error_type=best_error_type,
            error_message=best_error_message,
            stack_trace=best_entries,
            root_file=root_file,
            root_line=root_line,
            diagnosis=diagnosis_text,
            log_source=best_log_source,
            confidence=confidence,
        )

        logger.info(
            f"RuntimeLogAnalyzer: diagnosis complete. "
            f"error_type={best_error_type}, root_file={root_file}:{root_line}, "
            f"confidence={confidence}"
        )
        return result
