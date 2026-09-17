import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from ticket_to_code.models import (
    PlanningScopeVerificationResult, 
    VerificationStatus, 
    VerificationEvidence
)

logger = logging.getLogger(__name__)


class PlanningScopeVerifier:
    """
    Verifies Preflight claims that could materially change implementation scope 
    (e.g., "create new backend endpoint").
    
    Prevents the 'absence from evidence == absence from repository' semantic error
    by explicitly searching the repository for claims not found in the Evidence Ledger.
    """
    
    def __init__(self, agents):
        self.agents = agents
        
    def verify_guidance(
        self, 
        preflight_guidance: List[dict], 
        evidence_items: List[Any], 
        discovered_files: List[dict]
    ) -> List[PlanningScopeVerificationResult]:
        """
        Processes preflight guidance and returns structured verification results.
        Only processes claims that are scope-changing.
        """
        results = []
        if not preflight_guidance:
            return results
            
        for guidance in preflight_guidance:
            claim_text = guidance.get("what_to_do", "")
            req_text = guidance.get("requirement", "")
            full_claim = f"{req_text}: {claim_text}" if req_text else claim_text
            
            if not (self._is_scope_changing(claim_text) or self._is_scope_changing(req_text)):
                continue
                
            logger.info(f"  🔍 Verifying scope-changing claim: {full_claim}")
            
            # Level 1: Existing Evidence
            evidence = self._check_existing_evidence(claim_text, req_text, evidence_items, discovered_files)
            if evidence:
                results.append(PlanningScopeVerificationResult(
                    claim=full_claim,
                    status=VerificationStatus.VERIFIED_EXISTS,
                    evidence=evidence,
                    planning_implication="Consume existing capability; do not create duplicate implementation."
                ))
                continue
                
            # Level 2: Targeted Repository Verification
            evidence = self._perform_targeted_search(claim_text, req_text, guidance.get("target_file", ""))
            if evidence:
                results.append(PlanningScopeVerificationResult(
                    claim=full_claim,
                    status=VerificationStatus.VERIFIED_EXISTS,
                    evidence=evidence,
                    planning_implication="Consume existing capability; do not create duplicate implementation."
                ))
                continue
                
            # Level 3: Classification
            # We default to UNKNOWN unless we can definitively prove absence.
            # For now, we do not have a robust generic absence prover, so we use UNKNOWN.
            # (zero search results -> UNKNOWN, never VERIFIED_ABSENT)
            results.append(PlanningScopeVerificationResult(
                claim=full_claim,
                status=VerificationStatus.UNKNOWN,
                evidence=[],
                planning_implication="Capability is unresolved. UNKNOWN is NOT evidence of absence. Do not silently convert UNKNOWN into 'create new implementation'."
            ))
            
        return results
        
    def _is_scope_changing(self, text: str) -> bool:
        """
        Heuristic to determine if a claim materially changes implementation tasks
        (e.g., creating a whole new endpoint vs modifying existing UI).
        """
        if not text:
            return False
        text_lower = text.lower()
        
        # Explicitly looking for "new" / "create" / "implement" / "introduce" + architectural components
        if any(action in text_lower for action in ["new", "create", "implement", "introduce"]):
            if any(term in text_lower for term in [
                "endpoint", "service", "controller", "api", "resolver", 
                "table", "schema", "backend", "method", "mutation", "query"
            ]):
                return True
                
        # "expose via GraphQL" or "expose via REST" when it implies new backend surface
        if "expose via" in text_lower or "expose a" in text_lower:
            return True
            
        return False
        
    def _check_existing_evidence(
        self, 
        claim_text: str, 
        req_text: str, 
        evidence_items: List[Any], 
        discovered_files: List[dict]
    ) -> List[VerificationEvidence]:
        """Check if current evidence ledger or discovered files already prove the capability."""
        keywords = self._get_combined_keywords(claim_text, req_text)
        found_evidence = []
        
        if not keywords:
            return found_evidence
            
        # Check evidence items
        for item in (evidence_items or []):
            content = getattr(item, "content_snippet", "") or (item.get("content_snippet", "") if isinstance(item, dict) else "")
            file_path = getattr(item, "file_path", "") or (item.get("file_path", "") if isinstance(item, dict) else "")
            
            if self._matches_keywords(content, keywords) or self._matches_keywords(file_path, keywords):
                found_evidence.append(VerificationEvidence(
                    file=file_path,
                    reason=f"Found existing evidence matching claim keywords ({', '.join(keywords)})"
                ))
                return found_evidence

        # Check discovered files
        for df in (discovered_files or []):
            p = df.get("path", "") if isinstance(df, dict) else getattr(df, "path", "")
            if p and self._matches_keywords(p, keywords):
                found_evidence.append(VerificationEvidence(
                    file=p,
                    reason=f"Found existing discovered file matching claim keywords ({', '.join(keywords)})"
                ))
                return found_evidence
                
        return found_evidence
        
    def _perform_targeted_search(
        self, 
        claim_text: str, 
        req_text: str, 
        target_file: str
    ) -> List[VerificationEvidence]:
        """Use repository intelligence to find the capability."""
        keywords = self._get_combined_keywords(claim_text, req_text)
        found_evidence = []
        
        if not keywords:
            return found_evidence
            
        # 1. Symbol Search via sqlite_store
        localizer = getattr(self.agents, "localizer", None)
        sqlite_store = getattr(localizer, "sqlite_store", None) if localizer else None
        workspace_path = getattr(self.agents, "workspace_path", None)
        
        if sqlite_store:
            for kw in keywords:
                try:
                    rows = sqlite_store.search_symbols(
                        kw, kinds=["class", "interface", "method", "function", "property", "type"], limit=5
                    )
                    for row in rows:
                        row_path = row.get("path", "")
                        sym_name = row.get("name", "")
                        
                        # Verify the file actually exists if workspace_path is known
                        if workspace_path:
                            try:
                                ws = Path(workspace_path) if isinstance(workspace_path, str) else workspace_path
                                if not (ws / row_path).exists():
                                    continue
                            except Exception:
                                pass
                            
                        found_evidence.append(VerificationEvidence(
                            file=row_path,
                            symbol=sym_name,
                            reason=f"Found symbol '{sym_name}' matching keyword '{kw}'"
                        ))
                        return found_evidence # Return on first strong hit
                except Exception as e:
                    logger.debug(f"Symbol search failed for {kw}: {e}")
                    
        # 2. Literal Search via RepositorySearchEngine
        repo_search = getattr(self.agents, "repo_search", None)
        if repo_search:
            for kw in keywords:
                try:
                    hits = repo_search.search_literal(kw, max_results=3)
                    for hit in hits:
                        hit_path = getattr(hit, "file_path", "") or (hit.get("file_path", "") if isinstance(hit, dict) else "")
                        if hit_path:
                            found_evidence.append(VerificationEvidence(
                                file=hit_path,
                                reason=f"Found literal match for keyword '{kw}'"
                            ))
                            return found_evidence
                except Exception as e:
                    logger.debug(f"Literal search failed for {kw}: {e}")
                    
        # 3. Relationship resolution (if we know the target file)
        rel_provider = getattr(self.agents, "relationship_provider", None)
        if target_file and rel_provider:
            try:
                rels = rel_provider.get_relationships_for_file(target_file)
                for rel in (rels or []):
                    source = getattr(rel, "source_file", "") or (rel.get("source_file", "") if isinstance(rel, dict) else "")
                    if source and self._matches_keywords(source, keywords):
                        found_evidence.append(VerificationEvidence(
                            file=source,
                            reason=f"Found capability through established repository relationship with {target_file}"
                        ))
                        return found_evidence
            except Exception as e:
                logger.debug(f"Relationship search failed for {target_file}: {e}")

        return found_evidence
        
    def _get_combined_keywords(self, claim_text: str, req_text: str) -> List[str]:
        claim_kw = self._extract_keywords(claim_text)
        req_kw = self._extract_keywords(req_text)
        return claim_kw + [k for k in req_kw if k not in claim_kw]

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords for searching."""
        # Strip common stop words and generic verbs/nouns that pollute search
        stop_words = {
            "new", "add", "create", "update", "modify", "delete", "remove", 
            "endpoint", "service", "method", "class", "function", "the", "a", 
            "an", "is", "required", "for", "via", "to", "in", "on", "with",
            "backend", "frontend", "api", "rest", "graphql", "component",
            "needs", "needed", "should", "be", "exposed", "added"
        }
        
        words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
        
        # Filter and deduplicate
        keywords = []
        for w in words:
            if w.lower() not in stop_words:
                if w not in keywords:
                    keywords.append(w)
                
        # Also extract PascalCase / camelCase terms as strong keywords
        camel_pascal = re.findall(r'\b[a-z]+[A-Z][a-zA-Z]*\b|\b[A-Z][a-z]+[A-Z][a-zA-Z]*\b', text)
        for w in camel_pascal:
            if w not in keywords and w.lower() not in stop_words:
                keywords.append(w)
                
        return keywords
        
    def _matches_keywords(self, text: str, keywords: List[str]) -> bool:
        if not text:
            return False
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)
