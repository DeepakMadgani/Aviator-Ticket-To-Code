"""Quick routing test for the unified skills.py."""
import sys
import types

# Stub the dependency so skills.py can import without the full aviator stack.
# We need to set up the full package hierarchy as real packages.
ttc = types.ModuleType("ticket_to_code")
ttc.__path__ = ["src/ticket_to_code"]  # Make it a package
ttc_llm = types.ModuleType("ticket_to_code.llm_utils")
ttc_llm.expand_ticket_keywords = lambda t: {"current_state_literals": [], "desired_state_literals": []}
ttc_reasoning = types.ModuleType("ticket_to_code.reasoning")
ttc_reasoning.__path__ = ["src/ticket_to_code/reasoning"]  # Make it a package

sys.modules["ticket_to_code"] = ttc
sys.modules["ticket_to_code.llm_utils"] = ttc_llm
sys.modules["ticket_to_code.reasoning"] = ttc_reasoning

# Now import skills.py normally
sys.path.insert(0, "src")
from ticket_to_code.reasoning.skills import select_skill, Skill, LiteralInvestigation

print("Import OK")

tests = [
    ("", "Add a new REST API endpoint with swagger docs", "rest_endpoint"),
    ("", "Add a Flyway migration to add a column", "db_migration"),
    ("", "Create a new Angular component in xchange-ui", "ui_component"),
    ("", "Fix NPE regression in saga compensate", "bugfix"),
    ("version_bump", "Bump version from 26.3 to 26.4", "version_change"),
    ("", "Something totally unrelated", "cc4e_default"),
    ("access_control", "Restrict coordinator role for organization", "access_control"),
    ("", "Add RabbitMQ consumer for event stream", "messaging_event"),
    ("", "Add a ConstraintValidator for field length", "validator"),
    ("", "Update application.yaml config for timeout", "configuration"),
]

passed = 0
for ticket_type, text, expected in tests:
    skill = select_skill(ticket_type, text)
    status = "PASS" if skill.name == expected else "FAIL"
    if status == "PASS":
        passed += 1
    print(f"  [{status}] '{text[:50]}...' -> {skill.name} (expected: {expected})")

print(f"\n{passed}/{len(tests)} tests passed")
