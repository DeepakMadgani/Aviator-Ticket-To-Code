"""Logging filters for suppressing or lowering specific log messages."""

import logging
import sys

#
# Route uncaught exceptions through the logging system so they appear
# with proper severity instead of raw stderr tracebacks.  This applies
# to every process that imports this module (API server, Celery worker,
# beat scheduler, migration producer, etc.).
#
_original_excepthook = sys.excepthook


def _excepthook(exc_type: type, exc_value: BaseException, exc_tb: object) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        _original_excepthook(exc_type, exc_value, exc_tb)
        return
    logging.getLogger("aviator").critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_tb))


sys.excepthook = _excepthook

#
# Lower httpx loglevel, used by google sdk
#
logging.getLogger("httpx").setLevel("WARN")

#
# suppress specific google_genai info log messages
#
logging.getLogger("google_genai.models").addFilter(
    lambda record: "AFC is enabled with max remote calls" not in record.getMessage()
)
logging.getLogger("langchain_google_genai._function_utils").addFilter(
    lambda record: "Key 'additionalProperties' is not supported in schema" not in record.getMessage()
)

#
# Suppress specific langfuse warnings about missing public key
#
logging.getLogger("langfuse").addFilter(
    lambda record: (
        "Authentication error: Langfuse client initialized without public_key. Client will be disabled. Provide a public_key parameter or set LANGFUSE_PUBLIC_KEY environment variable."
        not in record.getMessage()
    )
)
logging.getLogger("langfuse").addFilter(
    lambda record: (
        "Auth check failed: Client not properly initialized. Error: 'Langfuse' object has no attribute 'api'"
        not in record.getMessage()
    )
)

#
# Suppress specific opentelemetry instrumentation warnings
#
logging.getLogger("opentelemetry.instrumentation.instrumentor").addFilter(
    lambda record: "Attempting to instrument while already instrumented" not in record.getMessage()
)
