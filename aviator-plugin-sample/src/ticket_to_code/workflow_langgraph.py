"""
Ticket-to-Code Workflow with LangGraph - TDD Approach

This implements Test-Driven Development with AI:
1. Generate test cases FIRST (using product behavior RAG)
2. Generate code SECOND (using architecture RAG)
3. Tests and code are independent (no knowledge leak)
4. Both grounded in real knowledge (no hallucinations)

Author: Deepak Madgani
Date: April 2026
"""

import logging
from pathlib import Path
from typing import Annotated, Literal, TypedDict, List, Optional
from datetime import datetime

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from ticket_to_code.models import (
    ValueEdgeTicket,
    InvestigationResult,
    StructuredRequirements,
    ArchitecturalPlan,
    GeneratedCode,
    BuildResult,
    TestResult
)

from ticket_to_code.agents.investigation_agent import InvestigationAgent
from ticket_to_code.agents.ticket_analyzer import TicketAnalyzerAgent
from ticket_to_code.agents.planning_agent import PlanningAgent
from ticket_to_code.agents.rag_engine import CodebaseRAGEngine
from ticket_to_code.agents.code_generator import CodeGeneratorAgent
from ticket_to_code.execution.base_executor import ExecutionEngineFactory

logger = logging.getLogger(__name__)


# ============================================================================
# STATE DEFINITION
# ============================================================================

class TicketToCodeState(TypedDict):
    """
    Complete workflow state managed by LangGraph.
    
    Annotated fields are automatically tracked and merged.
    """
    # Input
    ticket: ValueEdgeTicket
    workspace_path: str
    max_retry_attempts: int
    
    # Phase results
    investigation_result: Optional[InvestigationResult]
    requirements: Optional[StructuredRequirements]
    architectural_plan: Optional[ArchitecturalPlan]
    
    # RAG contexts (SEPARATED for independence)
    test_rag_context: Optional[List[dict]]  # Product behavior knowledge
    code_rag_context: Optional[List[dict]]  # Architecture pattern knowledge
    
    # Generated artifacts (TDD order)
    generated_tests: Optional[List[GeneratedCode]]  # Generated FIRST
    generated_code: Optional[List[GeneratedCode]]   # Generated SECOND
    
    # Execution results
    build_result: Optional[BuildResult]
    test_result: Optional[TestResult]
    
    # Retry tracking
    retry_attempt: Annotated[int, "Current retry attempt"]
    last_error_type: Optional[Literal["build", "test"]]
    
    # Status
    status: str
    errors: Annotated[List[str], "Accumulated errors"]
    start_time: datetime
    end_time: Optional[datetime]


# ============================================================================
# AGENT INITIALIZATION
# ============================================================================

class WorkflowAgents:
    """Container for all agents"""
    
    def __init__(self, workspace_path: str):
        self.workspace_path = Path(workspace_path)
        
        # Initialize agents
        self.investigation = InvestigationAgent()
        self.analyzer = TicketAnalyzerAgent()
        self.planner = PlanningAgent()
        self.rag_engine = CodebaseRAGEngine()
        self.code_generator = CodeGeneratorAgent()
        self.test_generator = CodeGeneratorAgent()  # Separate instance for tests
        self.llm = self.code_generator.llm
        
        # Initialize execution engine
        self.executor = ExecutionEngineFactory.create(str(workspace_path))
        
        logger.info(f"Workflow agents initialized for: {workspace_path}")


# ============================================================================
# GRAPH NODES (Each phase is a simple function)
# ============================================================================

