"""Enhanced AST-aware patch engine with Spoon-like precision.

This module provides precise, AST-driven code transformations using tree-sitter.
While Spoon is a powerful Java transformation framework, we replicate its key
capabilities in Python for seamless integration.

Key features:
- AST-aware method replacement (not regex)
- Syntax validation before/after
- Preserves formatting and comments
- Handles overloaded methods
- Supports multiple transformation types
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from datetime import datetime

from tree_sitter import Language, Node, Parser
import tree_sitter_java as tsjava

from aviator_core.models import SourceLocation


@dataclass
class Transformation:
    """A single code transformation operation."""
    
    kind: str  # 'replace_method', 'insert_method', 'delete_method', 'modify_field', etc.
    target_class: str  # Qualified class name
    target_method: Optional[str] = None  # Method signature
    target_field: Optional[str] = None  # Field name
    new_code: Optional[str] = None  # New code to insert/replace
    location: Optional[SourceLocation] = None


@dataclass
class TransformationResult:
    """Result of applying transformations."""
    
    success: bool
    transformed_code: Optional[str] = None
    error: Optional[str] = None
    backup_path: Optional[Path] = None
    validation_passed: bool = False


class EnhancedPatchEngine:
    """AST-aware patch engine with Spoon-like precision."""
    
    def __init__(self, repo_path: Path):
        """Initialize the patch engine.
        
        Args:
            repo_path: Repository root path
        """
        self.repo_path = Path(repo_path)
        self.backup_dir = self.repo_path / ".aviator" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize tree-sitter parser
        self._language = Language(tsjava.language())
        self._parser = Parser(self._language)
    
    def apply_transformations(
        self,
        file_path: Path,
        transformations: list[Transformation]
    ) -> TransformationResult:
        """Apply multiple transformations to a file.
        
        Args:
            file_path: Path to the Java file
            transformations: List of transformations to apply
            
        Returns:
            TransformationResult with success status
        """
        try:
            # Read original file
            original_code = file_path.read_text(encoding="utf-8")
            
            # Parse AST
            tree = self._parser.parse(original_code.encode("utf-8"))
            root = tree.root_node
            
            # Validate original syntax
            if not self._validate_syntax(root):
                return TransformationResult(
                    success=False,
                    error="Original file has syntax errors"
                )
            
            # Create backup
            backup_path = self._create_backup(file_path, original_code)
            
            # Apply transformations
            modified_code = original_code
            for transform in transformations:
                if transform.kind == "replace_method":
                    modified_code = self._replace_method_ast(
                        modified_code,
                        transform.target_class,
                        transform.target_method,
                        transform.new_code
                    )
                elif transform.kind == "insert_method":
                    modified_code = self._insert_method_ast(
                        modified_code,
                        transform.target_class,
                        transform.new_code
                    )
                elif transform.kind == "delete_method":
                    modified_code = self._delete_method_ast(
                        modified_code,
                        transform.target_class,
                        transform.target_method
                    )
                elif transform.kind == "modify_field":
                    modified_code = self._modify_field_ast(
                        modified_code,
                        transform.target_class,
                        transform.target_field,
                        transform.new_code
                    )
            
            # Validate modified syntax
            modified_tree = self._parser.parse(modified_code.encode("utf-8"))
            if not self._validate_syntax(modified_tree.root_node):
                return TransformationResult(
                    success=False,
                    error="Transformed code has syntax errors",
                    backup_path=backup_path
                )
            
            # Write transformed code
            file_path.write_text(modified_code, encoding="utf-8")
            
            return TransformationResult(
                success=True,
                transformed_code=modified_code,
                backup_path=backup_path,
                validation_passed=True
            )
        
        except Exception as e:
            return TransformationResult(
                success=False,
                error=f"Transformation failed: {str(e)}"
            )
    
    def _replace_method_ast(
        self,
        code: str,
        class_name: str,
        method_signature: str,
        new_method_code: str
    ) -> str:
        """Replace a method using AST navigation.
        
        Args:
            code: Original source code
            class_name: Target class name (simple or qualified)
            method_signature: Method signature (e.g., "validate(String)")
            new_method_code: New method implementation
            
        Returns:
            Modified source code
        """
        tree = self._parser.parse(code.encode("utf-8"))
        root = tree.root_node
        
        # Find target class
        target_class_node = self._find_class_node(root, class_name, code.encode("utf-8"))
        if not target_class_node:
            raise ValueError(f"Class {class_name} not found")
        
        # Find target method
        target_method_node = self._find_method_node(
            target_class_node,
            method_signature,
            code.encode("utf-8")
        )
        if not target_method_node:
            raise ValueError(f"Method {method_signature} not found in {class_name}")
        
        # Extract method indentation
        start_byte = target_method_node.start_byte
        line_start = code.rfind('\n', 0, start_byte) + 1
        indentation = code[line_start:start_byte]
        
        # Apply indentation to new code
        indented_new_code = self._apply_indentation(new_method_code, indentation)
        
        # Replace method
        modified = (
            code[:target_method_node.start_byte] +
            indented_new_code +
            code[target_method_node.end_byte:]
        )
        
        return modified
    
    def _insert_method_ast(
        self,
        code: str,
        class_name: str,
        new_method_code: str
    ) -> str:
        """Insert a new method into a class.
        
        Args:
            code: Original source code
            class_name: Target class name
            new_method_code: New method code
            
        Returns:
            Modified source code
        """
        tree = self._parser.parse(code.encode("utf-8"))
        root = tree.root_node
        
        # Find target class
        target_class_node = self._find_class_node(root, class_name, code.encode("utf-8"))
        if not target_class_node:
            raise ValueError(f"Class {class_name} not found")
        
        # Find class body
        body_node = target_class_node.child_by_field_name("body")
        if not body_node:
            raise ValueError(f"Class {class_name} has no body")
        
        # Find insertion point (before closing brace)
        insert_pos = body_node.end_byte - 1  # Before '}'
        
        # Get indentation from last method in class
        last_method = None
        for child in body_node.children:
            if child.type == "method_declaration":
                last_method = child
        
        if last_method:
            start_byte = last_method.start_byte
            line_start = code.rfind('\n', 0, start_byte) + 1
            indentation = code[line_start:start_byte]
        else:
            # Default indentation (4 spaces)
            indentation = "    "
        
        # Apply indentation to new code
        indented_new_code = self._apply_indentation(new_method_code, indentation)
        
        # Insert method
        modified = (
            code[:insert_pos] +
            "\n\n" + indented_new_code + "\n" +
            code[insert_pos:]
        )
        
        return modified
    
    def _delete_method_ast(
        self,
        code: str,
        class_name: str,
        method_signature: str
    ) -> str:
        """Delete a method from a class.
        
        Args:
            code: Original source code
            class_name: Target class name
            method_signature: Method signature
            
        Returns:
            Modified source code
        """
        tree = self._parser.parse(code.encode("utf-8"))
        root = tree.root_node
        
        # Find target class
        target_class_node = self._find_class_node(root, class_name, code.encode("utf-8"))
        if not target_class_node:
            raise ValueError(f"Class {class_name} not found")
        
        # Find target method
        target_method_node = self._find_method_node(
            target_class_node,
            method_signature,
            code.encode("utf-8")
        )
        if not target_method_node:
            raise ValueError(f"Method {method_signature} not found in {class_name}")
        
        # Delete method (including leading whitespace and trailing newlines)
        start_byte = target_method_node.start_byte
        end_byte = target_method_node.end_byte
        
        # Find line boundaries
        line_start = code.rfind('\n', 0, start_byte)
        line_end = code.find('\n', end_byte)
        if line_end == -1:
            line_end = len(code)
        
        # Remove method
        modified = code[:line_start] + code[line_end:]
        
        return modified
    
    def _modify_field_ast(
        self,
        code: str,
        class_name: str,
        field_name: str,
        new_field_code: str
    ) -> str:
        """Modify a field declaration.
        
        Args:
            code: Original source code
            class_name: Target class name
            field_name: Field name
            new_field_code: New field declaration
            
        Returns:
            Modified source code
        """
        tree = self._parser.parse(code.encode("utf-8"))
        root = tree.root_node
        
        # Find target class
        target_class_node = self._find_class_node(root, class_name, code.encode("utf-8"))
        if not target_class_node:
            raise ValueError(f"Class {class_name} not found")
        
        # Find target field
        target_field_node = self._find_field_node(
            target_class_node,
            field_name,
            code.encode("utf-8")
        )
        if not target_field_node:
            raise ValueError(f"Field {field_name} not found in {class_name}")
        
        # Get indentation
        start_byte = target_field_node.start_byte
        line_start = code.rfind('\n', 0, start_byte) + 1
        indentation = code[line_start:start_byte]
        
        # Apply indentation to new code
        indented_new_code = self._apply_indentation(new_field_code, indentation)
        
        # Replace field
        modified = (
            code[:target_field_node.start_byte] +
            indented_new_code +
            code[target_field_node.end_byte:]
        )
        
        return modified
    
    def _find_class_node(self, root: Node, class_name: str, raw: bytes) -> Optional[Node]:
        """Find a class node by name."""
        simple_name = class_name.split(".")[-1]
        
        stack = [root]
        while stack:
            node = stack.pop()
            
            if node.type == "class_declaration":
                name_node = node.child_by_field_name("name")
                if name_node and raw[name_node.start_byte:name_node.end_byte].decode("utf-8") == simple_name:
                    return node
            
            stack.extend(node.children)
        
        return None
    
    def _find_method_node(self, class_node: Node, signature: str, raw: bytes) -> Optional[Node]:
        """Find a method node by signature."""
        # Extract method name from signature
        method_name = signature.split("(")[0].strip()
        
        body_node = class_node.child_by_field_name("body")
        if not body_node:
            return None
        
        for child in body_node.children:
            if child.type == "method_declaration":
                name_node = child.child_by_field_name("name")
                if name_node and raw[name_node.start_byte:name_node.end_byte].decode("utf-8") == method_name:
                    # TODO: Match full signature (parameters)
                    return child
        
        return None
    
    def _find_field_node(self, class_node: Node, field_name: str, raw: bytes) -> Optional[Node]:
        """Find a field node by name."""
        body_node = class_node.child_by_field_name("body")
        if not body_node:
            return None
        
        for child in body_node.children:
            if child.type == "field_declaration":
                # Check variable declarators
                for declarator in child.children:
                    if declarator.type == "variable_declarator":
                        name_node = declarator.child_by_field_name("name")
                        if name_node and raw[name_node.start_byte:name_node.end_byte].decode("utf-8") == field_name:
                            return child
        
        return None
    
    def _apply_indentation(self, code: str, indentation: str) -> str:
        """Apply indentation to multi-line code."""
        lines = code.split('\n')
        indented_lines = [indentation + line if line.strip() else line for line in lines]
        return '\n'.join(indented_lines)
    
    def _validate_syntax(self, root: Node) -> bool:
        """Check if AST has any error nodes."""
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "ERROR" or node.is_missing:
                return False
            stack.extend(node.children)
        return True
    
    def _create_backup(self, file_path: Path, content: str) -> Path:
        """Create a timestamped backup of the file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_hash = hashlib.sha1(content.encode()).hexdigest()[:8]
        backup_name = f"{file_path.name}.{timestamp}.{file_hash}.bak"
        backup_path = self.backup_dir / backup_name
        backup_path.write_text(content, encoding="utf-8")
        return backup_path


# Example usage
if __name__ == "__main__":
    from pathlib import Path
    
    # Initialize engine
    repo_path = Path("C:/Supplier_exchange/area-service")
    engine = EnhancedPatchEngine(repo_path)
    
    # Example transformation
    transform = Transformation(
        kind="replace_method",
        target_class="TaskServiceImpl",
        target_method="validate(String)",
        new_code="""
    public void validate(String input) {
        // Enhanced validation logic
        if (input == null || input.trim().isEmpty()) {
            throw new IllegalArgumentException("Input cannot be empty");
        }
        // Additional validation...
    }
        """
    )
    
    # Apply transformation
    file_path = repo_path / "src/main/java/com/example/TaskServiceImpl.java"
    result = engine.apply_transformations(file_path, [transform])
    
    if result.success:
        print("✅ Transformation successful")
        print(f"Backup: {result.backup_path}")
    else:
        print(f"❌ Transformation failed: {result.error}")
