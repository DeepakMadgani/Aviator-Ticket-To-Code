"""Test suite for chart_generator module."""

import json

from aviator.tools.chart_generator import (
    create_vega_lite_spec,
    generate_vega_lite_chart,
)


class TestCreateVegaLiteSpecParameters:
    """Tests for create_vega_lite_spec function parameters."""

    def test_bar_chart_parameters(self):
        """Test creating a bar chart with valid parameters."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"category": "A", "value": 10}, {"category": "B", "value": 20}],
            x_field="category",
            y_field="value",
            x_type="nominal",
            y_type="quantitative",
        )
        assert spec["mark"] == "bar"
        assert len(spec["data"]["values"]) == 2
        assert spec["encoding"]["x"]["field"] == "category"
        assert spec["encoding"]["y"]["field"] == "value"

    def test_line_chart_parameters(self):
        """Test creating a line chart with temporal data."""
        spec = create_vega_lite_spec(
            chart_type="line",
            data=[{"date": "2024-01-01", "value": 100}],
            x_field="date",
            y_field="value",
            x_type="temporal",
            y_type="quantitative",
            title="Time Series",
        )
        assert spec["mark"] == "line"
        assert spec["title"] == "Time Series"
        assert spec["encoding"]["x"]["type"] == "temporal"

    def test_pie_chart_parameters(self):
        """Test creating a pie chart."""
        spec = create_vega_lite_spec(
            chart_type="pie",
            data=[{"category": "A", "value": 30}, {"category": "B", "value": 70}],
            x_field="category",
            y_field="value",
        )
        assert spec["mark"] == "arc"

    def test_histogram_parameters(self):
        """Test creating a histogram."""
        spec = create_vega_lite_spec(
            chart_type="histogram",
            data=[{"value": 5}, {"value": 10}, {"value": 15}],
            x_field="value",
            x_type="quantitative",
        )
        assert spec["mark"] == "bar"
        assert spec["encoding"]["x"]["bin"] is True

    def test_default_dimensions(self):
        """Test default width and height values."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 2}],
        )
        assert spec["width"] == 400
        assert spec["height"] == 300

    def test_custom_dimensions(self):
        """Test custom width and height."""
        spec = create_vega_lite_spec(
            chart_type="scatter",
            data=[{"x": 1, "y": 2}],
            width=800,
            height=600,
        )
        assert spec["width"] == 800
        assert spec["height"] == 600

    def test_color_field(self):
        """Test color field encoding."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 2, "category": "A"}],
            x_field="x",
            y_field="y",
            color_field="category",
        )
        assert "color" in spec["encoding"]
        assert spec["encoding"]["color"]["field"] == "category"

    def test_axis_titles(self):
        """Test custom axis titles."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 2}],
            x_field="x",
            y_field="y",
            x_title="X Axis Label",
            y_title="Y Axis Label",
        )
        assert spec["encoding"]["x"]["title"] == "X Axis Label"
        assert spec["encoding"]["y"]["title"] == "Y Axis Label"