def investigate_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 0: Investigation & Triage
    
    Determines if code changes are needed or just configuration guidance.
    """
    logger.info("🔍 PHASE 0: Investigation & Triage")
    
    codebase_summary = f"Codebase at: {state['workspace_path']}"
    
    investigation = agents.investigation.investigate(
        state["ticket"],
        codebase_summary=codebase_summary
    )
    
    logger.info(
        f"Investigation complete:\n"
        f"  Type: {investigation.ticket_type.value}\n"
        f"  Code needed: {investigation.requires_code_changes}\n"
        f"  Confidence: {investigation.confidence:.2f}"
    )
    
    return {
        "investigation_result": investigation,
        "status": "investigation_complete"
    }


def analyze_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 1: Requirement Analysis
    
    Extracts structured requirements from ticket.
    """
    logger.info("📋 PHASE 1: Requirement Analysis")
    
    requirements = agents.analyzer.analyze_ticket(state["ticket"])
    
    logger.info(
        f"Analysis complete:\n"
        f"  Functional: {len(requirements.functional_requirements)}\n"
        f"  Technical: {len(requirements.technical_requirements)}"
    )
    
    return {
        "requirements": requirements,
        "status": "analysis_complete"
    }


def plan_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 2: Architectural Planning
    
    Designs architecture and identifies files to create/modify.
    """
    logger.info("🎯 PHASE 2: Architectural Planning")
    
    plan = agents.planner.create_plan(
        state["ticket"],
        state["requirements"]
    )
    
    logger.info(
        f"Planning complete:\n"
        f"  Tasks: {len(plan.tasks)}\n"
        f"  API changes: {len(plan.api_changes)}"
    )
    
    return {
        "architectural_plan": plan,
        "status": "planning_complete"
    }


def rag_for_tests_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 3A: RAG Context Retrieval for TEST GENERATION
    
    Retrieves PRODUCT BEHAVIOR knowledge:
    - How features currently work
    - Existing test patterns
    - Business rules and validations
    - Error handling patterns
    
    This context is ONLY used for test generation, NOT for code.
    """
    logger.info("📚 PHASE 3A: RAG Retrieval for Test Cases (Product Behavior)")
    
    # Query for BEHAVIOR knowledge
    test_context = agents.rag_engine.multi_stage_retrieval(
        requirements=state["requirements"],
        architectural_plan=state["architectural_plan"],
        query_focus="test_behavior",  # Focus on how things work
        document_types=["tests", "documentation", "business_rules"],
        max_iterations=3
    )
    
    logger.info(
        f"Test RAG complete:\n"
        f"  Behavior examples: {len(test_context)}\n"
        f"  Sources: tests, docs, business rules"
    )
    
    return {
        "test_rag_context": test_context,
        "status": "test_rag_complete"
    }


