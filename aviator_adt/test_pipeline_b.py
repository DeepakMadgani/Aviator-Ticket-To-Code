import os
import sys
import io
from pathlib import Path

# Fix stdout encoding for powershell
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Setup path
sys.path.append(os.path.abspath(r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src"))
sys.path.append(os.path.abspath(r"C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src"))

class MockResponse:
    def __init__(self, content):
        self.content = content

class MockLLM:
    def invoke(self, *args, **kwargs):
        # Return a valid JSON response so parsers don't crash
        return MockResponse('{"bug_fix": true, "code_changes_needed": true, "analysis": "mock"}')
    def run(self, *args, **kwargs):
        return '{"bug_fix": true, "code_changes_needed": true, "analysis": "mock"}'

from ticket_to_code.agents.ticket_analyzer import TicketAnalyzerAgent
def mock_analyze_ticket(*args, **kwargs):
    from ticket_to_code.agents.ticket_analyzer import StructuredRequirements
    return StructuredRequirements(
        bug_fix=True,
        code_changes_needed=True,
        functional_requirements=["Fix UI"],
        technical_requirements=[],
        edge_cases=[],
        acceptance_tests=[],
        affected_components=["UI"]
    )
TicketAnalyzerAgent.analyze_ticket = mock_analyze_ticket

from ticket_to_code.agents.planning_agent import PlanningAgent
def mock_create_plan(*args, **kwargs):
    from ticket_to_code.models import ArchitecturalPlan, LocalizedTask
    task = LocalizedTask(
        file_path="src/app/modules/deliverables/deliverable/deliverable.component.ts",
        description="Fix the UI",
        rationale="Fix the bug",
        is_new_file=False
    )
    return ArchitecturalPlan(
        ticket_id="TEST-123",
        tasks=[task],
        rationale="Shadow mode mock plan",
        pattern="MVC",
        affected_modules=["UI"]
    )
PlanningAgent.create_plan = mock_create_plan

import aviator.services.llm
aviator.services.llm.LLMRegistry.get_llm = staticmethod(lambda *args, **kwargs: MockLLM())
aviator.services.llm.LLMRegistry.get_model = staticmethod(lambda *args, **kwargs: MockLLM())

class MockPriority:
    def __init__(self, value):
        self.value = value

# Mock ticket class to avoid import errors from other repos
class MockTicket:
    def __init__(self, ticket_id, title, description, type, status):
        self.ticket_id = ticket_id
        self.title = title
        self.description = description
        self.type = type
        self.status = status
        self.priority = MockPriority("High")
        self.component = "UI"
        self.test_steps = []
        self.acceptance_criteria = []
        self.severity = "High"
        self.created_by = "test"
        self.assigned_to = "test"
        self.labels = []

from ticket_to_code.workflow import run_autonomous_workflow_langgraph

ticket = MockTicket(
    ticket_id="TEST-123",
    title="Align deliverable reviewers UI",
    description="The padding is wrong on the deliverable reviewers component.",
    type="Defect",
    status="New"
)

workspace = r"C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample"

print(f"Running LangGraph workflow for {ticket.ticket_id}...")
try:
    final_state = run_autonomous_workflow_langgraph(ticket, workspace, technology="dotnet")

    print("\n--- WORKFLOW EXECUTION COMPLETE ---")
    print("Status:", final_state.get("status"))

    # Verify pipeline B artifacts
    print("\n--- PIPELINE B ARTIFACTS ---")
    print("Behavior Graph present:", final_state.get("behavior_graph") is not None)
    print("Ownership Report present:", final_state.get("ownership_report") is not None)
    print("Behavior Plan present:", final_state.get("behavior_plan") is not None)
    print("Validation Result present:", final_state.get("plan_validation_result") is not None)
    print("Shadow Metrics present:", final_state.get("shadow_metrics") is not None)

    # Verify shadow_metrics.json
    metrics_path = os.path.join(workspace, "shadow_metrics.json")
    if os.path.exists(metrics_path):
        print(f"\nshadow_metrics.json FOUND at {metrics_path}")
        with open(metrics_path, "r") as f:
            print("Contents:")
            print(f.read())
    else:
        print("\nshadow_metrics.json NOT FOUND!")
except Exception as e:
    import traceback
    traceback.print_exc()

