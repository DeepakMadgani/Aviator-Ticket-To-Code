from __future__ import annotations

import logging
from typing import Any

from aviator_core.ticket_solver.skill_blocks import SkillBlock

logger = logging.getLogger(__name__)


class Neo4jQuerySkill(SkillBlock):
    """Query the Neo4j Code Graph."""

    name = "neo4j_query"
    description = (
        "Execute a raw Cypher query against the Neo4j Code Graph. "
        "HINT: You MUST include {workspace: $workspace} in all of your MATCH clauses. "
        "The system injects the $workspace parameter for you automatically."
    )

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute Neo4j query.

        Args:
            query (str): Cypher query string
            parameters (dict, optional): Query parameters
        """
        query = kwargs.get("query", "")
        if not query:
            return {"error": "Missing 'query' argument"}
            
        # Hard Mechanical Gate: Reject if $workspace is completely missing from the raw string
        if "$workspace" not in query:
            return {
                "error": "SECURITY REJECTION: Raw Cypher queries must contain the literal '$workspace' token "
                         "to ensure data isolation (e.g. MATCH (n {workspace: $workspace}))."
            }
            
        parameters = kwargs.get("parameters", {})
        # Parameter Injection: Force the workspace parameter regardless of what the caller passed
        if hasattr(self, "context") and hasattr(self.context, "workspace_name"):
            parameters["workspace"] = self.context.workspace_name
        else:
            # Fallback for testing/standalone
            parameters["workspace"] = "default_workspace"
        
        try:
            from aviator_core.storage.neo4j_store import Neo4jStore
            store = Neo4jStore(workspace_path=parameters["workspace"])
            res = store.query(query, parameters)
            return {"results": res}
        except Exception as e:
            logger.error(f"Neo4j query execution failed: {e}", exc_info=True)
            return {"error": f"Database execution failed: {e}"}


class FindCallersSkill(SkillBlock):
    """Find callers of a method using the Code Graph."""

    name = "find_callers"
    description = "Find all methods that call the specified target method"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Find callers.

        Args:
            method_name (str): Name of the method
            class_name (str, optional): Name of the class
        """
        method = kwargs.get("method_name", "")
        if not method:
            return {"error": "Missing 'method_name' argument"}
            
        logger.warning("FindCallersSkill is a stub — Graph DB integration pending")
        return {"error": "Graph DB integration not yet configured in this environment"}


class FindImplementationsSkill(SkillBlock):
    """Find implementors of an interface using the Code Graph."""

    name = "find_implementations"
    description = "Find all classes that implement the specified interface"

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Find implementors.

        Args:
            interface_name (str): Name of the interface
        """
        interface = kwargs.get("interface_name", "")
        if not interface:
            return {"error": "Missing 'interface_name' argument"}
            
        logger.warning("FindImplementationsSkill is a stub — Graph DB integration pending")
        return {"error": "Graph DB integration not yet configured in this environment"}