def rag_for_code_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 3B: RAG Context Retrieval for CODE GENERATION
    
    Retrieves ARCHITECTURE knowledge:
    - Code patterns and structure
    - Class hierarchies
    - Interface implementations
    - Coding standards
    
    This context is ONLY used for code generation, NOT for tests.
    INDEPENDENT from test context (no knowledge leak).
    """
    logger.info("📚 PHASE 3B: RAG Retrieval for Code (Architecture Patterns)")
    
    # Query for ARCHITECTURE knowledge
    code_context = agents.rag_engine.multi_stage_retrieval(
        requirements=state["requirements"],
        architectural_plan=state["architectural_plan"],
        query_focus="architecture",  # Focus on how to build
        document_types=["source_code", "interfaces", "patterns"],
        max_iterations=3
    )
    
    logger.info(
        f"Code RAG complete:\n"
        f"  Architecture examples: {len(code_context)}\n"
        f"  Sources: source code, interfaces, patterns"
    )
    
    return {
        "code_rag_context": code_context,
        "status": "code_rag_complete"
    }


def generate_tests_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 4A: Generate Test Cases FIRST (TDD)
    
    Uses ONLY test_rag_context (product behavior).
    Generates tests that verify expected behavior based on:
    - Existing test patterns
    - Business rules
    - Product documentation
    
    NO access to code patterns (prevents test-code coupling).
    """
    logger.info("🧪 PHASE 4A: Generate Test Cases (TDD - Tests First!)")
    
    test_generation_prompt = f"""
You are generating UNIT TESTS for a new feature.

IMPORTANT: Generate tests BEFORE the code exists (Test-Driven Development).
Base your tests on:
1. Product behavior (how it SHOULD work)
2. Existing test patterns (structure and style)
3. Business rules (validation, edge cases)

DO NOT assume implementation details - focus on BEHAVIOR CONTRACT.

Ticket: {state['ticket'].title}
Requirements: {state['requirements']}
Plan: {state['architectural_plan']}

Product Behavior Context (from existing tests/docs):
{state['test_rag_context']}

Generate comprehensive test cases that:
- Cover happy path scenarios
- Test edge cases and error conditions
- Verify business rules
- Follow existing test patterns
- Use real product behavior (no imagination)

Technology: C# with xUnit/NUnit
"""
    
    generated_tests = []
    
    for task in state["architectural_plan"].tasks:
        logger.info(f"  Generating tests for: {task.file_path}")
        
        # Generate test file
        test_file_path = task.file_path.replace("Services/", "Tests/").replace(".cs", "Tests.cs")
        
        test_code = agents.test_generator.generate_code(
            task=task,
            requirements=state["requirements"],
            retrieved_context=state["test_rag_context"],  # ONLY behavior context
            architectural_plan=state["architectural_plan"],
            custom_prompt=test_generation_prompt
        )
        
        # Override file path to test directory
        test_code.file_path = test_file_path
        test_code.language = "csharp_test"
        
        # Write test file
        output_path = Path(state["workspace_path"]) / test_file_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(test_code.code, encoding='utf-8')
        
        generated_tests.append(test_code)
        logger.info(f"  ✅ Test written: {output_path}")
    
    logger.info(f"Test generation complete: {len(generated_tests)} test files")
    
    return {
        "generated_tests": generated_tests,
        "status": "tests_generated"
    }


def generate_code_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 4B: Generate Implementation Code SECOND (TDD)
    
    Uses ONLY code_rag_context (architecture patterns).
    Generates code that:
    - Follows architectural patterns
    - Implements clean structure
    - Matches coding standards
    
    NO access to test context (prevents code knowing test internals).
    Code must satisfy tests independently.
    """
    logger.info("💻 PHASE 4B: Generate Implementation Code (to satisfy tests)")
    
    code_generation_prompt = f"""
You are generating PRODUCTION CODE to satisfy pre-written tests.

IMPORTANT: Tests already exist. Your code must make them pass.
Base your implementation on:
1. Architectural patterns (how to structure code)
2. Existing code examples (style and conventions)
3. Coding standards (best practices)

DO NOT look at test internals - implement to satisfy the CONTRACT.

Ticket: {state['ticket'].title}
Requirements: {state['requirements']}
Plan: {state['architectural_plan']}

Architecture Context (from existing code):
{state['code_rag_context']}

Generate production-ready code that:
- Follows architectural patterns
- Implements requirements correctly
- Handles errors appropriately
- Uses dependency injection
- Includes logging
- Follows coding standards

Technology: C# / .NET
"""
    
    generated_code = []
    
    for task in state["architectural_plan"].tasks:
        logger.info(f"  Generating code for: {task.file_path}")
        
        code = agents.code_generator.generate_code(
            task=task,
            requirements=state["requirements"],
            retrieved_context=state["code_rag_context"],  # ONLY architecture context
            architectural_plan=state["architectural_plan"],
            custom_prompt=code_generation_prompt
        )
        
        # Write code file
        output_path = Path(state["workspace_path"]) / code.file_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(code.code, encoding='utf-8')
        
        generated_code.append(code)
        logger.info(f"  ✅ Code written: {output_path}")
    
    logger.info(f"Code generation complete: {len(generated_code)} files")
    
    return {
        "generated_code": generated_code,
        "status": "code_generated"
    }


def build_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 5: Build Project
    
    Compiles both generated code AND generated tests.
    """
    logger.info("🔧 PHASE 5: Build Project")

    if hasattr(agents, "repo_search") and agents.repo_search:
        agents.repo_search.invalidate_index()
    
    # Find project file
    workspace = Path(state["workspace_path"])
    project_files = list(workspace.glob("**/*.csproj"))
    
    if not project_files:
        logger.error("No .csproj file found")
        return {
            "build_result": BuildResult(status="failed", errors=["No project file found"]),
            "status": "build_failed",
            "last_error_type": "build"
        }
    
    project_file = str(project_files[0])
    logger.info(f"Building: {project_file}")
    
    build_result = agents.executor.execute_build(project_file)
    
    if build_result.status.value != "success":
        logger.error(f"Build failed: {len(build_result.errors)} errors")
        return {
            "build_result": build_result,
            "status": "build_failed",
            "last_error_type": "build"
        }
    else:
        logger.info("✅ Build succeeded")
        return {
            "build_result": build_result,
            "status": "build_success"
        }


