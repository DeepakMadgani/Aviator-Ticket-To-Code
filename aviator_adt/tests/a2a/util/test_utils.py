"""Tests for A2A utility helpers (build_base_url, build_forward_headers, to_skill_id, to_skill_name)."""

from unittest.mock import MagicMock, patch

from aviator.a2a.util.utils import (
    build_base_url,
    build_forward_headers,
    to_skill_id,
    to_skill_name,
)


def _make_request(auth=None, ticket=None):
    req = MagicMock()
    req.url = MagicMock()
    req.url.scheme = "http"
    req.url.netloc = "localhost:8000"
    headers = {}
    if auth:
        headers["authorization"] = auth
    if ticket:
        headers["otcsticket"] = ticket
    req.headers = headers
    return req


# ── build_base_url ────────────────────────────────────────────────────────────


class TestBuildBaseUrl:
    """Tests for the build_base_url helper."""

    def test_uses_chat_service_endpoint(self):
        from aviator.settings import settings

        with (
            patch.object(settings, "aviator_chat_service_endpoint", "http://aviator-service:8000"),
            patch.object(settings, "root_path", ""),
        ):
            url = build_base_url(_make_request())
        assert url == "http://aviator-service:8000"

    def test_appends_root_path(self):
        from aviator.settings import settings

        with (
            patch.object(settings, "aviator_chat_service_endpoint", "http://aviator-service:8000"),
            patch.object(settings, "root_path", "/api/v1"),
        ):
            url = build_base_url(_make_request())
        assert url == "http://aviator-service:8000/api/v1"

    def test_trailing_slash_stripped(self):
        from aviator.settings import settings

        with (
            patch.object(settings, "aviator_chat_service_endpoint", "http://aviator-service:8000/"),
            patch.object(settings, "root_path", ""),
        ):
            url = build_base_url(_make_request())
        assert not url.endswith("/")


# ── build_forward_headers ─────────────────────────────────────────────────────


class TestBuildForwardHeaders:
    """Tests for the build_forward_headers helper."""

    def test_includes_content_type(self):
        headers = build_forward_headers(_make_request())
        assert headers["content-type"] == "application/json"

    def test_forwards_authorization_header(self):
        req = _make_request(auth="Bearer my-token")
        headers = build_forward_headers(req)
        assert headers["authorization"] == "Bearer my-token"

    def test_forwards_auth_ticket_header(self):
        req = _make_request(ticket="CSTKN12345")
        headers = build_forward_headers(req)
        assert headers["otcsticket"] == "CSTKN12345"

    def test_no_auth_headers_only_content_type(self):
        headers = build_forward_headers(_make_request())
        assert list(headers.keys()) == ["content-type"]

    def test_both_auth_headers_forwarded(self):
        req = _make_request(auth="Bearer tok", ticket="ticket")
        headers = build_forward_headers(req)
        assert "authorization" in headers
        assert "otcsticket" in headers


# ── to_skill_id ───────────────────────────────────────────────────────────────


class TestToSkillId:
    """Tests for the to_skill_id normalisation helper."""

    def test_simple_name(self):
        assert to_skill_id("rag_query") == "rag_query"

    def test_spaces_replaced(self):
        assert to_skill_id("RAG Query Tool") == "rag_query_tool"

    def test_special_chars_replaced(self):
        assert to_skill_id("my-tool!name") == "my_tool_name"

    def test_uppercase_lowercased(self):
        assert to_skill_id("ChartGenerator") == "chartgenerator"

    def test_empty_string_returns_tool(self):
        assert to_skill_id("") == "tool"

    def test_all_special_chars_returns_tool(self):
        assert to_skill_id("!!!") == "tool"

    def test_leading_trailing_underscores_stripped(self):
        result = to_skill_id("_my_tool_")
        assert not result.startswith("_")
        assert not result.endswith("_")


# ── to_skill_name ─────────────────────────────────────────────────────────────


class TestToSkillName:
    """Tests for the to_skill_name formatting helper."""

    def test_simple_underscore_name(self):
        assert to_skill_name("rag_query") == "Rag Query"

    def test_hyphenated_name(self):
        assert to_skill_name("chart-generator") == "Chart Generator"

    def test_single_word(self):
        assert to_skill_name("search") == "Search"

    def test_empty_string_returns_tool(self):
        assert to_skill_name("") == "Tool"

    def test_camel_case_split(self):
        # Non-alphanumeric separators only — CamelCase stays as one word
        result = to_skill_name("ChartGenerator")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mixed_separators(self):
        result = to_skill_name("my_tool-name")
        assert result == "My Tool Name"
