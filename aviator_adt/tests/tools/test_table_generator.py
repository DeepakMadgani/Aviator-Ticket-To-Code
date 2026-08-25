"""Test suite for table_generator module."""

from aviator.tools.table_generator import (
    create_markdown_table,
    generate_markdown_table,
)


class TestCreateMarkdownTable:
    """Tests for create_markdown_table function."""

    def test_create_simple_table(self):
        """Test creating a simple markdown table."""
        headers = ["Name", "Age"]
        rows = [{"Name": "Alice", "Age": "30"}, {"Name": "Bob", "Age": "25"}]
        result = create_markdown_table(headers, rows)

        assert "| Name | Age |" in result
        assert "| Alice | 30 |" in result
        assert "| Bob | 25 |" in result
        assert "|---|---|" in result

    def test_create_table_empty_inputs(self):
        """Test creating table with empty inputs."""
        result = create_markdown_table([], [])
        assert result == ""

    def test_create_table_with_missing_values(self):
        """Test creating table with missing values in rows."""
        headers = ["Name", "Age", "City"]
        rows = [{"Name": "Alice", "Age": "30"}]  # Missing City
        result = create_markdown_table(headers, rows)

        assert "| Alice | 30 |  |" in result


class TestGenerateMarkdownTableTool:
    """Tests for generate_markdown_table tool function."""

    def test_generate_basic_table(self):
        """Test generating a basic table using the tool."""
        result = generate_markdown_table.invoke(
            {
                "headers": ["Quarter", "Revenue"],
                "rows": [
                    {"Quarter": "Q1", "Revenue": "$100K"},
                    {"Quarter": "Q2", "Revenue": "$120K"},
                ],
            }
        )

        assert isinstance(result, str)
        assert "Quarter" in result
        assert "Q1" in result

    def test_generate_table_with_title(self):
        """Test generating a table with title."""
        result = generate_markdown_table.invoke(
            {
                "headers": ["Item", "Count"],
                "rows": [{"Item": "A", "Count": "10"}],
                "title": "Inventory Report",
            }
        )

        assert "Inventory Report" in result

    def test_generate_table_empty_headers(self):
        """Test error handling with empty headers."""
        result = generate_markdown_table.invoke(
            {
                "headers": [],
                "rows": [],
            }
        )

        assert "Error" in result

    def test_generate_table_multiple_rows(self):
        """Test generating table with multiple rows."""
        result = generate_markdown_table.invoke(
            {
                "headers": ["Name", "Score"],
                "rows": [
                    {"Name": "Alice", "Score": "95"},
                    {"Name": "Bob", "Score": "87"},
                    {"Name": "Charlie", "Score": "92"},
                ],
            }
        )

        assert "Alice" in result
        assert "Bob" in result
        assert "Charlie" in result


class TestTableGenerationIntegration:
    """Integration tests for table generation."""

    def test_end_to_end_table_generation(self):
        """Test complete table generation workflow."""
        # Simulate user request
        headers = ["Challenge", "Solution", "Impact"]
        rows = [
            {"Challenge": "Scalability", "Solution": "Cloud infrastructure", "Impact": "High"},
            {"Challenge": "Performance", "Solution": "Optimization techniques", "Impact": "Medium"},
        ]

        result = generate_markdown_table.invoke(
            {
                "headers": headers,
                "rows": rows,
                "title": "Technical Improvements",
            }
        )

        # Verify table structure
        assert "Technical Improvements" in result
        assert "Challenge" in result
        assert "Solution" in result
        assert "Impact" in result
        assert "Scalability" in result
        assert "Cloud infrastructure" in result
