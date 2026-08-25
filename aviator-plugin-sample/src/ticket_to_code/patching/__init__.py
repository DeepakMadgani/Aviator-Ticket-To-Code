"""
Patching Module - AST-based Code Modifications

Provides symbol-level patching instead of full file rewrites.
"""

from .ast_patch_engine import (
    ASTPatchEngine,
    JavaPatchEngine,
    PatchEngineFactory,
    PatchOperation,
    PatchOperationType,
    PatchResult
)

__all__ = [
    "ASTPatchEngine",
    "JavaPatchEngine",
    "PatchEngineFactory",
    "PatchOperation",
    "PatchOperationType",
    "PatchResult"
]
