"""
Query Expansion Engine

Deterministic, zero-token-cost expansion of search terms into equivalent
literal representations for code search.

Supports:
  1. Version normalization   — "26.2" → {"26.2", "260200"}
  2. Identifier variants     — "customer-id" → kebab, snake, camel, Pascal, UPPER_SNAKE

Design principles:
  • No LLM, no network, no I/O — pure string transforms.
  • expand()          — returns flat Set[str] (used by search loops).
  • expand_with_map() — returns Dict[str, Set[str]] mapping original → all
                        expansions.  This provenance map is passed downstream
                        to the EvidenceRankingEngine so it can credit expanded
                        literal matches correctly.
"""

import re
from typing import Dict, Set


class QueryExpansionEngine:
    """
    Deterministic query term expander.

    Usage::

        engine = QueryExpansionEngine()

        # Flat expansion — for search loops
        terms = engine.expand("26.2")       # {"26.2", "260200"}

        # Provenance map — for ranking
        mapping = engine.expand_with_map(["26.2", "26.3"])
        # {"26.2": {"26.2", "260200"}, "26.3": {"26.3", "260300"}}
    """

    def expand(self, term: str) -> Set[str]:
        """
        Expand a search term into all equivalent literal representations.

        Returns the original term plus every derived form.
        Always deterministic — no external calls.
        """
        return self._derive(term.strip())

    def expand_with_map(self, terms: list) -> Dict[str, Set[str]]:
        """
        Expand a list of terms and return the full provenance map.

        Returns:
            Dict mapping each original term → its full expansion set.
            Includes the original itself in each expansion set.

        Example::

            expand_with_map(["26.2", "customer-id"])
            # {
            #   "26.2":        {"26.2", "260200"},
            #   "customer-id": {"customer-id", "customer_id", "customerId", ...},
            # }
        """
        mapping: Dict[str, Set[str]] = {}
        for term in terms:
            stripped = term.strip()
            if stripped:
                mapping[stripped] = self._derive(stripped)
        return mapping

    # ── Internal ──────────────────────────────────────────────────────────────

    def _derive(self, term: str) -> Set[str]:
        """Core derivation — returns a set of all equivalent representations."""
        if not term:
            return set()

        expanded: Set[str] = {term}

        # 1. Version Normalization
        # Pattern: major.minor  →  major(minor:02d)00
        # e.g. "26.2" → "260200",  "27.1" → "270100",  "26.10" → "261000"
        #
        # Use re.finditer (not re.match) so versions embedded inside longer
        # phrases are still extracted and normalized.  This lets a literal such
        # as "Core Collaboration for Engineering 26.2" yield both the bare
        # version "26.2" and the packed numeric form "260200", which is how the
        # codebase actually stores the version constant.
        for version_match in re.finditer(r"(\d+)\.(\d+)", term):
            major = version_match.group(1)
            minor = int(version_match.group(2))
            bare = f"{major}.{version_match.group(2)}"
            expanded.add(bare)                       # isolated "26.2"
            expanded.add(f"{major}{minor:02d}00")    # packed "260200"

        # 2. Identifier / naming-convention variants
        # Split on: spaces, hyphens, underscores, camelCase boundaries
        spaced = re.sub(r"[-_]", " ", term)
        spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", spaced)
        tokens = [t.lower() for t in spaced.split() if t.strip()]

        if len(tokens) > 1:
            kebab      = "-".join(tokens)
            snake      = "_".join(tokens)
            upper_snake = snake.upper()
            camel      = tokens[0] + "".join(t.capitalize() for t in tokens[1:])
            pascal     = "".join(t.capitalize() for t in tokens)
            expanded.update({
                " ".join(tokens),
                kebab,
                snake,
                upper_snake,
                camel,
                pascal,
            })

        return expanded

