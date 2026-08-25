"""Karpathy Principle 3: Surgical Changes — Only touch what you must."""

from __future__ import annotations

from aviator_core.ticket_solver.gates import BaseGate, GateResult
from aviator_core.ticket_solver.models import Patch


class SurgicalChangesGate(BaseGate):
    """Ensures patches don't touch code outside the ticket scope.

    Propagation-aware: allows edits in the dependency chain of target files.
    """

    gate_name = "surgical_changes"

    def validate(
        self,
        patches: list[Patch],
        target_files: list[str],
        dependency_chain: list[str] | None = None,
    ) -> GateResult:
        """Check that patches only modify target files and their dependency chain."""
        allowed_files = set(target_files) | set(dependency_chain or [])
        issues: list[str] = []

        for patch in patches:
            # File must be in the identified target set or dependency chain
            if patch.file_path not in allowed_files:
                issues.append(
                    f"Patch modifies '{patch.file_path}' which is outside the target + "
                    f"dependency chain. Is this really needed for the fix?"
                )

            # Flag formatting-only changes
            for hunk in patch.hunks:
                if hunk.is_whitespace_only():
                    issues.append(
                        f"Whitespace-only change in {patch.file_path}:{hunk.start_line}. "
                        f"Don't clean up code you didn't need to touch."
                    )

            # Flag comment-only changes on untouched logic lines
            for hunk in patch.hunks:
                if hunk.is_comment_only_change() and not hunk.touches_logic():
                    issues.append(
                        f"Comment-only change in {patch.file_path}:{hunk.start_line} "
                        f"where logic wasn't modified."
                    )

        return self._make_result(
            issues,
            files_patched=[p.file_path for p in patches],
            target_files=target_files,
            dependency_chain=dependency_chain or [],
        )
