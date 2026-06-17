"""
Hybrid Localizer - Combines all localization signals with proper weights
Keyword: 60%, Symbol: 30%, Graph: 10%
"""
from typing import List
from pathlib import Path
from collections import defaultdict
from .keyword_search import KeywordLocalizer, FileCandidate
from .symbol_search import SymbolLocalizer


class GraphLocalizer:
    """
    Graph-based localization using call graph and dependency edges.
    
    Finds architecturally connected files:
    - Controllers that call services
    - Services that use repositories
    - Classes that implement interfaces
    """
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.index_dir = self.repo_path / ".aviator"
        
    def localize(self, initial_candidates: List[FileCandidate], ticket_description: str) -> List[FileCandidate]:
        """
        Find files connected to initial candidates via call graph.
        
        Args:
            initial_candidates: Files from keyword/symbol search
            ticket_description: Original ticket text
            
        Returns:
            Additional files found via graph traversal
        """
        if not initial_candidates:
            return []
        
        # Import SQLite here to avoid circular dependency
        import sqlite3
        db_path = self.index_dir / "index.db"
        if not db_path.exists():
            return []
        
        conn = sqlite3.connect(db_path)
        graph_candidates = []
        
        try:
            # Get symbol IDs from initial candidate files
            initial_paths = [c.path for c in initial_candidates[:5]]  # Top 5 only
            placeholders = ','.join('?' * len(initial_paths))
            
            # Find symbols in these files
            query = f"""
                SELECT id, qualified_name, kind, path 
                FROM symbols 
                WHERE path IN ({placeholders})
                AND kind IN ('class', 'interface', 'method')
            """
            cursor = conn.execute(query, initial_paths)
            initial_symbols = cursor.fetchall()
            
            if not initial_symbols:
                return []
            
            symbol_ids = [s[0] for s in initial_symbols]
            placeholders = ','.join('?' * len(symbol_ids))
            
            # Traverse graph: find files that are called by or call initial symbols
            graph_query = f"""
                SELECT DISTINCT s.path, s.qualified_name, e.kind
                FROM edges e
                JOIN symbols s ON (s.id = e.dst_id OR s.id = e.src_id)
                WHERE (e.src_id IN ({placeholders}) OR e.dst_id IN ({placeholders}))
                AND e.kind IN ('calls', 'extends', 'implements', 'references')
                AND s.path NOT IN ({','.join('?' * len(initial_paths))})
                LIMIT 20
            """
            
            cursor = conn.execute(graph_query, symbol_ids + symbol_ids + initial_paths)
            graph_results = cursor.fetchall()
            
            # Score files based on edge types
            edge_weights = {
                'calls': 0.7,      # Direct call relationship
                'implements': 0.5,  # Interface implementation
                'extends': 0.5,     # Inheritance
                'references': 0.3   # Type reference
            }
            
            path_scores = defaultdict(lambda: {'score': 0.0, 'reasons': []})
            for path, qname, edge_kind in graph_results:
                weight = edge_weights.get(edge_kind, 0.3)
                path_scores[path]['score'] += weight
                path_scores[path]['reasons'].append(f"{edge_kind}: {qname}")
            
            # Create candidates
            for path, data in path_scores.items():
                score = min(1.0, data['score'])  # Cap at 1.0
                reason = " | ".join(data['reasons'][:2])
                graph_candidates.append(FileCandidate(
                    path=path,
                    score=round(score, 2),
                    reason=f"Graph: {reason}",
                    method_name=None,
                    start_line=None,
                    end_line=None
                ))
            
        except Exception as e:
            print(f"Warning: Graph traversal failed: {e}")
        finally:
            conn.close()
        
        return graph_candidates


