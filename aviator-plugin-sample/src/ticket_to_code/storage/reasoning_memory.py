"""
ReasoningMemory - Store and retrieve ticket reasoning patterns from PostgreSQL

Learns from every ticket so the next similar ticket benefits from past experience.

Author: Deepak Madgani
Date: April 2026
"""

import logging
import json
import hashlib
from typing import List, Dict, Optional, Set
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ReasoningMemory:
    """
    Persistent memory that stores reasoning about each ticket.
    
    This allows the system to:
    1. Find similar past tickets by keywords/type
    2. Retrieve what fix strategies worked before
    3. Learn common patterns (e.g., "validateForm bugs → check null handling")
    4. Avoid repeating failed strategies
    
    TODO: Implement PostgreSQL backend for persistence
    For now: In-memory dictionary (sufficient for Phase 1 testing)
    """
    
    def __init__(self, enable_postgres: bool = False):
        """
        Initialize memory storage.
        
        Args:
            enable_postgres: If True, use PostgreSQL backend. If False, use in-memory.
        """
        self.enable_postgres = enable_postgres
        self.logger = logging.getLogger(__name__)
        
        # In-memory storage for Phase 1
        self._memory = {
            'tickets': {},        # ticket_id -> ticket_data
            'patterns': {},       # pattern_hash -> pattern_data
            'strategies': {}      # strategy_hash -> strategy_data
        }
        
        if enable_postgres:
            self._init_postgres()
        else:
            self.logger.info("[MEMORY] Using in-memory storage (Phase 1)")
    
    def _init_postgres(self):
        """
        TODO: Initialize PostgreSQL connection and create schema.
        
        Schema design:
        - tickets table: id, title, type, created_at, status
        - reasoning table: ticket_id, root_cause, strategy, confidence, success
        - patterns table: pattern_hash, pattern_type, count, files_involved
        - embeddings table: pattern_hash, vector, created_at (for semantic search)
        """
        try:
            import psycopg2
            # TODO: Connect to PostgreSQL
            # self.conn = psycopg2.connect("postgresql://user:pass@localhost:5433/aviator")
            self.logger.info("[MEMORY] PostgreSQL initialized")
        except ImportError:
            self.logger.warning("[MEMORY] psycopg2 not installed - falling back to in-memory")
            self.enable_postgres = False
    
    def find_similar_tickets(
        self,
        keywords: List[str],
        ticket_type: str,
        limit: int = 5
    ) -> List[Dict]:
        """
        Find similar previous tickets.
        
        Args:
            keywords: Keywords from current ticket
            ticket_type: Type of ticket (BUG, FEATURE, etc)
            limit: Max number of tickets to return
        
        Returns:
            List of similar tickets with their solutions
        """
        
        self.logger.debug(f"[MEMORY] Searching for similar tickets: type={ticket_type}, keywords={keywords}")
        
        if self.enable_postgres:
            return self._search_postgres(keywords, ticket_type, limit)
        else:
            return self._search_memory(keywords, ticket_type, limit)
    
    def _search_memory(self, keywords: Set[str], ticket_type: str, limit: int) -> List[Dict]:
        """Search in-memory storage"""
        
        similar = []
        
        for ticket_id, ticket_data in self._memory['tickets'].items():
            # Match by type
            if ticket_data.get('type') != ticket_type:
                continue
            
            # Match by keywords
            ticket_keywords = set(ticket_data.get('keywords', []))
            matches = len(keywords & ticket_keywords)
            
            if matches > 0:
                similar.append({
                    'ticket_id': ticket_id,
                    'title': ticket_data.get('title'),
                    'root_cause': ticket_data.get('root_cause'),
                    'strategy': ticket_data.get('strategy'),
                    'success': ticket_data.get('success'),
                    'confidence': ticket_data.get('confidence'),
                    'keyword_matches': matches
                })
        
        # Sort by keyword matches (highest first)
        similar.sort(key=lambda x: x['keyword_matches'], reverse=True)
        
        return similar[:limit]
    
    def _search_postgres(self, keywords: List[str], ticket_type: str, limit: int) -> List[Dict]:
        """
        TODO: Search PostgreSQL using pgvector similarity.
        
        For now: Return empty (Phase 1)
        """
        # TODO: Implement pgvector search
        return []
    
    def find_patterns(
        self,
        files_involved: List[str],
        methods_involved: List[str]
    ) -> Dict[str, any]:
        """
        Find common patterns from past tickets affecting these files/methods.
        
        Args:
            files_involved: List of files being modified
            methods_involved: List of methods being modified
        
        Returns:
            {
                'common_fixes': List[str],
                'common_issues': List[str],
                'success_rate': float
            }
        """
        
        self.logger.debug(f"[MEMORY] Searching for patterns in files: {files_involved}")
        
        patterns = {
            'common_fixes': [],
            'common_issues': [],
            'success_rate': 0.0
        }
        
        # Search memory for tickets affecting these files
        for ticket_id, ticket_data in self._memory['tickets'].items():
            ticket_files = set(ticket_data.get('files_modified', []))
            
            # If this ticket touched any of our files
            if any(f in ticket_files for f in files_involved):
                # Record the strategy
                if ticket_data.get('strategy'):
                    patterns['common_fixes'].append(ticket_data['strategy'])
                
                # Record the issue
                if ticket_data.get('root_cause'):
                    patterns['common_issues'].append(ticket_data['root_cause'])
        
        # Calculate success rate
        if patterns['common_fixes']:
            successful = sum(
                1 for strategy in patterns['common_fixes']
                if self._get_success_rate(strategy) > 0.7
            )
            patterns['success_rate'] = successful / len(patterns['common_fixes'])
        
        self.logger.info(f"[MEMORY] Found {len(patterns['common_fixes'])} patterns")
        
        return patterns
    
    def store_ticket(
        self,
        ticket_id: str,
        ticket_data: Dict
    ):
        """
        Store a ticket and its solution in memory.
        
        Args:
            ticket_id: Unique ticket ID
            ticket_data: {
                'title': str,
                'type': str,
                'keywords': List[str],
                'root_cause': str,
                'strategy': str,
                'files_modified': List[str],
                'methods_modified': List[str],
                'success': bool,
                'confidence': float,
                'timestamp': datetime
            }
        """
        
        self.logger.info(f"[MEMORY] Storing ticket: {ticket_id}")
        
        if self.enable_postgres:
            self._store_postgres(ticket_id, ticket_data)
        else:
            self._store_memory(ticket_id, ticket_data)
    
    def _store_memory(self, ticket_id: str, ticket_data: Dict):
        """Store in in-memory dictionary"""
        
        self._memory['tickets'][ticket_id] = {
            'title': ticket_data.get('title', ''),
            'type': ticket_data.get('type', ''),
            'keywords': ticket_data.get('keywords', []),
            'root_cause': ticket_data.get('root_cause', ''),
            'strategy': ticket_data.get('strategy', ''),
            'files_modified': ticket_data.get('files_modified', []),
            'methods_modified': ticket_data.get('methods_modified', []),
            'success': ticket_data.get('success', False),
            'confidence': ticket_data.get('confidence', 0.0),
            'timestamp': ticket_data.get('timestamp', datetime.now())
        }
        
        self.logger.debug(f"[MEMORY] Stored: {ticket_id} → success={ticket_data.get('success')}")
    
    def _store_postgres(self, ticket_id: str, ticket_data: Dict):
        """
        TODO: Store in PostgreSQL with embedding.
        
        For now: Fall back to memory
        """
        self._store_memory(ticket_id, ticket_data)
    
    def _get_success_rate(self, strategy: str) -> float:
        """Get success rate of a strategy"""
        
        matching_tickets = [
            t for t in self._memory['tickets'].values()
            if t.get('strategy') == strategy
        ]
        
        if not matching_tickets:
            return 0.0
        
        successful = sum(1 for t in matching_tickets if t.get('success'))
        return successful / len(matching_tickets)
    
    def get_memory_stats(self) -> Dict:
        """Get statistics about stored memory"""
        
        total_tickets = len(self._memory['tickets'])
        successful = sum(
            1 for t in self._memory['tickets'].values()
            if t.get('success')
        )
        
        unique_strategies = set(
            t.get('strategy') for t in self._memory['tickets'].values()
            if t.get('strategy')
        )
        
        return {
            'total_tickets': total_tickets,
            'successful': successful,
            'success_rate': successful / total_tickets if total_tickets > 0 else 0.0,
            'unique_strategies': len(unique_strategies),
            'memory_type': 'postgresql' if self.enable_postgres else 'in-memory'
        }
