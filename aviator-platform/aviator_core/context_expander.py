"""
Context expansion - Loads actual code and dependencies for LLM prompt
"""
from pathlib import Path
from typing import Dict, List, Any

class ContextExpander:
    """
    Expands localized files into full context for LLM patch generation.
    Loads target code, dependencies, and relevant documentation.
    """
    
    def __init__(self, repo_path: str, knowledge_base_path: str = None):
        self.repo_path = Path(repo_path)
        self.knowledge_base_path = Path(knowledge_base_path) if knowledge_base_path else None
    
    def expand_context(self, 
                      selected_files: List[str],
                      method_names: List[str] = None,
                      operation_type: str = "CODE_MODIFICATION") -> Dict[str, Any]:
        """
        Expand context for selected files.
        
        Args:
            selected_files: List of file paths to modify
            method_names: Optional list of method names to focus on
            operation_type: Type of operation being performed
            
        Returns:
            Dictionary with target_code, dependencies, rules, etc.
        """
        context = {
            "target_files": [],
            "dependencies": [],
            "architecture_rules": "",
            "coding_standards": "",
            "business_rules": ""
        }
        
        # Load target file contents
        for file_path in selected_files:
            full_path = self.repo_path / file_path if not Path(file_path).is_absolute() else Path(file_path)
            
            if full_path.exists():
                content = full_path.read_text(encoding='utf-8', errors='ignore')
                
                # If method names provided, extract just those methods
                if method_names:
                    method_code = self._extract_methods(content, method_names)
                    context["target_files"].append({
                        "path": file_path,
                        "content": method_code if method_code else content[:5000],  # Limit to 5000 chars
                        "full_file": False if method_code else True
                    })
                else:
                    # Limit full file to avoid token explosion
                    context["target_files"].append({
                        "path": file_path,
                        "content": content[:10000],  # First 10K chars
                        "full_file": True
                    })
        
        # Load dependencies (imports, called classes)
        context["dependencies"] = self._load_dependencies(selected_files)
        
        # Load RAG documentation if available
        if self.knowledge_base_path and self.knowledge_base_path.exists():
            context["architecture_rules"] = self._load_rag_doc("architecture")
            context["coding_standards"] = self._load_rag_doc("guidelines")
            context["business_rules"] = self._load_rag_doc("business_rules")
        
        return context
    
    def _extract_methods(self, file_content: str, method_names: List[str]) -> str:
        """
        Extract specific methods from file content.
        Simple regex-based extraction (can be enhanced with tree-sitter).
        """
        import re
        
        methods = []
        for method_name in method_names:
            # Pattern: method signature followed by body
            pattern = rf'((?:public|private|protected)\s+[\w<>\[\],\s]+\s+{method_name}\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\s*\{{[^{{}}]*(?:\{{[^{{}}]*\}}[^{{}}]*)*\}})'
            matches = re.findall(pattern, file_content, re.MULTILINE | re.DOTALL)
            methods.extend(matches)
        
        return "\n\n".join(methods) if methods else ""
    
    def _load_dependencies(self, selected_files: List[str]) -> List[str]:
        """
        Load dependencies for selected files.
        Simplified: loads imported classes mentioned in the files.
        """
        dependencies = []
        
        for file_path in selected_files[:3]:  # Limit to avoid token explosion
            full_path = self.repo_path / file_path if not Path(file_path).is_absolute() else Path(file_path)
            
            if full_path.exists():
                content = full_path.read_text(encoding='utf-8', errors='ignore')
                
                # Extract imports
                import re
                imports = re.findall(r'import\s+([\w.]+);', content)
                
                # Try to load imported classes (if in same repo)
                for imp in imports[:5]:  # Limit to 5 dependencies
                    dep_path = self._resolve_import(imp)
                    if dep_path and dep_path.exists():
                        dep_content = dep_path.read_text(encoding='utf-8', errors='ignore')
                        dependencies.append(dep_content[:2000])  # First 2K chars
        
        return dependencies
    
    def _resolve_import(self, import_path: str) -> Path:
        """Resolve Java import to file path."""
        # Convert com.example.MyClass to src/main/java/com/example/MyClass.java
        parts = import_path.split('.')
        class_name = parts[-1]
        package_path = '/'.join(parts[:-1])
        
        # Try common Java paths
        for base in ['src/main/java', 'src/test/java']:
            potential_path = self.repo_path / base / package_path / f"{class_name}.java"
            if potential_path.exists():
                return potential_path
        
        return None
    
    def _load_rag_doc(self, category: str) -> str:
        """Load RAG documentation by category."""
        if not self.knowledge_base_path:
            return ""
        
        # Try to find relevant doc
        doc_map = {
            "architecture": "architecture/SYSTEM_ARCHITECTURE.md",
            "guidelines": "workflows/SERVICE_PATTERNS.md",
            "business_rules": "business_rules/VALIDATION_RULES.md"
        }
        
        doc_path = self.knowledge_base_path / doc_map.get(category, "")
        
        if doc_path.exists():
            content = doc_path.read_text(encoding='utf-8', errors='ignore')
            return content[:3000]  # First 3K chars
        
        return ""