class TestCreateVegaLiteSpec:
    """Tests for create_vega_lite_spec function."""

    def test_bar_chart_spec(self):
        """Test generating a bar chart specification."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"category": "A", "value": 10}, {"category": "B", "value": 20}],
            x_field="category",
            y_field="value",
            x_type="nominal",
            y_type="quantitative",
            title="Bar Chart",
        )

        assert spec["$schema"] == "https://vega.github.io/schema/vega-lite/v5.json"
        assert spec["mark"] == "bar"
        assert spec["title"] == "Bar Chart"
        assert spec["width"] == 400
        assert spec["height"] == 300
        assert spec["data"]["values"] == [{"category": "A", "value": 10}, {"category": "B", "value": 20}]
        assert spec["encoding"]["x"]["field"] == "category"
        assert spec["encoding"]["x"]["type"] == "nominal"
        assert spec["encoding"]["y"]["field"] == "value"
        assert spec["encoding"]["y"]["type"] == "quantitative"

    def test_line_chart_spec(self):
        """Test generating a line chart specification."""
        spec = create_vega_lite_spec(
            chart_type="line",
            data=[{"x": 1, "y": 10}, {"x": 2, "y": 20}],
            x_field="x",
            y_field="y",
            x_type="quantitative",
            y_type="quantitative",
        )

        assert spec["mark"] == "line"
        assert spec["encoding"]["x"]["field"] == "x"
        assert spec["encoding"]["y"]["field"] == "y"

    def test_area_chart_spec(self):
        """Test generating an area chart specification."""
        spec = create_vega_lite_spec(
            chart_type="area",
            data=[{"x": 1, "y": 10}],
            x_field="x",
            y_field="y",
        )

        assert spec["mark"] == "area"

    def test_scatter_chart_spec(self):
        """Test generating a scatter chart specification."""
        spec = create_vega_lite_spec(
            chart_type="scatter",
            data=[{"x": 1, "y": 10}, {"x": 2, "y": 20}],
            x_field="x",
            y_field="y",
            x_type="quantitative",
            y_type="quantitative",
        )

        assert spec["mark"] == "point"

    def test_point_chart_spec(self):
        """Test generating a point chart specification."""
        spec = create_vega_lite_spec(
            chart_type="point",
            data=[{"x": 1, "y": 10}],
            x_field="x",
            y_field="y",
        )

        assert spec["mark"] == "point"

    def test_boxplot_spec(self):
        """Test generating a boxplot specification."""
        spec = create_vega_lite_spec(
            chart_type="boxplot",
            data=[{"category": "A", "value": 10}],
            x_field="category",
            y_field="value",
        )

        assert spec["mark"] == "boxplot"

    def test_pie_chart_spec(self):
        """Test generating a pie chart specification."""
        spec = create_vega_lite_spec(
            chart_type="pie",
            data=[{"category": "A", "value": 30}, {"category": "B", "value": 70}],
            x_field="category",
            y_field="value",
        )

        # Pie charts use arc mark and special encoding
        assert spec["mark"] == "arc"
        assert "theta" in spec["encoding"]
        assert spec["encoding"]["theta"]["field"] == "value"
        assert "color" in spec["encoding"]
        assert spec["encoding"]["color"]["field"] == "category"

    def test_histogram_spec(self):
        """Test generating a histogram specification."""
        spec = create_vega_lite_spec(
            chart_type="histogram",
            data=[{"value": 5}, {"value": 10}, {"value": 15}],
            x_field="value",
            x_type="quantitative",
        )

        # Histogram uses bar mark with binning
        assert spec["mark"] == "bar"
        assert spec["encoding"]["x"]["bin"] is True
        assert spec["encoding"]["x"]["field"] == "value"
        assert spec["encoding"]["y"]["aggregate"] == "count"
        assert spec["encoding"]["y"]["type"] == "quantitative"

    def test_color_encoding(self):
        """Test color field is properly encoded (non-pie charts)."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 10, "category": "A"}],
            x_field="x",
            y_field="y",
            color_field="category",
        )

        assert "color" in spec["encoding"]
        assert spec["encoding"]["color"]["field"] == "category"
        assert spec["encoding"]["color"]["type"] == "nominal"

    def test_custom_axis_titles(self):
        """Test custom axis titles are included."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 10}],
            x_field="x",
            y_field="y",
            x_title="Custom X",
            y_title="Custom Y",
        )

        assert spec["encoding"]["x"]["title"] == "Custom X"
        assert spec["encoding"]["y"]["title"] == "Custom Y"

    def test_histogram_with_custom_y_title(self):
        """Test histogram with custom y-axis title."""
        spec = create_vega_lite_spec(
            chart_type="histogram",
            data=[{"value": 5}],
            x_field="value",
            y_title="Frequency",
        )

        assert spec["encoding"]["y"]["title"] == "Frequency"

    def test_no_title(self):
        """Test spec without title."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 10}],
            x_field="x",
            y_field="y",
        )

        assert "title" not in spec

    def test_default_type_inference(self):
        """Test that default types are inferred when not provided."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": "A", "y": 10}],
            x_field="x",
            y_field="y",
            # No x_type or y_type specified
        )

        # Default x_type should be nominal, y_type should be quantitative
        assert spec["encoding"]["x"]["type"] == "nominal"
        assert spec["encoding"]["y"]["type"] == "quantitative"

    def test_temporal_data_type(self):
        """Test temporal data type encoding."""
        spec = create_vega_lite_spec(
            chart_type="line",
            data=[{"date": "2024-01-01", "value": 100}],
            x_field="date",
            y_field="value",
            x_type="temporal",
        )

        assert spec["encoding"]["x"]["type"] == "temporal"

    def test_ordinal_data_type(self):
        """Test ordinal data type encoding."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"category": "low", "value": 10}],
            x_field="category",
            y_field="value",
            x_type="ordinal",
        )

        assert spec["encoding"]["x"]["type"] == "ordinal"