class HybridLocalizer:
    """
    Combines multiple localization strategies with weighted ranking.
    
    Weights:
    - Keyword search (FTS5): 60%
    - Symbol search (exact match): 30%
    - Graph traversal: 10%
    """
    
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.keyword_localizer = KeywordLocalizer(repo_path)
        self.symbol_localizer = SymbolLocalizer(repo_path)
        self.graph_localizer = GraphLocalizer(repo_path)
    
    def localize(self, ticket_description: str, top_n: int = 10) -> List[FileCandidate]:
        """
        Localize files using hybrid approach.
        
        Args:
            ticket_description: The ticket/requirement text
            top_n: Number of top candidates to return
            
        Returns:
            List of FileCandidate objects ranked by combined score
        """
        # Get results from each localizer
        keyword_results = self.keyword_localizer.localize(ticket_description)
        symbol_results = self.symbol_localizer.localize(ticket_description)
        
        # Get graph-based results (using top keyword/symbol candidates as seeds)
        initial_candidates = keyword_results[:5] if keyword_results else symbol_results[:5]
        graph_results = self.graph_localizer.localize(initial_candidates, ticket_description)
        
        # Combine and re-rank
        combined = self._combine_results(keyword_results, symbol_results, graph_results)
        
        # Sort by combined score
        combined.sort(key=lambda c: c.score, reverse=True)
        
        return combined[:top_n]
    
    def _combine_results(self, keyword_results: List[FileCandidate], 
                        symbol_results: List[FileCandidate],
                        graph_results: List[FileCandidate]) -> List[FileCandidate]:
        """
        Combine results with weighted scoring.
        
        Weights: Keyword 60%, Symbol 30%, Graph 10%
        """
        from collections import defaultdict
        
        # Index results by file path
        scores_by_path = defaultdict(lambda: {'keyword': 0.0, 'symbol': 0.0, 'graph': 0.0, 'reasons': []})
        
        # Add keyword scores (60% weight)
        for candidate in keyword_results:
            scores_by_path[candidate.path]['keyword'] = candidate.score * 0.6
            scores_by_path[candidate.path]['reasons'].append(f"Keyword: {candidate.reason}")
            if not scores_by_path[candidate.path].get('method'):
                scores_by_path[candidate.path]['method'] = {
                    'name': candidate.method_name,
                    'start': candidate.start_line,
                    'end': candidate.end_line
                }
        
        # Add symbol scores (30% weight)
        for candidate in symbol_results:
            scores_by_path[candidate.path]['symbol'] = candidate.score * 0.3
            scores_by_path[candidate.path]['reasons'].append(f"Symbol: {candidate.reason}")
            if not scores_by_path[candidate.path].get('method'):
                scores_by_path[candidate.path]['method'] = {
                    'name': candidate.method_name,
                    'start': candidate.start_line,
                    'end': candidate.end_line
                }
        
        # Add graph scores (10% weight)
        for candidate in graph_results:
            scores_by_path[candidate.path]['graph'] = candidate.score * 0.1
            scores_by_path[candidate.path]['reasons'].append(f"Graph: {candidate.reason}")
        
        # Create combined candidates
        combined = []
        for path, data in scores_by_path.items():
            combined_score = data['keyword'] + data['symbol'] + data['graph']
            reason = " | ".join(data['reasons'][:3])  # Top 3 reasons
            
            method_info = data.get('method', {})
            
            candidate = FileCandidate(
                path=path,
                score=round(combined_score, 2),
                reason=reason,
                method_name=method_info.get('name'),
                start_line=method_info.get('start'),
                end_line=method_info.get('end')
            )
            combined.append(candidate)
        
        return combined

# CLI test
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("Usage: python hybrid_localizer.py <repo_path> <ticket>")
        sys.exit(1)
    
    repo_path = sys.argv[1]
    ticket = " ".join(sys.argv[2:])
    
    localizer = HybridLocalizer(repo_path)
    candidates = localizer.localize(ticket)
    
    print(f"\n🎯 Hybrid Localization Results for: {ticket}\n")
    for i, candidate in enumerate(candidates, 1):
        print(f"{i}. {candidate.path}")
        print(f"   Combined Score: {candidate.score}")
        print(f"   Reason: {candidate.reason}")
        if candidate.method_name:
            print(f"   Target Method: {candidate.method_name}() at line {candidate.start_line}")
        print()
