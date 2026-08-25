"""CC4E project-specific Skill playbooks.

A ``Skill`` is a project-specific playbook that the autonomous reasoning engine
consults *before* it starts coding a ticket. It injects our exact architectural
guardrails, mandatory validators, and preferred tool (capability) sequences that
were derived from a direct analysis of the C:/CC4E codebase.

Design notes (grounded in the actual CC4E repo):
  * Java services (project-service, issues-service, ene-load-service, sagas-service,
    bim-gateway) are Spring Boot 3.x / Java 17, mostly Gradle. area-service and
    se-connector-apis are Maven.
  * Layered structure: controller -> service(/impl) -> repository(JPA) -> entity/dto,
    with validators/, sagas/handlers, messaging/, events/, gql/client, config/, exceptions/.
  * REST controllers use springdoc-openapi (io.swagger.v3.oas.annotations) and HATEOAS
    model assemblers. New code must use constructor injection (@RequiredArgsConstructor).
  * Schema changes go through Flyway migrations in src/main/resources/db/migration.
  * Async flows use Spring Cloud Stream + RabbitMQ (StreamBridge) and the sagas-lib.
  * xchange-ui is Angular 19 (NgModules + some standalone), selector prefix ``se-``,
    ngx-translate for i18n, RxJS/@ngrx-signals for state.

Capability names below map to the engine's real tool registry
(see reasoning/tools.py): recall_memory, brain_query, recall_feature, find_owner,
search_code, grep, rag, read_file, write_patch, run_build, verify_outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Skill:
    """A project-specific playbook that intercepts a class of tickets.

    Attributes:
        name: Short, descriptive identifier (e.g. "rest_endpoint").
        matches_keys: Keywords that commonly appear in tickets of this type. Used
            to intercept/route a ticket to this playbook.
        capabilities: Prioritized list of engine tools the agent should use, in order.
        validators: Strict conditions that MUST hold before the job is "done".
        guidance: Architectural guardrails grounded in the CC4E codebase.
        common_mistakes: Recurring errors engineers make in CC4E for this task.
        priority: Higher wins when more than one skill matches a ticket.
    """

    name: str
    matches_keys: List[str]
    capabilities: List[str]
    validators: List[str]
    guidance: str
    common_mistakes: List[str]
    priority: int = 100

    def matches(self, ticket_text: str) -> int:
        """Return the number of ``matches_keys`` present in ``ticket_text``."""
        text = (ticket_text or "").lower()
        return sum(1 for key in self.matches_keys if key.lower() in text)


# ---------------------------------------------------------------------------
# Skill playbooks — one per major CC4E ticket type.
# ---------------------------------------------------------------------------

REST_ENDPOINT = Skill(
    name="rest_endpoint",
    matches_keys=["endpoint", "rest api", "controller", "swagger", "route", "http"],
    capabilities=[
        "brain_query",
        "recall_feature",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "New handler methods carry a springdoc @Operation (and @ApiResponses) annotation; "
        "the controller keeps its @Tag.",
        "Request bodies use @Valid DTOs (never raw entities); URL paths come from the "
        "constants/URLMappings package, not hardcoded strings.",
        "The owning *-service Gradle/Maven build compiles cleanly and JUnit 5 + Mockito "
        "tests cover the success and failure paths.",
    ],
    guidance=(
        "Endpoints belong in the service's controller package (e.g. "
        "project-service/src/main/java/com/opentext/bim/projectservice/controller or "
        "issues-service .../rest/controllers) and MUST delegate to a service interface — "
        "never call repositories, GraphQL clients, or messaging directly from a controller. "
        "Use constructor injection with @RequiredArgsConstructor + private final fields (do "
        "not extend the legacy @Autowired field-injection pattern) and shape responses with "
        "HATEOAS EntityModel + the existing model assemblers rather than returning JPA entities."
    ),
    common_mistakes=[
        "Returning JPA entities straight from the controller instead of a DTO/HATEOAS model assembler.",
        "Hardcoding URL path strings and HTTP status codes instead of reusing URLMappings/constants.",
        "Adding @Autowired field injection to a new controller instead of constructor injection.",
    ],
    priority=120,
)

DB_MIGRATION = Skill(
    name="db_migration",
    matches_keys=["migration", "schema", "flyway", "column", "table", "entity"],
    capabilities=[
        "brain_query",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "A new, forward-only Flyway script exists under src/main/resources/db/migration "
        "named V{next-version}__Description.sql; no previously applied migration is edited.",
        "The matching JPA @Entity mapping is updated in lock-step with the migration and "
        "uses explicit @Table/@Column mappings (no reliance on implicit Hibernate naming).",
        "spring.jpa.hibernate.ddl-auto stays 'none' in non-test profiles and the service build passes.",
    ],
    guidance=(
        "Every schema change is a NEW Flyway migration in the service's "
        "src/main/resources/db/migration folder (PostgreSQL) — never modify an already-applied "
        "V-script. Keep the migration and the JPA entity consistent, and access data through "
        "Spring Data JPA repositories or Specifications/Criteria API. Never concatenate raw SQL/JPQL "
        "with user input."
    ),
    common_mistakes=[
        "Editing an existing applied V-script instead of adding a new higher-versioned migration.",
        "Relying on ddl-auto=update to change the schema instead of writing a Flyway script.",
        "Building JPQL/SQL by string concatenation with user input instead of parameterized "
        "queries or Specifications (SQL-injection risk).",
    ],
    priority=120,
)

UI_COMPONENT = Skill(
    name="ui_component",
    matches_keys=["component", "angular", "xchange-ui", "template", "ngx-translate", "ui"],
    capabilities=[
        "brain_query",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "The component ships the full file set (.ts/.html/.scss/.spec.ts), uses the 'se-' "
        "selector prefix, and is declared/exported by the correct NgModule (or is a standalone "
        "component if the surrounding area already uses loadComponent).",
        "No hardcoded user-facing strings — all labels/messages use ngx-translate keys under "
        "src/assets/i18n; API access lives in a service (HttpClient/GraphQL), not the component.",
        "Every manual RxJS subscription is cleaned up (currObs[] or takeUntilDestroyed), and "
        "'ng build' / 'ng lint' / 'ng test' pass.",
    ],
    guidance=(
        "UI work lives in c:/CC4E/xchange-ui/src/app (feature modules) or src/shared (reusable "
        "components). Match the local pattern of the feature you edit — module-based stays "
        "module-based. Keep components focused on view orchestration and push API/GraphQL/business "
        "logic into the shared services under src/app/modules/shared/services. Surface failures "
        "through NotificationService.notifyError, never console-only, and reuse existing constants."
    ),
    common_mistakes=[
        "Hardcoding display strings instead of adding ngx-translate keys.",
        "Calling HttpClient/GraphQL directly from the component instead of a shared service.",
        "Leaking manual subscriptions by not unsubscribing in ngOnDestroy.",
    ],
    priority=120,
)

SAGA_HANDLER = Skill(
    name="saga_handler",
    matches_keys=["saga", "handler", "compensate", "workflow", "orchestration"],
    capabilities=[
        "brain_query",
        "recall_feature",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "The handler extends the correct abstract saga handler and implements process(), "
        "compensate(), backup(), and the required-fields methods.",
        "compensate() is idempotent and safe to call when process() never ran (state flags "
        "are checked first); no mutable request state is stored on the singleton bean.",
        "JUnit 5 + Mockito tests cover both the happy path and the compensation flow.",
    ],
    guidance=(
        "Saga step handlers live in the service's sagas/handlers package and are built on the "
        "sagas-lib. Handlers are Spring singletons — keep per-request state out of instance fields. "
        "Use constants for saga field names, log entry/exit with the commandId, and make "
        "compensate() fully idempotent."
    ),
    common_mistakes=[
        "Storing mutable request/step state in singleton handler fields (thread-safety bug).",
        "Writing a compensate() that assumes process() completed, so it fails or double-reverts.",
        "Inlining saga field-name strings instead of reusing the constants package.",
    ],
    priority=115,
)

MESSAGING_EVENT = Skill(
    name="messaging_event",
    matches_keys=["kafka", "rabbitmq", "event", "stream", "producer", "consumer", "message"],
    capabilities=[
        "brain_query",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "Producers use StreamBridge.send() with a binding-name constant, and every new binding "
        "has a matching entry in application.yaml.",
        "Consumers follow the Consumer<Message<T>> pattern and handle deserialization/bad-payload "
        "failures without crashing the listener.",
        "The service build passes and status/state transitions driven by the message stay consistent.",
    ],
    guidance=(
        "Async messaging uses Spring Cloud Stream over RabbitMQ (not raw Kafka clients). Put "
        "producers/consumers in the service's messaging package, drive them with StreamBridge and "
        "binding-name constants, and register any new binding in application.yaml. In pipeline "
        "services like ene-load-service, preserve the Upload->Validate->Process->Report "
        "producer/subscriber pairing."
    ),
    common_mistakes=[
        "Adding a new binding in code but forgetting the corresponding application.yaml entry.",
        "Letting a malformed/undeserializable message crash the consumer instead of handling it.",
        "Hardcoding binding/channel names instead of using the messaging constants.",
    ],
    priority=110,
)

VALIDATOR = Skill(
    name="validator",
    matches_keys=["validator", "validation", "constraint", "@valid", "field length"],
    capabilities=[
        "brain_query",
        "recall_feature",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "Validation logic is stateless with no side effects (no service calls or DB writes) and "
        "lives in the validators/ package.",
        "Declarative field validation uses Jakarta Validation annotations (@NotNull/@NotBlank/@Size) "
        "or a proper ConstraintValidator; field limits reuse the shared FieldLengths constants.",
        "JUnit 5 + Mockito/AssertJ tests cover valid and invalid inputs.",
    ],
    guidance=(
        "Custom validators belong in the service's validators/ package and must be reusable and "
        "stateless (extend ConstraintValidator, or the existing SagaRequestValidator base for saga "
        "flows). Never perform service calls or database writes inside a validator, and reuse the "
        "shared FieldLengths / constants rather than inlining magic numbers."
    ),
    common_mistakes=[
        "Putting side effects (service or repository calls) inside validator logic.",
        "Hardcoding field-length limits instead of using the shared FieldLengths constants.",
        "Duplicating validation already available via Jakarta Validation annotations.",
    ],
    priority=110,
)

BUGFIX = Skill(
    name="bugfix",
    matches_keys=["bug", "fix", "npe", "error", "exception", "regression", "defect"],
    capabilities=[
        "recall_memory",
        "brain_query",
        "brain_get_root_causes",
        "find_owner",
        "read_file",
        "grep",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "The fix targets the root-cause file/path only; no opportunistic unrelated refactors.",
        "Exceptions are handled explicitly (log-and-rethrow or map to the bim-commons-lib "
        "exception hierarchy) — no empty catch blocks, System.out.println, or printStackTrace.",
        "A regression test reproduces the bug and passes after the fix; the owning build is green.",
    ],
    guidance=(
        "Constrain edits to the root-cause path and keep a clear symptom->cause->fix chain. "
        "Route errors through the existing exception hierarchy and @ControllerAdvice handlers "
        "(GenericControllerExceptionHandler), use parameterized @Slf4j logging, and never leak "
        "stack traces, tokens, or DB constraint names to callers."
    ),
    common_mistakes=[
        "Swallowing exceptions with an empty catch or logging without rethrowing/handling.",
        "Fixing the symptom in a consumer file while the real owner/root-cause file is untouched.",
        "Bundling unrelated cleanup into the bugfix, expanding the blast radius.",
    ],
    priority=90,
)

# Catch-all so the engine can still guide *any* ticket it does not specifically match.
DEFAULT = Skill(
    name="cc4e_default",
    matches_keys=[],
    capabilities=[
        "recall_memory",
        "brain_query",
        "find_owner",
        "read_file",
        "write_patch",
        "run_build",
        "verify_outcome",
    ],
    validators=[
        "Changes are minimal and scoped to the owner file(s) discovered for the ticket.",
        "The owning service build passes (Gradle for most services, Maven for area-service / "
        "se-connector-apis; 'ng build' for xchange-ui).",
        "No plaintext secrets, no System.out.println/console-only logging, no swallowed exceptions.",
    ],
    guidance=(
        "Respect the CC4E layered architecture (controller -> service -> repository -> entity for "
        "Java services; feature-module components + shared services for xchange-ui). Prefer additive, "
        "backward-compatible changes, reuse existing services/constants/shared libraries "
        "(bim-commons-lib, sagas-lib, ot2-auth), and keep the diff as small as the ticket allows."
    ),
    common_mistakes=[
        "Editing consumer files when the real change belongs in an owner file.",
        "Broad refactors or new abstractions the ticket did not ask for.",
        "Bypassing existing shared services/constants and reimplementing them.",
    ],
    priority=0,
)


# All skills, highest priority first. DEFAULT stays last as the catch-all.
SKILLS: List[Skill] = [
    REST_ENDPOINT,
    DB_MIGRATION,
    UI_COMPONENT,
    SAGA_HANDLER,
    MESSAGING_EVENT,
    VALIDATOR,
    BUGFIX,
    DEFAULT,
]


def match_skill(ticket_text: str) -> Skill:
    """Intercept a ticket and return the best-fitting Skill playbook.

    The skill with the most keyword hits wins; ties break on ``priority``. When no
    skill matches, the ``DEFAULT`` catch-all playbook is returned so that *every*
    ticket type still receives CC4E guardrails.
    """
    best = DEFAULT
    best_hits = 0
    for skill in SKILLS:
        if skill is DEFAULT:
            continue
        hits = skill.matches(ticket_text)
        if hits > best_hits or (hits == best_hits and hits > 0 and skill.priority > best.priority):
            best, best_hits = skill, hits
    return best
