"""Logging context management for automatically injecting thread_id into log records.

This module provides a logging filter that automatically adds thread_id to all log records
within a LangGraph execution context. It retrieves the thread_id from the RunnableConfig
that LangGraph provides during graph execution.

The filter also supports dynamic log level control per execution via configurable.log_level.

The filter works automatically without any manual setup - just apply it to your loggers
and thread_id will be extracted from the execution context.

Usage:
    Pass log_level in the configurable to enable DEBUG/verbose logging for a specific execution:
        configurable={"thread_id": "my-thread-123", "log_level": "DEBUG"}
"""

import logging


class ThreadIdLoggingFilter(logging.Filter):
    """Logging filter that automatically injects thread_id into log records.

    This filter retrieves thread_id from LangGraph's execution context (via get_config).
    When logs are emitted during graph execution, the filter extracts the thread_id
    from the RunnableConfig and adds it to the log record.

    Also supports dynamic log level control:
    - Check for configurable.log_level and set root logger level if present
    - This allows per-execution log level control (e.g., DEBUG for specific threads)

    Outside of graph execution context, thread_id defaults to "-".
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add thread_id to the log record from LangGraph execution context.

        Also enables dynamic log level control via configurable.log_level.

        Args:
            record: The LogRecord to modify

        Returns:
            bool: Always True (we never filter out records)

        """
        thread_id = None

        # Try to get thread_id from LangGraph execution context
        try:
            from langgraph.config import get_config, get_stream_writer

            config = get_config()
            if config:
                configurable = config.get("configurable", {})
                thread_id = configurable.get("thread_id")

                # Log the log message as a LangGraph event with thread_id context (if available)
                if configurable.get("log_level") is not None:
                    log_level = getattr(logging, configurable.get("log_level"), logging.INFO)
                    if record.levelno >= log_level:
                        writer = get_stream_writer()
                        writer(
                            {
                                "event": "debug",
                                "level": record.levelname,
                                "message": record.getMessage(),
                            }
                        )

        except (RuntimeError, ImportError, KeyError):
            # Not in a LangGraph context or LangGraph not available
            pass

        # Set the attribute (use "-" if not in graph context)
        record.thread_id = thread_id if thread_id is not None else "-"

        return True