def test_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Phase 6: Run Tests
    
    Runs AI-generated tests against AI-generated code.
    """
    logger.info("🧪 PHASE 6: Run Tests")
    
    workspace = Path(state["workspace_path"])
    project_files = list(workspace.glob("**/*.csproj"))
    
    if not project_files:
        return {
            "test_result": TestResult(status="failed", total=0, passed=0, failed=0),
            "status": "test_failed",
            "last_error_type": "test"
        }
    
    project_file = str(project_files[0])
    logger.info(f"Running tests: {project_file}")
    
    test_result = agents.executor.execute_tests(project_file)
    
    if test_result.status.value != "success":
        logger.error(f"Tests failed: {test_result.failed}/{test_result.total}")
        return {
            "test_result": test_result,
            "status": "test_failed",
            "last_error_type": "test"
        }
    else:
        logger.info(f"✅ Tests passed: {test_result.passed}/{test_result.total}")
        return {
            "test_result": test_result,
            "status": "test_success"
        }


def fix_build_errors_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Debug Node: Fix Build Errors
    
    Analyzes build errors and regenerates ONLY the code (not tests).
    Tests define the contract - code must adapt.
    """
    logger.info("🔧 DEBUG: Fixing Build Errors")
    
    error_messages = "\n".join(state["build_result"].errors[:10])
    logger.info(f"Analyzing {len(state['build_result'].errors)} build errors...")
    
    try:
        from aviator.services.llm import LLMRegistry
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = LLMRegistry.get_llm()
        
        # Build debug prompt
        system_prompt = """You are an expert debugger. Analyze build errors and fix the code.

TASK: Fix the code to resolve all build errors.

RULES:
1. Analyze the error messages carefully
2. Identify the root cause
3. Generate ONLY the fixed code sections
4. Maintain existing functionality
5. Follow the original code style and patterns
6. Tests are CORRECT - fix the code to satisfy them

Respond with JSON:
{
  "analysis": "Brief analysis of errors",
  "fixes": [
    {
      "file": "path/to/file.cs",
      "fixed_code": "complete corrected code for the file",
      "reason": "why this fixes the error"
    }
  ]
}"""
        
        # Get generated files content
        generated_files_content = "\n\n".join([
            f"FILE: {gen.file_path}\n{gen.code}"
            for gen in state["generated_code"]
        ])
        
        user_prompt = f"""BUILD ERRORS:
{error_messages}

GENERATED CODE:
{generated_files_content}

Analyze and fix these errors."""
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # Parse response and apply fixes
        import json
        try:
            fixes_data = json.loads(response.content)
            logger.info(f"Debug Analysis: {fixes_data.get('analysis', 'No analysis')}")
            
            # Apply fixes to generated code
            fixed_code = []
            for fix in fixes_data.get('fixes', []):
                file_path = fix['file']
                fixed_content = fix['fixed_code']
                
                # Update generated code
                for gen_code in state["generated_code"]:
                    if gen_code.file_path == file_path:
                        gen_code.code = fixed_content
                        
                        # Write to file
                        output_path = Path(state["workspace_path"]) / file_path
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(fixed_content, encoding='utf-8')
                        
                        fixed_code.append(gen_code)
                        logger.info(f"Applied fix to: {file_path}")
            
            logger.info(f"✅ Fixed {len(fixes_data.get('fixes', []))} files")
            
            return {
                "generated_code": state["generated_code"],  # Return updated code
                "retry_attempt": state["retry_attempt"] + 1,
                "status": "build_errors_fixed"
            }
            
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON, returning original code")
            return {
                "generated_code": state["generated_code"],
                "retry_attempt": state["retry_attempt"] + 1,
                "status": "build_errors_fixed"
            }
            
    except Exception as e:
        logger.error(f"Debug agent error: {e}", exc_info=True)
        return {
            "generated_code": state["generated_code"],
            "retry_attempt": state["retry_attempt"] + 1,
            "status": "build_fix_failed"
        }


