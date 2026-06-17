"""
Patch applicator - Applies generated code patches to files safely
"""
from pathlib import Path
from typing import Optional
import shutil
import tempfile

class PatchApplicator:
    """
    Applies code patches to files with backup and validation.
    """
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.backup_dir = self.repo_path / ".aviator" / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
    
    def apply_patch(self, 
                   file_path: str, 
                   new_content: str,
                   method_name: Optional[str] = None,
                   start_line: Optional[int] = None,
                   end_line: Optional[int] = None) -> bool:
        """
        Apply patch to a file.
        
        Args:
            file_path: Path to file to modify
            new_content: New code to insert
            method_name: Optional method name to replace
            start_line: Optional start line for replacement
            end_line: Optional end line for replacement
            
        Returns:
            True if successful, False otherwise
        """
        full_path = self.repo_path / file_path if not Path(file_path).is_absolute() else Path(file_path)
        
        if not full_path.exists():
            print(f"⚠️  File not found: {full_path}")
            return False
        
        try:
            # Backup original file
            backup_path = self._backup_file(full_path)
            print(f"📁 Backed up to: {backup_path}")
            
            # Read original content
            original_content = full_path.read_text(encoding='utf-8')
            
            # Apply patch based on method
            if start_line and end_line:
                # Line-based replacement
                modified_content = self._replace_lines(original_content, new_content, start_line, end_line)
            elif method_name:
                # Method-based replacement (AST-aware)
                modified_content = self._replace_method(original_content, new_content, method_name)
            else:
                # Full file replacement
                modified_content = new_content
            
            # Validate syntax before writing
            if not self._validate_syntax(modified_content):
                print(f"❌ Syntax validation failed for {file_path}")
                return False
            
            # Write modified content
            full_path.write_text(modified_content, encoding='utf-8')
            print(f"✅ Successfully patched: {file_path}")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to apply patch: {e}")
            # Restore from backup
            if backup_path and backup_path.exists():
                shutil.copy(backup_path, full_path)
                print(f"🔄 Restored from backup")
            return False
    
    def _backup_file(self, file_path: Path) -> Path:
        """Create backup of file."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_path.name}.{timestamp}.bak"
        backup_path = self.backup_dir / backup_name
        
        shutil.copy(file_path, backup_path)
        return backup_path
    
    def _replace_lines(self, original: str, new_content: str, start_line: int, end_line: int) -> str:
        """Replace specific lines in file."""
        lines = original.split('\n')
        
        # Convert to 0-indexed
        start_idx = start_line - 1
        end_idx = end_line
        
        # Replace lines
        new_lines = new_content.split('\n')
        modified_lines = lines[:start_idx] + new_lines + lines[end_idx:]
        
        return '\n'.join(modified_lines)
    
    def _replace_method(self, original: str, new_content: str, method_name: str) -> str:
        """
        Replace entire method using simple regex.
        For production, should use tree-sitter for AST-safe replacement.
        """
        import re
        
        # Pattern to find method
        pattern = rf'((?:public|private|protected)\s+[\w<>\[\],\s]+\s+{method_name}\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*)\{{[^{{}}]*(?:\{{[^{{}}]*\}}[^{{}}]*)*\}}'
        
        # Try to replace method body
        modified = re.sub(pattern, new_content, original, count=1, flags=re.MULTILINE | re.DOTALL)
        
        if modified == original:
            # Pattern didn't match, append new method
            print(f"⚠️  Method {method_name} not found, appending new method")
            # Find last closing brace of class
            last_brace = original.rfind('}')
            if last_brace > 0:
                modified = original[:last_brace] + "\n\n" + new_content + "\n" + original[last_brace:]
            else:
                modified = original + "\n\n" + new_content
        
        return modified
    
    def _validate_syntax(self, content: str) -> bool:
        """
        Validate Java syntax.
        Uses tree-sitter if available, otherwise basic checks.
        """
        try:
            # Try tree-sitter validation
            from tree_sitter import Parser
            import tree_sitter_java as tsjava
            
            parser = Parser()
            parser.set_language(tsjava.language())
            
            tree = parser.parse(bytes(content, 'utf-8'))
            
            # Check for syntax errors
            has_error = tree.root_node.has_error
            
            if has_error:
                print("⚠️  Tree-sitter detected syntax errors")
            
            return not has_error
            
        except ImportError:
            # Fallback: basic syntax checks
            print("ℹ️  Tree-sitter not available, using basic validation")
            
            # Check for balanced braces
            open_braces = content.count('{')
            close_braces = content.count('}')
            
            if open_braces != close_braces:
                print(f"⚠️  Unbalanced braces: {open_braces} open, {close_braces} close")
                return False
            
            # Check for common syntax errors
            if content.count('(') != content.count(')'):
                print("⚠️  Unbalanced parentheses")
                return False
            
            return True
    
    def rollback(self, file_path: str) -> bool:
        """Rollback file to most recent backup."""
        full_path = self.repo_path / file_path if not Path(file_path).is_absolute() else Path(file_path)
        
        # Find most recent backup
        file_name = full_path.name
        backups = sorted(self.backup_dir.glob(f"{file_name}.*.bak"), reverse=True)
        
        if not backups:
            print(f"⚠️  No backup found for {file_path}")
            return False
        
        # Restore most recent backup
        latest_backup = backups[0]
        shutil.copy(latest_backup, full_path)
        print(f"✅ Restored {file_path} from {latest_backup.name}")
        
        return True
