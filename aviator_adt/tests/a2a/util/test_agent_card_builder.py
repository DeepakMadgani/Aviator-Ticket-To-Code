"""Tests for SkillBuilder and AgentCardBuilder."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from aviator.a2a.models import AgentCard, Skill
from aviator.a2a.util.agent_card_builder import AgentCardBuilder, SkillBuilder

# ── SkillBuilder ──────────────────────────────────────────────────────────────


def _make_tool(name, description="A tool", tags=None, examples=None):
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.tags = tags or []
    tool.examples = examples or []
    return tool


class TestSkillBuilder:
    """Tests for the SkillBuilder class."""

    @pytest.mark.asyncio
    async def test_builds_skills_from_tools(self):
        agent = MagicMock()
        agent.get_tools = AsyncMock(return_value=[_make_tool("rag_query", "Search documents")])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert len(skills) == 1
        assert skills[0].id == "rag_query"
        assert skills[0].name == "Rag Query"
        assert skills[0].description == "Search documents"

    @pytest.mark.asyncio
    async def test_deduplicates_skills(self):
        """Two tools that normalise to the same skill id should yield one skill."""
        agent = MagicMock()
        agent.get_tools = AsyncMock(
            return_value=[
                _make_tool("rag_query"),
                _make_tool("rag_query"),  # duplicate
            ]
        )

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert len(skills) == 1

    @pytest.mark.asyncio
    async def test_tool_without_name_skipped(self):
        agent = MagicMock()
        tool_no_name = MagicMock()
        tool_no_name.name = ""
        agent.get_tools = AsyncMock(return_value=[tool_no_name, _make_tool("valid_tool")])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert len(skills) == 1
        assert skills[0].id == "valid_tool"

    @pytest.mark.asyncio
    async def test_empty_tools_returns_empty_list(self):
        agent = MagicMock()
        agent.get_tools = AsyncMock(return_value=[])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert skills == []

    @pytest.mark.asyncio
    async def test_skill_uses_fallback_description(self):
        agent = MagicMock()
        tool = _make_tool("my_tool", description="")
        agent.get_tools = AsyncMock(return_value=[tool])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert skills[0].description == "Use the my_tool tool."

    @pytest.mark.asyncio
    async def test_skill_carries_tags_and_examples(self):
        agent = MagicMock()
        tool = _make_tool("chart_tool", tags=["visualization"], examples=["Draw a chart"])
        agent.get_tools = AsyncMock(return_value=[tool])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert skills[0].tags == ["visualization"]
        assert skills[0].examples == ["Draw a chart"]

    @pytest.mark.asyncio
    async def test_skill_default_input_output_modes(self):
        agent = MagicMock()
        agent.get_tools = AsyncMock(return_value=[_make_tool("search")])

        builder = SkillBuilder()
        skills = await builder.build(agent)

        assert skills[0].inputModes == ["text/plain"]
        assert skills[0].outputModes == ["text/plain"]


# ── AgentCardBuilder ──────────────────────────────────────────────────────────


def _make_request(scheme="https", netloc="myagent.example.com"):
    req = MagicMock()
    req.url = MagicMock()
    req.url.scheme = scheme
    req.url.netloc = netloc
    return req


class TestAgentCardBuilder:
    """Tests for the AgentCardBuilder class."""

    @pytest.mark.asyncio
    async def test_builds_agent_card(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "")
        monkeypatch.setattr(settings, "otds_url", None)

        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request())

        assert isinstance(card, AgentCard)
        assert card.name == "Content Aviator Agent"
        assert card.capabilities.streaming is True

    @pytest.mark.asyncio
    async def test_supported_interfaces_use_external_url(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "")
        monkeypatch.setattr(settings, "otds_url", None)

        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request(scheme="https", netloc="myagent.example.com"))

        for iface in card.supportedInterfaces:
            assert "myagent.example.com" in iface.url

    @pytest.mark.asyncio
    async def test_security_schemes_include_otcs_ticket(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "")
        monkeypatch.setattr(settings, "otds_url", None)

        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request())

        assert "otcsTicket" in card.securitySchemes

    @pytest.mark.asyncio
    async def test_bearer_auth_added_when_otds_configured(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "")
        monkeypatch.setattr(settings, "otds_url", "https://otds.example.com")

        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request())

        assert "bearerAuth" in card.securitySchemes

    @pytest.mark.asyncio
    async def test_root_path_included_in_interfaces(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "/aviator")
        monkeypatch.setattr(settings, "otds_url", None)

        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request())

        for iface in card.supportedInterfaces:
            assert "/aviator" in iface.url

    @pytest.mark.asyncio
    async def test_default_skill_builder_used_when_not_injected(self):
        """AgentCardBuilder creates its own SkillBuilder when none is injected."""
        builder = AgentCardBuilder()
        assert builder._skill_builder is not None

    @pytest.mark.asyncio
    async def test_skills_included_in_card(self, monkeypatch):
        from aviator.settings import settings

        monkeypatch.setattr(settings, "root_path", "")
        monkeypatch.setattr(settings, "otds_url", None)

        skill = Skill(id="rag", name="Rag", description="Search")
        mock_skill_builder = MagicMock()
        mock_skill_builder.build = AsyncMock(return_value=[skill])

        builder = AgentCardBuilder(skill_builder=mock_skill_builder)
        card = await builder.build(_make_request())

        assert len(card.skills) == 1
        assert card.skills[0].id == "rag"
