"""
Keyword-based Localization - Primary retrieval method (60% weight)
Uses SQLite FTS5 for fast full-text search
"""
from typing import List
from pathlib import Path
from ..storage.sqlite_store import SqliteStore
from ..models import Symbol

class FileCandidate:
    """Represents a candidate file for modification"""
    def __init__(self, path: str, score: float, reason: str, 
                 method_name: str = None, start_line: int = None, end_line: int = None):
        self.path = path
        self.score = score
        self.reason = reason
        self.method_name = method_name
        self.start_line = start_line
        self.end_line = end_line
        self.selected = score > 0.7  # Auto-select high confidence

class KeywordLocalizer:
    """
    Keyword-based file localization using SQLite FTS5.
    This is the PRIMARY localization method (60% weight).
    """
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.db_path = self.repo_path / ".aviator" / "index.db"
        self.store = SqliteStore(str(self.db_path))
    
    def localize(self, ticket_description: str) -> List[FileCandidate]:
        """
        Localize files relevant to the ticket using keyword search.
        
        Args:
            ticket_description: The ticket description/requirement
            
        Returns:
            List of FileCandidate objects ranked by relevance
        """
        # Extract keywords from ticket
        keywords = self._extract_keywords(ticket_description)
        
        if not keywords:
            return []
        
        # Search using FTS5
        results = []
        for keyword in keywords:
            symbols = self.store.search_symbols(keyword, limit=20)
            results.extend(symbols)
        
        # Group by file and calculate scores
        candidates = self._group_and_rank(results, keywords, ticket_description)
        
        return candidates
    
    def _extract_keywords(self, text: str) -> List[str]:
        """
        Extract meaningful keywords from ticket description.
        
        Simple implementation:
        - Split on whitespace
        - Remove common words
        - Extract camelCase/snake_case terms
        """
        import re
        
        # Remove common stop words
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'are', 'was', 'were', 'be',
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should',
            'could', 'may', 'might', 'must', 'can', 'this', 'that', 'these', 'those'
        }
        
        # Extract words
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text.lower())
        
        # Filter stop words and short words
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        
        # Add camelCase splits (e.g., "UserService" -> ["user", "service"])
        for word in words:
            if any(c.isupper() for c in word):
                # Split on uppercase
                parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)', word)
                keywords.extend([p.lower() for p in parts if len(p) > 2])
        
        return list(set(keywords))  # Remove duplicates
    
    def _group_and_rank(self, symbols: List[Symbol], keywords: List[str], 
                        ticket: str) -> List[FileCandidate]:
        """
        Group symbols by file and rank by relevance.
        """
        from collections import defaultdict
        
        # Group by file
        by_file = defaultdict(list)
        for symbol in symbols:
            # Handle both Symbol objects and sqlite3.Row
            try:
                path = symbol['path']  # sqlite3.Row
            except (TypeError, KeyError):
                path = symbol.location.path  # Symbol object
            by_file[path].append(symbol)
        
        candidates = []
        
        for file_path, file_symbols in by_file.items():
            # Calculate score
            score = self._calculate_score(file_symbols, keywords, ticket)
            
            # Generate reason
            reason = self._generate_reason(file_symbols, keywords)
            
            # Get best method candidate
            method_candidate = self._get_best_method(file_symbols, keywords)
            
            candidate = FileCandidate(
                path=file_path,
                score=score,
                reason=reason,
                method_name=method_candidate.get('name'),
                start_line=method_candidate.get('start_line'),
                end_line=method_candidate.get('end_line')
            )
            
            candidates.append(candidate)
        
        # Sort by score descending
        candidates.sort(key=lambda c: c.score, reverse=True)
        
        return candidates[:10]  # Top 10
    
    def _calculate_score(self, symbols: List, keywords: List[str], 
                        ticket: str) -> float:
        """
        Calculate relevance score for file.
        
        Scoring factors:
        - Number of keyword matches
        - Type of symbols (methods score higher)
        - Name similarity to keywords
        """
        score = 0.0
        
        # Base score from number of matches
        score += len(symbols) * 0.1
        
        # Boost for method matches
        method_count = sum(1 for s in symbols if self._get_kind(s) == 'METHOD')
        score += method_count * 0.2
        
        # Boost for exact keyword matches in symbol names
        for symbol in symbols:
            name = self._get_name(symbol)
            for keyword in keywords:
                if keyword.lower() in name.lower():
                    score += 0.15
        
        # Normalize to 0-1 range
        score = min(score, 1.0)
        
        return round(score, 2)
    
    def _get_name(self, symbol) -> str:
        """Get symbol name from either Symbol object or sqlite3.Row."""
        try:
            return symbol['name']  # sqlite3.Row
        except (TypeError, KeyError):
            return symbol.name  # Symbol object
    
    def _get_kind(self, symbol) -> str:
        """Get symbol kind from either Symbol object or sqlite3.Row."""
        try:
            return symbol['kind']  # sqlite3.Row
        except (TypeError, KeyError):
            return symbol.kind.name if hasattr(symbol.kind, 'name') else str(symbol.kind)
    
    def _generate_reason(self, symbols: List, keywords: List[str]) -> str:
        """Generate human-readable reason for file selection."""
        matched_symbols = []
        for symbol in symbols[:3]:  # Top 3
            name = self._get_name(symbol)
            if any(kw.lower() in name.lower() for kw in keywords):
                matched_symbols.append(name)
        
        if matched_symbols:
            return f"Contains relevant symbols: {', '.join(matched_symbols)}"
        else:
            return f"Contains {len(symbols)} keyword matches"
    
    def _get_best_method(self, symbols: List, keywords: List[str]) -> dict:
        """Find the most relevant method in file."""
        methods = [s for s in symbols if self._get_kind(s) == 'METHOD']
        
        if not methods:
            return {}
        
        # Score methods by keyword relevance
        best_method = max(methods, key=lambda m: sum(
            1 for kw in keywords if kw.lower() in self._get_name(m).lower()
        ))
        
        # Get line number
        try:
            line = best_method['line']  # sqlite3.Row
        except (TypeError, KeyError):
            line = best_method.location.line  # Symbol object
        
        return {
            'name': self._get_name(best_method),
            'start_line': line,
            'end_line': line + 20  # Estimate
        }

# Quick test
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python keyword_search.py <repo_path> <ticket>")
        sys.exit(1)
    
    repo_path = sys.argv[1]
    ticket = " ".join(sys.argv[2:])
    
    localizer = KeywordLocalizer(repo_path)
    candidates = localizer.localize(ticket)
    
    print(f"\n🔍 Found {len(candidates)} candidates for: {ticket}\n")
    for i, candidate in enumerate(candidates, 1):
        print(f"{i}. {candidate.path}")
        print(f"   Score: {candidate.score}")
        print(f"   Reason: {candidate.reason}")
        if candidate.method_name:
            print(f"   Method: {candidate.method_name}() at line {candidate.start_line}")
        print()