class TestGenerateVegaLiteChart:
    """Tests for generate_vega_lite_chart tool function."""

    def test_generate_bar_chart(self):
        """Test generating a bar chart using the tool."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "bar",
                "data": [{"x": "A", "y": 10}, {"x": "B", "y": 20}],
                "x_field": "x",
                "y_field": "y",
                "x_type": "nominal",
                "y_type": "quantitative",
            }
        )

        assert isinstance(result, str)
        spec = json.loads(result)
        assert spec["mark"] == "bar"
        assert spec["encoding"]["x"]["field"] == "x"

    def test_generate_line_chart(self):
        """Test generating a line chart using the tool."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "line",
                "data": [{"x": 1, "y": 10}],
                "x_field": "x",
                "y_field": "y",
            }
        )

        spec = json.loads(result)
        assert spec["mark"] == "line"

    def test_generate_pie_chart(self):
        """Test generating a pie chart using the tool."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "pie",
                "data": [{"category": "A", "value": 50}],
                "x_field": "category",
                "y_field": "value",
            }
        )

        spec = json.loads(result)
        assert spec["mark"] == "arc"

    def test_generate_histogram(self):
        """Test generating a histogram using the tool."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "histogram",
                "data": [{"value": 5}, {"value": 10}],
                "x_field": "value",
            }
        )

        spec = json.loads(result)
        assert spec["mark"] == "bar"
        assert spec["encoding"]["x"]["bin"] is True

    def test_generate_with_title(self):
        """Test generating a chart with a title."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "bar",
                "data": [{"x": "A", "y": 10}],
                "x_field": "x",
                "y_field": "y",
                "title": "Test Chart",
            }
        )

        spec = json.loads(result)
        assert spec["title"] == "Test Chart"

    def test_generate_with_custom_dimensions(self):
        """Test generating a chart with custom dimensions."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "bar",
                "data": [{"x": "A", "y": 10}],
                "x_field": "x",
                "y_field": "y",
                "width": 600,
                "height": 400,
            }
        )

        spec = json.loads(result)
        assert spec["width"] == 600
        assert spec["height"] == 400

    def test_generate_with_color_field(self):
        """Test generating a chart with color encoding."""
        result = generate_vega_lite_chart.invoke(
            {
                "chart_type": "scatter",
                "data": [{"x": 1, "y": 10, "category": "A"}],
                "x_field": "x",
                "y_field": "y",
                "color_field": "category",
            }
        )

        spec = json.loads(result)
        assert "color" in spec["encoding"]
        assert spec["encoding"]["color"]["field"] == "category"

    def test_generate_all_chart_types(self):
        """Test generating all supported chart types."""
        chart_types = ["bar", "line", "area", "scatter", "pie", "point", "boxplot", "histogram"]
        data = [{"x": "A", "y": 10}, {"x": "B", "y": 20}]

        for chart_type in chart_types:
            result = generate_vega_lite_chart.invoke(
                {
                    "chart_type": chart_type,
                    "data": data,
                    "x_field": "x",
                    "y_field": "y",
                }
            )

            assert isinstance(result, str)
            spec = json.loads(result)
            assert "$schema" in spec
            assert "data" in spec
            assert "mark" in spec


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_empty_data_array(self):
        """Test handling of empty data array."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[],
            x_field="x",
            y_field="y",
        )

        # Should still create a valid spec, just with empty data
        assert spec["data"]["values"] == []
        assert "$schema" in spec

    def test_missing_fields(self):
        """Test chart generation with missing field names."""
        spec = create_vega_lite_spec(
            chart_type="bar",
            data=[{"x": 1, "y": 2}],
            # No x_field or y_field specified
        )

        # Should still generate a spec, but encoding may be minimal
        assert "encoding" in spec
        assert isinstance(spec["encoding"], dict)

    def test_pie_chart_without_y_field(self):
        """Test pie chart with missing y_field."""
        spec = create_vega_lite_spec(
            chart_type="pie",
            data=[{"category": "A"}],
            x_field="category",
            # No y_field
        )

        # Should still generate valid spec
        assert spec["mark"] == "arc"

    def test_histogram_without_fields(self):
        """Test histogram with minimal configuration."""
        spec = create_vega_lite_spec(
            chart_type="histogram",
            data=[{"value": 5}],
            # No x_field specified
        )

        # Should still have count aggregation for y
        assert spec["encoding"]["y"]["aggregate"] == "count"

    def test_histogram_with_x_title_only(self):
        """Test histogram with only x_title provided."""
        spec = create_vega_lite_spec(
            chart_type="histogram",
            data=[{"value": 5}, {"value": 10}],
            x_field="value",
            x_title="Values",
        )

        assert spec["encoding"]["x"]["title"] == "Values"
        assert spec["mark"] == "bar"

    def test_exception_handling_in_generate(self):
        """Test exception handling in generate_vega_lite_chart function."""
        from unittest.mock import patch

        # Mock create_vega_lite_spec to raise an exception
        with patch("aviator.tools.chart_generator.create_vega_lite_spec", side_effect=Exception("Test error")):
            result = generate_vega_lite_chart.invoke(
                {
                    "chart_type": "bar",
                    "data": [{"x": 1, "y": 2}],
                    "x_field": "x",
                    "y_field": "y",
                }
            )

        # Should return error message instead of JSON spec
        assert "Error generating chart" in result
        assert "Test error" in result
