"""
Symbol-based Localization - Direct entity matching (30% weight)
Searches for exact symbol names mentioned in ticket
"""
from typing import List
from pathlib import Path
from ..storage.sqlite_store import SqliteStore
from .keyword_search import FileCandidate

class SymbolLocalizer:
    """
    Symbol-based file localization using direct SQL queries.
    This is the SECONDARY localization method (30% weight).
    Finds exact matches for class names, method names, etc.
    """
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.db_path = self.repo_path / ".aviator" / "index.db"
        self.store = SqliteStore(str(self.db_path))
    
    def localize(self, ticket_description: str) -> List[FileCandidate]:
        """
        Localize files by finding exact symbol matches.
        
        Args:
            ticket_description: The ticket description
            
        Returns:
            List of FileCandidate objects
        """
        # Extract potential symbol names (CamelCase, entities)
        symbols = self._extract_symbols(ticket_description)
        
        if not symbols:
            return []
        
        # Search for exact matches
        results = []
        for symbol_name in symbols:
            matches = self._find_symbol(symbol_name)
            results.extend(matches)
        
        # Group by file and rank
        candidates = self._group_and_rank(results, symbols)
        
        return candidates
    
    def _extract_symbols(self, text: str) -> List[str]:
        """
        Extract potential symbol names from ticket.
        Look for:
        - CamelCase words (UserService, ProjectDTO)
        - Capitalized words that might be class names
        - Common patterns like "Service", "Controller", "Repository"
        """
        import re
        
        symbols = []
        
        # Extract CamelCase words
        camel_case = re.findall(r'\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b', text)
        symbols.extend(camel_case)
        
        # Extract single capitalized words that might be classes
        capitalized = re.findall(r'\b[A-Z][a-z]{2,}\b', text)
        symbols.extend(capitalized)
        
        # Extract quoted identifiers
        quoted = re.findall(r'[\'"`]([A-Za-z_][A-Za-z0-9_]*)[\'"`]', text)
        symbols.extend(quoted)
        
        return list(set(symbols))  # Remove duplicates
    
    def _find_symbol(self, symbol_name: str) -> List:
        """Find symbols matching the name."""
        import sqlite3
        
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Search for exact or partial matches
        query = """
            SELECT *
            FROM symbols
            WHERE name LIKE ?
               OR qualified_name LIKE ?
            ORDER BY 
                CASE 
                    WHEN name = ? THEN 1
                    WHEN name LIKE ? THEN 2
                    ELSE 3
                END
            LIMIT 50
        """
        
        pattern = f"%{symbol_name}%"
        exact_pattern = f"{symbol_name}%"
        
        results = cursor.execute(query, (pattern, pattern, symbol_name, exact_pattern)).fetchall()
        conn.close()
        
        return results
    
    def _group_and_rank(self, symbols: List, search_terms: List[str]) -> List[FileCandidate]:
        """Group symbols by file and rank."""
        from collections import defaultdict
        
        by_file = defaultdict(list)
        for symbol in symbols:
            try:
                path = symbol['path']
            except (TypeError, KeyError):
                continue
            by_file[path].append(symbol)
        
        candidates = []
        for file_path, file_symbols in by_file.items():
            score = self._calculate_score(file_symbols, search_terms)
            reason = self._generate_reason(file_symbols, search_terms)
            method = self._get_best_method(file_symbols, search_terms)
            
            candidate = FileCandidate(
                path=file_path,
                score=score,
                reason=reason,
                method_name=method.get('name'),
                start_line=method.get('start_line'),
                end_line=method.get('end_line')
            )
            candidates.append(candidate)
        
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:10]
    
    def _calculate_score(self, symbols: List, search_terms: List[str]) -> float:
        """Calculate relevance score based on exact matches."""
        score = 0.0
        
        for symbol in symbols:
            try:
                name = symbol['name']
                kind = symbol['kind']
            except (TypeError, KeyError):
                continue
            
            # Exact match bonus
            if any(term == name for term in search_terms):
                score += 0.4
            
            # Partial match
            elif any(term in name for term in search_terms):
                score += 0.2
            
            # Class/interface bonus
            if kind in ('CLASS', 'INTERFACE', 'ENUM'):
                score += 0.15
            
            # Method bonus
            elif kind == 'METHOD':
                score += 0.1
        
        return min(round(score, 2), 1.0)
    
    def _generate_reason(self, symbols: List, search_terms: List[str]) -> str:
        """Generate human-readable reason."""
        matched = []
        for symbol in symbols[:3]:
            try:
                name = symbol['name']
                if any(term in name for term in search_terms):
                    matched.append(name)
            except (TypeError, KeyError):
                continue
        
        if matched:
            return f"Exact symbol matches: {', '.join(matched)}"
        return f"Contains {len(symbols)} related symbols"
    
    def _get_best_method(self, symbols: List, search_terms: List[str]) -> dict:
        """Find most relevant method."""
        methods = [s for s in symbols if s['kind'] == 'METHOD']
        
        if not methods:
            return {}
        
        # Prefer methods matching search terms
        for method in methods:
            if any(term in method['name'] for term in search_terms):
                return {
                    'name': method['name'],
                    'start_line': method['start_line'],
                    'end_line': method['end_line']
                }
        
        # Return first method
        return {
            'name': methods[0]['name'],
            'start_line': methods[0]['start_line'],
            'end_line': methods[0]['end_line']
        }
