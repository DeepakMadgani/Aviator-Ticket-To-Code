"""AgentCardBuilder — builds the A2A Agent Card and its skills.

Merges what was previously ``builder.py`` and ``skill_builder.py`` into one
cohesive class hierarchy:

  - ``SkillBuilder``      — converts agent tools → :class:`~aviator.a2a.models.Skill` list.
  - ``AgentCardBuilder``  — uses ``SkillBuilder`` and assembles the full
                            :class:`~aviator.a2a.models.AgentCard`.

Both classes accept their dependencies via constructor injection, keeping
them independently testable.
"""

from fastapi import Request

from aviator.a2a.models import (
    AgentCapabilities,
    AgentCard,
    SecurityScheme,
    Skill,
    SupportedInterface,
)
from aviator.a2a.util.utils import to_skill_id, to_skill_name
from aviator.graph import aviator
from aviator.settings import settings
from aviator.utils.version_util import get_aviator_version

# ── SkillBuilder ──────────────────────────────────────────────────────────────


class SkillBuilder:
    """Builds a deduplicated list of A2A ``Skill`` objects from agent tools."""

    async def build(self, agent: object) -> list[Skill]:
        """Return skills derived from *agent*'s registered tools.

        Args:
            agent: Any object exposing an async ``get_tools()`` method
                   (e.g. ``ContentAviatorAgent``).

        Returns:
            Deduplicated list of :class:`~aviator.a2a.models.Skill` objects.

        """
        tools = await agent.get_tools()
        skills: list[Skill] = []
        seen: set[str] = set()

        for tool in tools:
            tool_name = getattr(tool, "name", "")
            if not tool_name:
                continue

            skill_id = to_skill_id(tool_name)
            if skill_id in seen:
                continue

            raw_desc = str(getattr(tool, "description", "") or "").strip()
            tool_tags = getattr(tool, "tags", [])
            tool_examples = getattr(tool, "examples", [])

            skills.append(
                Skill(
                    id=skill_id,
                    name=to_skill_name(tool_name),
                    description=raw_desc or f"Use the {tool_name} tool.",
                    inputModes=["text/plain"],
                    outputModes=["text/plain"],
                    examples=list(tool_examples) if isinstance(tool_examples, (list, tuple)) else [],
                    tags=list(tool_tags) if isinstance(tool_tags, (list, tuple)) else ["tool"],
                )
            )
            seen.add(skill_id)

        return skills


# ── AgentCardBuilder ──────────────────────────────────────────────────────────


class AgentCardBuilder:
    """Builds a fully-populated :class:`~aviator.a2a.models.AgentCard`.

    Args:
        skill_builder: Optional custom :class:`SkillBuilder`. Defaults to
            a fresh instance if not provided.

    """

    def __init__(self, skill_builder: SkillBuilder | None = None) -> None:
        """Initialise with an optional custom :class:`SkillBuilder`."""
        self._skill_builder = skill_builder or SkillBuilder()

    async def build(self, request: Request) -> AgentCard:
        """Assemble the agent card for the given request context."""
        # Use the external-facing URL (original scheme + netloc from the request)
        # so that supportedInterfaces advertises the correct public https:// URL.
        external_base = f"{request.url.scheme}://{request.url.netloc}"
        if settings.root_path:
            external_base = f"{external_base}{settings.root_path}"
        skills = await self._skill_builder.build(aviator)

        # ── Security schemes ──────────────────────────────────────────────────
        # Two schemes are supported, matching require_authentication():
        #   1. Bearer token  — OAuth2 JWT issued by OTDS (Authorization header)
        #   2. OTCS ticket   — OpenText Content Server ticket (otcsticket header)

        security_schemes: dict[str, SecurityScheme] = {
            "otcsTicket": SecurityScheme(
                type="apiKey",
                description="OpenText Content Server ticket passed in the 'otcsticket' request header.",
                name="otcsticket",
                location="header",
            ),
        }
        security: list[dict[str, list[str]]] = [{"otcsTicket": []}]

        if settings.otds_url:
            security_schemes["bearerAuth"] = SecurityScheme(
                type="http",
                scheme="bearer",
                bearerFormat="JWT",
                description="Bearer token issued by OTDS. Obtain it from your tenant's OTDS instance and pass it as 'Authorization: Bearer <token>'.",
            )
            security.append({"bearerAuth": []})

        return AgentCard(
            protocolVersions=["1.0"],
            name="Content Aviator Agent",
            version=get_aviator_version(),
            description=("Enterprise AI-powered document analysis and retrieval-augmented generation agent."),
            supportedInterfaces=[
                SupportedInterface(
                    url=f"{external_base}/agent",
                    protocolBinding="JSON-RPC",
                    protocolVersion="1.0",
                ),
                SupportedInterface(
                    url=f"{external_base}/agent",
                    protocolBinding="HTTP+JSON",
                    protocolVersion="1.0",
                ),
            ],
            capabilities=AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=False,
            ),
            securitySchemes=security_schemes,
            security=security,
            defaultInputModes=["text/plain"],
            defaultOutputModes=["text/plain"],
            skills=skills,
        )