def fix_test_failures_node(state: TicketToCodeState, agents: WorkflowAgents) -> dict:
    """
    Debug Node: Fix Test Failures
    
    Analyzes test failures and decides:
    - Fix CODE (if implementation wrong)
    - OR Fix TESTS (if test expectations wrong)
    
    Uses LLM to determine which is incorrect.
    """
    logger.info("🔧 DEBUG: Fixing Test Failures")
    
    test_result = state.get("test_result")
    if not test_result:
        return {
            "retry_attempt": state["retry_attempt"] + 1,
            "status": "test_fix_skipped"
        }
    
    test_output = test_result.test_output if hasattr(test_result, 'test_output') else "No output"
    logger.info(f"Analyzing {test_result.failed} test failures...")
    
    try:
        from aviator.services.llm import LLMRegistry
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = LLMRegistry.get_llm()
        
        # Build debug prompt
        system_prompt = """You are an expert debugger specializing in test failures.

TASK: Analyze test failures and determine what needs to be fixed.

IMPORTANT DECISION:
1. If the TEST is wrong (incorrect expectations) → Fix the test
2. If the CODE is wrong (incorrect implementation) → Fix the code
3. Be clear about which one needs fixing

Respond with JSON:
{
  "analysis": "Analysis of test failures",
  "decision": "fix_code" or "fix_tests",
  "fixes": [
    {
      "file": "path/to/file.cs",
      "type": "code" or "test",
      "fixed_code": "complete corrected code for the file",
      "reason": "why this fixes the failure"
    }
  ]
}"""
        
        # Get generated files content
        generated_code_content = "\n\n".join([
            f"FILE: {gen.file_path}\n{gen.code}"
            for gen in state["generated_code"]
        ])
        
        generated_tests_content = "\n\n".join([
            f"TEST FILE: {gen.file_path}\n{gen.code}"
            for gen in state.get("generated_tests", [])
        ]) if state.get("generated_tests") else "No tests generated"
        
        user_prompt = f"""TEST FAILURES:
Total: {test_result.total}
Failed: {test_result.failed}
Passed: {test_result.passed}

TEST OUTPUT:
{test_output[:2000]}

GENERATED CODE:
{generated_code_content[:3000]}

GENERATED TESTS:
{generated_tests_content[:3000]}

REQUIREMENTS:
{state["requirements"]}

Analyze and determine what needs to be fixed."""
        
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        # Parse response and apply fixes
        import json
        try:
            fixes_data = json.loads(response.content)
            logger.info(f"Debug Analysis: {fixes_data.get('analysis', 'No analysis')}")
            logger.info(f"Decision: {fixes_data.get('decision', 'Unknown')}")
            
            # Apply fixes based on decision
            decision = fixes_data.get('decision', 'fix_code')
            
            for fix in fixes_data.get('fixes', []):
                file_path = fix['file']
                fixed_content = fix['fixed_code']
                fix_type = fix.get('type', 'code')
                
                logger.info(f"Fixing {fix_type}: {file_path}")
                
                # Determine which collection to update
                if fix_type == 'test' or 'test' in file_path.lower():
                    # Update test files
                    target_collection = state.get("generated_tests", [])
                else:
                    # Update code files
                    target_collection = state["generated_code"]
                
                # Update the file
                for gen_file in target_collection:
                    if gen_file.file_path == file_path:
                        gen_file.code = fixed_content
                        
                        # Write to file
                        output_path = Path(state["workspace_path"]) / file_path
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(fixed_content, encoding='utf-8')
                        logger.info(f"Applied {fix_type} fix to: {file_path}")
            
            logger.info(f"✅ Applied {len(fixes_data.get('fixes', []))} fixes")
            
            return {
                "generated_code": state["generated_code"],
                "generated_tests": state.get("generated_tests", []),
                "retry_attempt": state["retry_attempt"] + 1,
                "status": "test_failures_fixed"
            }
            
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON response")
            return {
                "retry_attempt": state["retry_attempt"] + 1,
                "status": "test_fix_failed"
            }
            
    except Exception as e:
        logger.error(f"Debug agent error: {e}", exc_info=True)
        return {
            "retry_attempt": state["retry_attempt"] + 1,
            "status": "test_fix_failed"
        }


