"""
CC4E Dynamic Reasoning Engine (Ticket-to-Code v2).

A ReAct-style reasoner specialized for ONE product (CC4E). It orchestrates the
existing capabilities (RAG, discovery, generation, build, verification) as tools
and compounds knowledge across tickets via the CC4E Brain.

Public API:
    from ticket_to_code.reasoning import run_cc4e_reasoning_agent, CC4EBrain, BrainManager
"""

from .cc4e_brain import CC4EBrain
from .brain_manager import BrainManager
from .engine import run_cc4e_reasoning_agent, ReasoningResult

__all__ = [
    "run_cc4e_reasoning_agent",
    "ReasoningResult",
    "CC4EBrain",
    "BrainManager",
]
