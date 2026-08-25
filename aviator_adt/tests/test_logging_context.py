"""Test thread_id logging context functionality."""

import logging
from unittest.mock import patch

from aviator.utils.logging_context import ThreadIdLoggingFilter


def test_thread_id_logging_filter_without_graph_context():
    """Test that logging filter uses default '-' when not in graph context."""
    # Create a log record
    logger = logging.getLogger("test_logger")
    record = logger.makeRecord(
        name="test_logger",
        level=logging.INFO,
        fn="test.py",
        lno=1,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    # Apply the filter (not in graph context)
    log_filter = ThreadIdLoggingFilter()
    result = log_filter.filter(record)

    # Verify the filter returns True and adds default thread_id
    assert result is True
    assert hasattr(record, "thread_id")
    assert record.thread_id == "-"


def test_thread_id_logging_filter_with_graph_context():
    """Test that logging filter extracts thread_id from LangGraph context."""
    # Mock get_config to simulate being in a graph execution
    mock_config = {
        "configurable": {
            "thread_id": "test-thread-789",
        }
    }

    with patch("langgraph.config.get_config", return_value=mock_config):
        # Create a log record
        logger = logging.getLogger("test_logger")
        record = logger.makeRecord(
            name="test_logger",
            level=logging.INFO,
            fn="test.py",
            lno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Apply the filter
        log_filter = ThreadIdLoggingFilter()
        result = log_filter.filter(record)

        # Verify the filter returns True and extracts thread_id from config
        assert result is True
        assert hasattr(record, "thread_id")
        assert record.thread_id == "test-thread-789"


def test_thread_id_logging_filter_with_empty_config():
    """Test that logging filter handles empty config gracefully."""
    # Mock get_config to return empty config
    with patch("langgraph.config.get_config", return_value={}):
        # Create a log record
        logger = logging.getLogger("test_logger")
        record = logger.makeRecord(
            name="test_logger",
            level=logging.INFO,
            fn="test.py",
            lno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Apply the filter
        log_filter = ThreadIdLoggingFilter()
        result = log_filter.filter(record)

        # Verify the filter returns True and uses default
        assert result is True
        assert hasattr(record, "thread_id")
        assert record.thread_id == "-"


def test_thread_id_logging_filter_with_runtime_error():
    """Test that logging filter handles RuntimeError when get_config is called outside context."""
    # Mock get_config to raise RuntimeError (happens when called outside graph context)
    with patch("langgraph.config.get_config", side_effect=RuntimeError("No context")):
        # Create a log record
        logger = logging.getLogger("test_logger")
        record = logger.makeRecord(
            name="test_logger",
            level=logging.INFO,
            fn="test.py",
            lno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Apply the filter
        log_filter = ThreadIdLoggingFilter()
        result = log_filter.filter(record)

        # Verify the filter returns True and uses default despite error
        assert result is True
        assert hasattr(record, "thread_id")
        assert record.thread_id == "-"