# ============================================================================
# CONDITIONAL ROUTING FUNCTIONS
# ============================================================================

def should_generate_code(state: TicketToCodeState) -> Literal["analyze", "end"]:
    """Route after investigation: code needed or just explanation?"""
    if state["investigation_result"].requires_code_changes:
        return "analyze"
    return "end"


def check_build_status(state: TicketToCodeState) -> Literal["test", "fix_build", "end"]:
    """Route after build: success, fix, or give up?"""
    if state["build_result"].status.value == "success":
        return "test"
    elif state["retry_attempt"] < state["max_retry_attempts"]:
        return "fix_build"
    else:
        logger.error("Max retry attempts reached for build")
        return "end"


def check_test_status(state: TicketToCodeState) -> Literal["fix_test", "end"]:
    """Route after tests: success, fix, or give up?"""
    if state["test_result"].status.value == "success":
        return "end"
    elif state["retry_attempt"] < state["max_retry_attempts"]:
        return "fix_test"
    else:
        logger.error("Max retry attempts reached for tests")
        return "end"


# ============================================================================
# BUILD LANGGRAPH WORKFLOW
# ============================================================================

def create_ticket_to_code_graph(workspace_path: str) -> StateGraph:
    """
    Create the complete ticket-to-code workflow graph.
    
    Flow:
    1. Investigate → Decide if code needed
    2. Analyze → Extract requirements
    3. Plan → Design architecture
    4. RAG (Tests) → Get product behavior knowledge
    5. RAG (Code) → Get architecture knowledge (INDEPENDENT)
    6. Generate Tests → TDD: Tests first!
    7. Generate Code → Code to satisfy tests
    8. Build → Compile everything
    9. Test → Run AI-generated tests
    10. Fix → Loop back if failures
    """
    # Initialize agents
    agents = WorkflowAgents(workspace_path)
    
    # Create graph
    workflow = StateGraph(TicketToCodeState)
    
    # Add nodes (each phase)
    workflow.add_node("investigate", lambda s: investigate_node(s, agents))
    workflow.add_node("analyze", lambda s: analyze_node(s, agents))
    workflow.add_node("plan", lambda s: plan_node(s, agents))
    workflow.add_node("rag_tests", lambda s: rag_for_tests_node(s, agents))
    workflow.add_node("rag_code", lambda s: rag_for_code_node(s, agents))
    workflow.add_node("generate_tests", lambda s: generate_tests_node(s, agents))
    workflow.add_node("generate_code", lambda s: generate_code_node(s, agents))
    workflow.add_node("build", lambda s: build_node(s, agents))
    workflow.add_node("test", lambda s: test_node(s, agents))
    workflow.add_node("fix_build", lambda s: fix_build_errors_node(s, agents))
    workflow.add_node("fix_test", lambda s: fix_test_failures_node(s, agents))
    
    # Set entry point
    workflow.set_entry_point("investigate")
    
    # Add edges
    workflow.add_conditional_edges("investigate", should_generate_code)
    workflow.add_edge("analyze", "plan")
    workflow.add_edge("plan", "rag_tests")
    workflow.add_edge("rag_tests", "rag_code")
    workflow.add_edge("rag_code", "generate_tests")
    workflow.add_edge("generate_tests", "generate_code")
    workflow.add_edge("generate_code", "build")
    
    # Retry loops
    workflow.add_conditional_edges("build", check_build_status)
    workflow.add_edge("fix_build", "build")  # Loop back to build
    
    workflow.add_conditional_edges("test", check_test_status)
    workflow.add_edge("fix_test", "build")  # Loop back to build (rebuild after fix)
    
    return workflow


