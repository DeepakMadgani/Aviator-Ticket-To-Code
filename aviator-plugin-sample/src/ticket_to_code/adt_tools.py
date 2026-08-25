"""
Ticket-to-Code ADT Plugin Tools

Exposes the Autonomous Ticket-to-Code Engine as a native tool
for the Aviator ADT's ContentAviatorAgent. The ADT Assistant can invoke 
this tool to hand over control to the backend Orchestrator.

Author: AI Assistant
Date: April 2026
"""

import logging
import re
from typing import Annotated, List, Dict
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from aviator.models import StateModel
from aviator.settings import settings
from ticket_to_code.models import TicketPriority
from ticket_to_code.conversation.session_manager import ConversationSessionManager
from ticket_to_code.workflow import run_autonomous_workflow

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
conversation_manager = ConversationSessionManager()


def _resolve_workspace_path(task_description: str, messages: List[Dict[str, str]]) -> str:
    """Resolve the best workspace path from user intent, with safe fallback."""
    settings_base = getattr(settings, "base_dir", None)
    fallback = Path(settings_base) if settings_base else Path.cwd()
    search_text = "\n".join([
        task_description or "",
        *[m.get("content", "") for m in messages],
    ])

    # Prefer explicit Windows paths mentioned by the user.
    path_matches = re.findall(r"[A-Za-z]:\\[^\n\r\"']+", search_text)
    for raw_path in path_matches:
        candidate = Path(raw_path.strip())
        if candidate.exists() and candidate.is_dir():
            return str(candidate)

    # Common shorthand for this environment's external target repo.
    if "cc4e" in search_text.lower():
        cc4e_path = Path(r"C:\CC4E")
        if cc4e_path.exists() and cc4e_path.is_dir():
            return str(cc4e_path)

    return str(fallback)


class AviatorConversationAdapter:
    """
    Adapter layer ensuring the Engineering Engine remains 100% frontend-agnostic.
    Converts Aviator's specific StateModel into standard Python dicts/lists.
    """
    @staticmethod
    def extract_messages(state: StateModel) -> List[Dict[str, str]]:
        # In a real environment, this might also extract image base64, attached files, etc.
        return [{"role": m.type, "content": m.content} for m in state.messages]

    @staticmethod
    def extract_acceptance_criteria(messages: List[Dict[str, str]]) -> List[str]:
        criteria: List[str] = []
        for msg in messages:
            text = msg.get("content", "")
            for line in text.splitlines():
                clean = line.strip()
                if clean.startswith("-"):
                    criteria.append(clean[1:].strip())
                elif clean[:2].isdigit() and clean[2:3] in {".", ")"}:
                    criteria.append(clean[3:].strip())
        return criteria[:10]


@tool
@tracer.start_as_current_span("run_autonomous_engineering_engine")
def run_autonomous_engineering_engine(
    task_description: str,
    state: Annotated[StateModel, InjectedState],
) -> str:
    """Execute the full autonomous software engineering pipeline.
    
    Use this tool when the user asks to implement a feature, fix a bug, or execute a coding task.
    This hands over control to the dedicated Ticket-to-Code Orchestrator, which will plan, generate,
    compile, and validate the code changes.
    
    Args:
        task_description: A summary of what the user wants to accomplish.
        state: Injected ADT conversation state (contains full message history and images).
        
    Returns:
        A markdown string detailing the result, any needed clarifications, or the final patch plan.
    """
    logger.info(f"Handing over to Autonomous LangGraph Engine for task: {task_description}")
    
    # 1. Use the Adapter to isolate frontend
    standard_messages = AviatorConversationAdapter.extract_messages(state)
    acceptance_criteria = AviatorConversationAdapter.extract_acceptance_criteria(standard_messages)

    resolved_workspace = _resolve_workspace_path(task_description, standard_messages)
    logger.info("Resolved ticket-to-code workspace: %s", resolved_workspace)

    # 2. Build an interactive conversation session and synthesize an evidence-rich ticket
    session = conversation_manager.create_session(
        workspace_path=resolved_workspace,
        goal=task_description,
        initial_message=task_description,
    )
    for msg in standard_messages[-25:]:
        if msg.get("role") == "user" and msg.get("content"):
            conversation_manager.add_user_message(session.session_id, msg["content"])

    ticket = conversation_manager.synthesize_ticket(session.session_id, priority=TicketPriority.MEDIUM)
    if acceptance_criteria:
        ticket.acceptance_criteria = acceptance_criteria
    ticket.ticket_id = f"ADT-CHAT-{session.session_id}"
    ticket.labels = sorted(set(ticket.labels + ["adt-chat", "autonomous-engine"]))
    
    # 3. Run the autonomous workflow
    try:
        conversation_manager.mark_running(session.session_id)
        final_state = run_autonomous_workflow(
            ticket=ticket,
            workspace_path=resolved_workspace
        )
        
        status = final_state.get('status', 'unknown')
        errors = final_state.get('errors', []) if isinstance(final_state, dict) else []
        conversation_manager.attach_workflow_result(session.session_id, status=status, errors=errors)

        reasoning = conversation_manager.reasoning_snapshot(session.session_id)
        if status == "success":
            code_files = final_state.get('generated_code', [])
            return (
                f"### Autonomous Engine Completed Successfully\n\n"
                f"**Status:** {status}\n"
                f"**Files Modified/Generated:** {len(code_files)}\n"
                f"**Primary Hypothesis:** {reasoning.primary_hypothesis}\n"
                f"**Confidence:** {reasoning.confidence}"
            )
        elif status == "failed":
            errors = final_state.get('errors', [])
            error_str = "\n".join([str(e) for e in errors]) if errors else "Unknown error."
            return f"**Autonomous Engine Failed:**\n{error_str}"
        else:
            return f"Engine finished in an incomplete state. Status: {status}"
            
    except Exception as e:
        logger.error(f"Engine orchestration failed: {e}", exc_info=True)
        conversation_manager.attach_workflow_result(session.session_id, status="failed", errors=[str(e)])
        return f"Autonomous Engine encountered a fatal error: {str(e)}"
