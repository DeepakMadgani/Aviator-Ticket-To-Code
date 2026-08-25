"""Skill Block: build_runner — executes real builds (mvn compile / npm run build).

Produces structured BuildResult observations, not just pass/fail strings.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)

DEFAULT_CC4E_PATH = r"C:\CC4E"

# Build commands per service type
BUILD_COMMANDS = {
    "java": ["mvn", "compile", "-q", "-pl", "{service}", "-am"],
    "typescript": ["npm", "run", "build"],
    "angular": ["npm", "run", "build"],
}


class BuildRunnerSkill(SkillBlock):
    """Execute real build commands and parse structured errors."""

    name = "build_runner"
    description = "Run mvn compile or npm run build and return structured results"

    def __init__(self, cc4e_path: str = DEFAULT_CC4E_PATH):
        self.cc4e_path = Path(cc4e_path)

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Run a build for the specified service.

        Args (via kwargs):
            service: Service name (e.g., "area-service")
            build_type: "java" or "typescript" (auto-detected if omitted)
            timeout: Max seconds for build (default: 120)
        """
        service = kwargs.get("service", "")
        build_type = kwargs.get("build_type", "")
        timeout = kwargs.get("timeout", 120)

        if not service:
            return {"passed": False, "errors": [], "output": "No service specified"}

        service_path = self.cc4e_path / service

        # Auto-detect build type
        if not build_type:
            if (service_path / "pom.xml").exists():
                build_type = "java"
            elif (service_path / "package.json").exists():
                build_type = "typescript"
            elif service == "xchange-ui":
                build_type = "angular"
            else:
                return {"passed": False, "errors": [], "output": f"Cannot detect build type for {service}"}

        # Build the command
        cmd_template = BUILD_COMMANDS.get(build_type)
        if not cmd_template:
            return {"passed": False, "errors": [], "output": f"Unknown build type: {build_type}"}

        cmd = [c.replace("{service}", service) for c in cmd_template]
        cwd = self.cc4e_path if build_type == "java" else service_path

        logger.info("BuildRunner: running '%s' in %s", " ".join(cmd), cwd)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )

            passed = result.returncode == 0
            output = result.stdout + result.stderr
            errors = self._parse_errors(output, build_type) if not passed else []

            logger.info(
                "BuildRunner: %s — %s (%d errors)",
                service, "PASS" if passed else "FAIL", len(errors),
            )

            return {
                "passed": passed,
                "errors": errors,
                "output": output[-2000:],  # Last 2KB — enough for error context
                "service": service,
                "build_type": build_type,
            }

        except subprocess.TimeoutExpired:
            return {
                "passed": False,
                "errors": [{"type": "timeout", "message": f"Build timed out after {timeout}s"}],
                "output": f"Build timed out after {timeout}s",
                "service": service,
            }
        except FileNotFoundError as e:
            return {
                "passed": False,
                "errors": [{"type": "tool_missing", "message": str(e)}],
                "output": f"Build tool not found: {e}",
                "service": service,
            }

    def _parse_errors(self, output: str, build_type: str) -> list[dict[str, Any]]:
        """Parse structured errors from build output."""
        errors: list[dict[str, Any]] = []

        if build_type == "java":
            errors.extend(self._parse_java_errors(output))
        elif build_type in ("typescript", "angular"):
            errors.extend(self._parse_ts_errors(output))

        return errors

    def _parse_java_errors(self, output: str) -> list[dict[str, Any]]:
        """Parse Java/Maven compilation errors."""
        errors: list[dict[str, Any]] = []
        # Pattern: [ERROR] /path/File.java:[line,col] error: message
        pattern = re.compile(
            r"\[ERROR\]\s*(.+\.java):\[(\d+),\d+\]\s*(?:error:\s*)?(.*)", re.IGNORECASE
        )
        for match in pattern.finditer(output):
            file_path, line, message = match.groups()
            # Extract symbol from common error messages
            symbol = ""
            sym_match = re.search(r"cannot find symbol.*symbol:\s*(\w+\s+\w+)", message)
            if sym_match:
                symbol = sym_match.group(1)

            errors.append({
                "type": "compilation",
                "file": file_path.strip(),
                "line": int(line),
                "message": message.strip(),
                "symbol": symbol,
            })

        # Also catch "package does not exist" and "cannot find symbol"
        pkg_pattern = re.compile(r"package\s+([\w.]+)\s+does not exist")
        for match in pkg_pattern.finditer(output):
            errors.append({
                "type": "dependency",
                "file": "",
                "line": 0,
                "message": f"Package does not exist: {match.group(1)}",
                "symbol": match.group(1),
            })

        return errors

    def _parse_ts_errors(self, output: str) -> list[dict[str, Any]]:
        """Parse TypeScript compilation errors."""
        errors: list[dict[str, Any]] = []
        # Pattern: src/app/file.ts(line,col): error TSxxxx: message
        pattern = re.compile(
            r"(.+\.tsx?)\((\d+),\d+\):\s*error\s+TS\d+:\s*(.*)", re.IGNORECASE
        )
        for match in pattern.finditer(output):
            file_path, line, message = match.groups()
            errors.append({
                "type": "compilation",
                "file": file_path.strip(),
                "line": int(line),
                "message": message.strip(),
                "symbol": "",
            })

        return errors
