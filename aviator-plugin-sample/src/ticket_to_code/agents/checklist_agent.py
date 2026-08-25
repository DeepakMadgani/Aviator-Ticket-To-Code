"""
Phase 0: ChecklistAgent — Convert ticket into a mechanical definition-of-done.

Each checklist item is:
  - A concrete, testable assertion about the code
  - Tied to a specific file or symbol
  - Verifiable by grep/AST inspection (not vibes)

Uses the same LLM as the pipeline. Called after plan_node, before generate_code_node.
Stored in state["definition_of_done"].
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)


@dataclass
class ChecklistItem:
    """A single verifiable requirement derived from the ticket."""
    id: int
    description: str
    file_hint: str = ""          # expected file (e.g., "add-members.component.ts")
    symbol_hint: str = ""        # expected symbol (e.g., "allProjectMembers")
    verification_type: str = ""  # "grep" | "ast" | "semantic"
    status: str = "pending"      # "pending" | "pass" | "fail"
    evidence: str = ""           # citation when verified


@dataclass
class DefinitionOfDone:
    """Complete definition-of-done for a ticket."""
    items: List[ChecklistItem] = field(default_factory=list)
    ticket_title: str = ""
    
    def pending_items(self) -> List[ChecklistItem]:
        return [item for item in self.items if item.status == "pending"]
    
    def failed_items(self) -> List[ChecklistItem]:
        return [item for item in self.items if item.status == "fail"]
    
    def pass_rate(self) -> float:
        if not self.items:
            return 0.0
        passed = sum(1 for item in self.items if item.status == "pass")
        return passed / len(self.items)


class ChecklistAgent:
    """
    Phase 0: Convert ticket + requirements into a numbered definition-of-done.
    
    Runs BEFORE code generation. The output is consumed by:
    - Phase 5 (ChecklistVerifier): checks each item against generated code
    - Phase 6 (Escalation): unresolved items trigger escalation instead of silent ship
    """

    SYSTEM_PROMPT = """You are a QA lead converting a development ticket into a testable checklist.

For each requirement in the ticket, produce a numbered checklist item that:
1. Describes a SPECIFIC, OBSERVABLE behavior or code artifact
2. Names the expected FILE where this should appear (if knowable)
3. Names the expected SYMBOL (method, property, class) if applicable
4. Can be verified by reading the code — not by running the app

RULES:
- Each item must be independently verifiable by reading ONE file
- "Logic that calls an API" is verifiable (grep for service call). "Works correctly" is NOT.
- If an item verifies a property declaration, use `"verification_type": "declaration"`
- If an item verifies logic that SETS/ASSIGNS a property, use `"verification_type": "assignment"`
- If an item verifies a function call, use `"verification_type": "call"`
- For other logic, use `"verification_type": "logic"`
- Separate "declares X" from "sets X" — declaring a property and writing logic to SET it are different verifiable steps
- Include both the POSITIVE case (shows read-only) and NEGATIVE case (shows dropdown) as separate items
- Maximum 12 items. Minimum 3 items.

Respond with JSON ONLY:
{
  "items": [
    {
      "description": "add-members.component.ts declares allProjectMembers property",
      "file_hint": "add-members.component.ts",
      "symbol_hint": "allProjectMembers",
      "verification_type": "declaration"
    },
    {
      "description": "add-members.component.ts logic sets allProjectMembers property",
      "file_hint": "add-members.component.ts",
      "symbol_hint": "allProjectMembers",
      "verification_type": "assignment"
    },
    ...
  ]
}

No markdown, no explanations, ONLY valid JSON."""

    def __init__(self, llm):
        self.llm = llm

    def generate_checklist(
        self,
        ticket_title: str,
        ticket_description: str,
        requirements: Optional[object] = None,
        plan_tasks: Optional[list] = None,
    ) -> DefinitionOfDone:
        """
        Generate a definition-of-done checklist from the ticket.
        
        Args:
            ticket_title: The ticket title
            ticket_description: The ticket description
            requirements: StructuredRequirements object (if available)
            plan_tasks: List of DevelopmentTask objects from the planner (if available)
        
        Returns:
            DefinitionOfDone with numbered checklist items
        """
        # Build context from requirements
        req_context = ""
        if requirements:
            func_reqs = getattr(requirements, "functional_requirements", []) or []
            if func_reqs:
                req_context = "\n\nFUNCTIONAL REQUIREMENTS:\n" + "\n".join(
                    f"  {i+1}. {r}" for i, r in enumerate(func_reqs)
                )

        # Build context from plan tasks
        plan_context = ""
        if plan_tasks:
            plan_lines = []
            for t in plan_tasks:
                _type = getattr(getattr(t, "task_type", None), "value", "?")
                plan_lines.append(
                    f"  - [{_type.upper()}] {getattr(t, 'file_path', '?')}: "
                    f"{getattr(t, 'title', '?')}"
                )
            plan_context = "\n\nPLANNED TASKS:\n" + "\n".join(plan_lines)

        user_prompt = (
            f"TICKET: {ticket_title}\n\n"
            f"DESCRIPTION:\n{ticket_description}"
            f"{req_context}"
            f"{plan_context}"
            f"\n\nGenerate the checklist. JSON only."
        )

        try:
            from ticket_to_code.llm_utils import llm_invoke
            response = llm_invoke(
                self.llm,
                [
                    SystemMessage(content=self.SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
            )
            raw = response.content if hasattr(response, "content") else str(response)
            return self._parse_response(raw, ticket_title)

        except Exception as e:
            logger.warning(f"ChecklistAgent failed: {e} — returning empty checklist")
            return DefinitionOfDone(ticket_title=ticket_title)

    def _parse_response(self, raw: str, ticket_title: str) -> DefinitionOfDone:
        """Parse the LLM's JSON response into a DefinitionOfDone."""
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            logger.warning("ChecklistAgent: no JSON found in response")
            return DefinitionOfDone(ticket_title=ticket_title)

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f"ChecklistAgent: JSON parse failed: {e}")
            return DefinitionOfDone(ticket_title=ticket_title)

        items = []
        for i, item_data in enumerate(data.get("items", [])):
            items.append(ChecklistItem(
                id=i + 1,
                description=item_data.get("description", ""),
                file_hint=item_data.get("file_hint", ""),
                symbol_hint=item_data.get("symbol_hint", ""),
                verification_type=item_data.get("verification_type", "semantic"),
            ))

        if not items:
            logger.warning("ChecklistAgent: response had zero items")

        dod = DefinitionOfDone(items=items, ticket_title=ticket_title)
        logger.info(
            f"  ✅ ChecklistAgent: generated {len(items)} checklist items "
            f"for '{ticket_title[:60]}'"
        )
        return dod
