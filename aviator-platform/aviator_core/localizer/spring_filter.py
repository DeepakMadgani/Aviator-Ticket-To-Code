"""Spring-aware filtering for localization results.

This module prioritizes files based on Spring Framework semantics:
- Spring stereotypes (@Service > @Controller > @Repository > @Component)
- REST endpoints matching ticket keywords
- Feign clients for microservice communication
- Spring layer architecture (Controller → Service → Repository)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from aviator_core.models import Symbol


@dataclass
class SpringContext:
    """Spring-specific metadata extracted from a ticket or query."""
    
    is_api_ticket: bool = False  # Mentions API, endpoint, REST
    is_service_ticket: bool = False  # Mentions business logic, service
    is_data_ticket: bool = False  # Mentions database, entity, repository
    mentioned_endpoints: list[str] = None  # e.g., ["/api/users", "POST /transmittals"]
    mentioned_services: list[str] = None  # e.g., ["UserService", "TransmittalService"]
    
    def __post_init__(self):
        if self.mentioned_endpoints is None:
            self.mentioned_endpoints = []
        if self.mentioned_services is None:
            self.mentioned_services = []


class SpringFilter:
    """Apply Spring-aware filtering and ranking to localization candidates."""
    
    # Stereotype priority weights (higher = more important)
    STEREOTYPE_WEIGHTS = {
        "RestController": 1.0,
        "Controller": 0.9,
        "Service": 0.8,
        "Repository": 0.7,
        "Component": 0.6,
        "Configuration": 0.3,
    }
    
    def __init__(self, store):
        """Initialize with a storage backend to query Spring metadata.
        
        Args:
            store: SqliteStore or Neo4jStore instance
        """
        self.store = store
    
    def detect_spring_context(self, ticket_text: str) -> SpringContext:
        """Analyze ticket to determine Spring-specific context.
        
        Args:
            ticket_text: The ticket description or query
            
        Returns:
            SpringContext with detected Spring-specific metadata
        """
        text_lower = ticket_text.lower()
        context = SpringContext()
        
        # Detect ticket type
        api_keywords = ["api", "endpoint", "rest", "controller", "http", "request", "response"]
        service_keywords = ["service", "business logic", "validation", "process"]
        data_keywords = ["database", "repository", "entity", "jpa", "query", "save", "delete"]
        
        context.is_api_ticket = any(kw in text_lower for kw in api_keywords)
        context.is_service_ticket = any(kw in text_lower for kw in service_keywords)
        context.is_data_ticket = any(kw in text_lower for kw in data_keywords)
        
        # Extract potential service names (CamelCase words ending with Service, Controller, etc.)
        import re
        service_pattern = r'\b([A-Z][a-zA-Z]+(?:Service|Controller|Repository))\b'
        context.mentioned_services = re.findall(service_pattern, ticket_text)
        
        # Extract potential endpoints (/api/*, POST /*, GET /*)
        endpoint_pattern = r'(?:GET|POST|PUT|DELETE|PATCH)?\s*(/[a-zA-Z0-9/_-]+)'
        context.mentioned_endpoints = re.findall(endpoint_pattern, ticket_text)
        
        return context
    
    def apply_spring_boost(
        self,
        candidates: list[dict],
        spring_context: SpringContext,
        boost_weight: float = 0.2
    ) -> list[dict]:
        """Apply Spring-aware boosting to localization candidates.
        
        Args:
            candidates: List of file candidates with 'path' and 'score'
            spring_context: Detected Spring context from ticket
            boost_weight: Maximum boost to apply (0.0 to 1.0)
            
        Returns:
            Candidates with updated scores based on Spring intelligence
        """
        # Fetch Spring metadata for all candidate files
        file_paths = [c['path'] for c in candidates]
        spring_data = self._fetch_spring_metadata(file_paths)
        
        for candidate in candidates:
            path = candidate['path']
            metadata = spring_data.get(path)
            if not metadata:
                continue
            
            boost = 0.0
            
            # Boost based on stereotype match
            stereotype = metadata.get('spring_stereotype')
            if stereotype:
                boost += self._get_stereotype_boost(stereotype, spring_context)
            
            # Boost for endpoint matches
            endpoints = metadata.get('spring_endpoints', [])
            if endpoints and spring_context.mentioned_endpoints:
                boost += self._get_endpoint_match_boost(endpoints, spring_context.mentioned_endpoints)
            
            # Boost for Feign clients if microservice communication mentioned
            if metadata.get('is_feign_client') and 'microservice' in spring_context:
                boost += 0.1
            
            # Apply boost (scaled by boost_weight)
            candidate['score'] = min(1.0, candidate['score'] + (boost * boost_weight))
            candidate['spring_boost'] = boost * boost_weight
            candidate['spring_stereotype'] = stereotype
        
        # Re-sort by updated scores
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates
    
    def filter_by_spring_layer(
        self,
        candidates: list[dict],
        layer: str
    ) -> list[dict]:
        """Filter candidates to only include specific Spring layer.
        
        Args:
            candidates: List of file candidates
            layer: 'controller', 'service', or 'repository'
            
        Returns:
            Filtered candidates
        """
        layer_map = {
            'controller': ['RestController', 'Controller'],
            'service': ['Service'],
            'repository': ['Repository']
        }
        
        valid_stereotypes = layer_map.get(layer.lower(), [])
        if not valid_stereotypes:
            return candidates
        
        file_paths = [c['path'] for c in candidates]
        spring_data = self._fetch_spring_metadata(file_paths)
        
        filtered = []
        for candidate in candidates:
            path = candidate['path']
            metadata = spring_data.get(path)
            if metadata and metadata.get('spring_stereotype') in valid_stereotypes:
                filtered.append(candidate)
        
        return filtered
    
    def _fetch_spring_metadata(self, file_paths: list[str]) -> dict:
        """Fetch Spring metadata for given file paths from storage.
        
        Args:
            file_paths: List of repo-relative file paths
            
        Returns:
            Dict mapping path -> Spring metadata
        """
        # Query symbols table for Spring metadata
        placeholders = ','.join('?' * len(file_paths))
        query = f"""
            SELECT DISTINCT 
                path,
                spring_stereotype,
                spring_endpoints,
                spring_dependencies,
                is_feign_client,
                feign_service_name
            FROM symbols
            WHERE path IN ({placeholders})
            AND (spring_stereotype IS NOT NULL OR is_feign_client = 1)
        """
        
        result = {}
        try:
            cursor = self.store._conn.execute(query, file_paths)
            for row in cursor.fetchall():
                path = row[0]
                result[path] = {
                    'spring_stereotype': row[1],
                    'spring_endpoints': row[2],
                    'spring_dependencies': row[3],
                    'is_feign_client': bool(row[4]),
                    'feign_service_name': row[5],
                }
        except Exception as e:
            # Graceful degradation if Spring columns don't exist yet
            print(f"Warning: Could not fetch Spring metadata: {e}")
        
        return result
    
    def _get_stereotype_boost(self, stereotype: str, context: SpringContext) -> float:
        """Calculate boost based on stereotype matching ticket context.
        
        Args:
            stereotype: Spring stereotype annotation
            context: Detected Spring context
            
        Returns:
            Boost value (0.0 to 1.0)
        """
        base_weight = self.STEREOTYPE_WEIGHTS.get(stereotype, 0.5)
        
        # Additional boost for context match
        if context.is_api_ticket and stereotype in ['RestController', 'Controller']:
            return base_weight + 0.2
        elif context.is_service_ticket and stereotype == 'Service':
            return base_weight + 0.2
        elif context.is_data_ticket and stereotype == 'Repository':
            return base_weight + 0.2
        
        return base_weight * 0.5  # Partial boost for non-matching context
    
    def _get_endpoint_match_boost(
        self,
        file_endpoints: list[str],
        mentioned_endpoints: list[str]
    ) -> float:
        """Calculate boost for endpoint matches.
        
        Args:
            file_endpoints: Endpoints in the file (e.g., ["GET:/api/users"])
            mentioned_endpoints: Endpoints mentioned in ticket
            
        Returns:
            Boost value (0.0 to 0.3)
        """
        if not file_endpoints or not mentioned_endpoints:
            return 0.0
        
        # Simple substring matching
        for mentioned in mentioned_endpoints:
            for file_endpoint in file_endpoints:
                if mentioned.strip('/') in file_endpoint:
                    return 0.3
        
        return 0.0