# ============================================================================
# MAIN EXECUTION FUNCTION
# ============================================================================

def run_autonomous_workflow_langgraph(
    ticket: ValueEdgeTicket,
    workspace_path: str,
    max_retry_attempts: int = 3
) -> TicketToCodeState:
    """
    Run the complete autonomous workflow using LangGraph.
    
    Args:
        ticket: ValueEdge ticket to process
        workspace_path: Path to C# project
        max_retry_attempts: Max attempts to fix build/test errors
        
    Returns:
        Final workflow state with results
    """
    logger.info(f"🚀 Starting LangGraph workflow for: {ticket.ticket_id}")
    
    # Create graph
    workflow = create_ticket_to_code_graph(workspace_path)
    
    # Compile with memory (state persistence)
    memory = MemorySaver()
    app = workflow.compile(checkpointer=memory)
    
    # Initial state
    initial_state: TicketToCodeState = {
        "ticket": ticket,
        "workspace_path": workspace_path,
        "max_retry_attempts": max_retry_attempts,
        "investigation_result": None,
        "requirements": None,
        "architectural_plan": None,
        "test_rag_context": None,
        "code_rag_context": None,
        "generated_tests": None,
        "generated_code": None,
        "build_result": None,
        "test_result": None,
        "retry_attempt": 0,
        "last_error_type": None,
        "status": "initialized",
        "errors": [],
        "start_time": datetime.now(),
        "end_time": None
    }
    
    # Execute workflow
    config = {"configurable": {"thread_id": ticket.ticket_id}}
    
    try:
        # Stream execution (optional - can use invoke() for non-streaming)
        final_state = None
        for state in app.stream(initial_state, config):
            final_state = state
            logger.info(f"  Status: {state.get('status', 'unknown')}")
        
        # Mark completion
        final_state["end_time"] = datetime.now()
        duration = (final_state["end_time"] - final_state["start_time"]).total_seconds()
        
        logger.info(
            f"✅ Workflow completed in {duration:.2f}s\n"
            f"   Status: {final_state['status']}\n"
            f"   Tests: {len(final_state.get('generated_tests', []))} files\n"
            f"   Code: {len(final_state.get('generated_code', []))} files\n"
            f"   Retry attempts: {final_state['retry_attempt']}"
        )
        
        return final_state
        
    except Exception as e:
        logger.error(f"❌ Workflow failed: {e}", exc_info=True)
        raise


# ============================================================================
# VISUALIZATION HELPER
# ============================================================================

def visualize_workflow(workspace_path: str, output_path: str = "workflow_graph.png"):
    """
    Generate visual diagram of the workflow.
    
    Args:
        workspace_path: Project path
        output_path: Where to save diagram
    """
    workflow = create_ticket_to_code_graph(workspace_path)
    app = workflow.compile()
    
    # Generate Mermaid diagram
    mermaid = app.get_graph().draw_mermaid()
    
    print("Workflow Graph (Mermaid):")
    print(mermaid)
    
    # Could also generate PNG with graphviz
    # app.get_graph().draw_png(output_path)
    
    return mermaid
