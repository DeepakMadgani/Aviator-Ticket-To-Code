"""Implement Content Aviator ADT."""

import asyncio
import logging
import os
import sys

# Set service name before any library (e.g. Langfuse) creates a TracerProvider,
# so all providers pick up the correct resource attribute from the start.
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") and not os.environ.get("OTEL_SERVICE_NAME"):
    os.environ["OTEL_SERVICE_NAME"] = "aviator-api"

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor
from opentelemetry.instrumentation.langchain import LangchainInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.threading import ThreadingInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import aviator.logfilter  # noqa: F401
from aviator import api
from aviator.api.mcp import router as mcp_router
from aviator.database import database_manager
from aviator.metrics import socket_counter
from aviator.plugins import (
    list_plugins,
    load_routers,
    load_startup_extension,
)
from aviator.settings import settings
from aviator.utils.limiter import limiter
from aviator.utils.version_util import get_aviator_version
from aviator.vector_store import vector_store

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Configure OTLP tracing exporter for the FastAPI server.
# Sends OpenTelemetry spans to an OTLP-compatible collector (e.g. Jaeger)
# when OTEL_EXPORTER_OTLP_ENDPOINT is set. Langfuse registers
# its own TracerProvider during import, so we add an extra span processor
# to the existing provider instead of replacing it.
if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider as _SdkTracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _existing = trace.get_tracer_provider()
    # Unwrap ProxyTracerProvider to reach the real SDK provider
    _real = getattr(_existing, "_real_tracer_provider", _existing)
    if isinstance(_real, _SdkTracerProvider):
        _real.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        # No SDK provider yet — create one (OTEL_SERVICE_NAME is already set above)
        from opentelemetry.sdk.resources import Resource

        _provider = _SdkTracerProvider(resource=Resource.create())
        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(_provider)

tracer = trace.get_tracer(__name__)
logger = logging.getLogger("aviator")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:  # noqa: ARG001
    """Handle application startup and shutdown events."""
    with tracer.start_as_current_span("startup"):
        logger.info(
            "Content Aviator (%s) starting...",
            get_aviator_version(),
        )
        logger.info(
            "LLM Provider: %s, Model: %s, Assistant Model: %s",
            settings.llm_provider.value,
            settings.llm_model,
            settings.llm_model_assistant,
        )

        logger.info("Installed plugins: %s", list_plugins())

        with tracer.start_as_current_span("setup_vector_store"):
            # Setup checkpointer, vector store and state graph upon startup
            vector_store.setup_vector_store()

        # Skip database setup during tests (similar to how vector_store respects VECTOR_STORE=memory)
        if not os.environ.get("TESTING"):
            logger.info("Setting up application database tables...")
            database_manager.setup_database()

        # Only setup database if using postgres checkpointer
        if settings.checkpointer == "postgres":
            with tracer.start_as_current_span("setup_database"):
                # Setup database tables upon startup
                database_manager.setup_database()
        else:
            logger.info("Skipping database setup (checkpointer=%s)", settings.checkpointer)
        # Initialize usage tracking DB pool if enabled
        if settings.usage_tracking_enabled:
            with tracer.start_as_current_span("setup_usage_tracking"):
                from aviator.services.usage_tracking.db import usage_tracking_db

                logger.info("Initializing usage tracking database...")
                await usage_tracking_db.initialize()

    for pkg, name, extension in load_startup_extension():
        logger.info("Executing startup extension: %s:%s", pkg, name)
        with tracer.start_as_current_span(f"startup_extension:{pkg}:{name}"):
            extension()

    yield

    # Shutdown: close usage tracking pool
    if settings.usage_tracking_enabled:
        try:
            from aviator.services.usage_tracking.db import usage_tracking_db

            await usage_tracking_db.close()
        except Exception:
            logger.exception("Error closing usage tracking DB pool")

    logger.info("application_shutdown")
    with tracer.start_as_current_span("shutdown"):
        try:
            await database_manager.reset()
            logger.info("database_pool_closed")
        except Exception as e:
            logger.error("Error closing database pool: %s", e)


app = FastAPI(
    title="Content Aviator",
    version=get_aviator_version(),
    summary="OpenText Content Aviator",
    description=f"""
## Installed plugins:
{list_plugins()}
    """,
    root_path=settings.root_path,
    openapi_url=settings.openapi_url,
    lifespan=lifespan,
    redoc_url=False,
    contact={
        "name": "OpenText Content Aviator",
        "url": "https://www.opentext.com/aviator-ai/content-aviator",
    },
    openapi_tags=[
        {
            "name": "chat",
            "description": "Chat Endpoint - Access various chat and chat history functionalities and interactions.",
        },
        {
            "name": "embeddings",
            "description": "Embeddings Endpoint - Handle embedding requests and metadata operations.",
        },
        {
            "name": "rag",
            "description": "RAG Endpoint - Perform retrieval-augmented generation queries.",
        },
        {
            "name": "graph",
            "description": "Graph Endpoint - Access and visualize the state graph.",
        },
        {
            "name": "general",
            "description": "General Endpoint - Health checks and other general functionalities.",
        },
        {
            "name": "tenants",
            "description": "Tenant Management - Create, list, get, and delete tenant schemas for multi-tenancy.",
        },
        {
            "name": "stats",
            "description": "Usage Statistics - Query usage metrics and semantic search footprint per tenant.",
        },
    ],
)

# Setup rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Instrument FastAPI app with Prometheus
metrics_instrumentator = Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    should_instrument_requests_inprogress=True,
    excluded_handlers=["/metrics", "/health", "/img/*"],
    inprogress_name="inprogress",
    inprogress_labels=True,
)
metrics_instrumentator.add(socket_counter.metrics())
metrics_instrumentator.instrument(app)
metrics_instrumentator.expose(app, include_in_schema=True, should_gzip=True, tags=["general"])

ThreadingInstrumentor().instrument()
if settings.enable_debug_tracing:
    # Instrument OpenTelemetry
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/metrics,/health,/img/*,/docs")
    RequestsInstrumentor().instrument()
    PsycopgInstrumentor().instrument()
    GoogleGenAiSdkInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument()
    LangchainInstrumentor().instrument()

app.include_router(api.a2a_router)
app.include_router(api.websockets.router)
app.include_router(api.v1.router)
app.include_router(api.graph.router)
app.include_router(api.stats.router)
app.include_router(api.queue.router)
app.include_router(api.projects_router)  # PHASE 1 - Project management
app.include_router(mcp_router)

if settings.multi_tenant_enabled and settings.tenant_api_enabled:
    app.include_router(api.tenants.router)

if settings.dev_tools:
    app.include_router(api.devtools.router)

for router in load_routers():
    app.include_router(router)

# Serve images from the `static/img` directory at the `/img` URL path
app.mount(
    "/img",
    StaticFiles(packages=["aviator"]),
    name="img",
)


# Allow CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def reject_request_body_for_forbidden_methods(request: Request, call_next: callable) -> JSONResponse | None:
    """Middleware: reject any GET request that carries a non-empty body."""
    if request.method == "GET":
        body = await request.body()
        if body and body.strip():
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": f"{request.method} request body is not allowed"},
            )
    return await call_next(request)


# Serve the static HTML file
@app.get("/", include_in_schema=True, tags=["general"])
@limiter.limit("15/minute")
async def get_chat(request: Request, ui: str = "jato", ticket: str = "", auth: str = "false") -> HTMLResponse:
    """Serve a debugging HTML page for chat interaction."""

    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    template = "chat.html" if ui == "jato" else "chat_alt.html"

    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "ticket": ticket,
            "version": get_aviator_version(),
            "content_system_url": str(settings.content_system_url),
            "enable_authentication": auth.lower(),
        },
        status_code=status.HTTP_200_OK,
    )


@app.get("/health", include_in_schema=False, tags=["general"])
async def health() -> JSONResponse:
    """Readiness and Liveness check endpoint."""
    return JSONResponse(content={"status": "healthy"}, status_code=status.HTTP_200_OK)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation exceptions and return a user-friendly JSON response."""

    errors = []
    for err in exc.errors():
        field = " -> ".join(str(loc) for loc in err.get("loc", []))
        msg = err.get("msg", "")
        # Append expected format hint for date validation errors
        if "valid date" in msg.lower():
            msg += " (expected format: YYYY-MM-DD)"
        # Produce a human-readable message per field
        errors.append(f"Invalid value for '{field}': {msg}")

    detail = "; ".join(errors) if errors else str(exc)
    logger.error("%s: %s", request, detail)
    content = {"status_code": 10422, "message": detail, "data": None}
    return JSONResponse(content=content, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)


## Start the API Server
def run_api() -> None:
    """Start the FastAPI Webserver."""

    if os.getenv("SENTRY_DSN"):
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.langchain import LangchainIntegration
        from sentry_sdk.integrations.langgraph import LanggraphIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        logger.info("Initializing Sentry SDK")
        sentry_sdk.init(
            dsn=os.getenv("SENTRY_DSN"),
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            integrations=[
                FastApiIntegration(),
                LangchainIntegration(),
                LanggraphIntegration(),
                SqlalchemyIntegration(),
            ],
        )

    # Check if Logfolder exists, if not create it.
    Path(settings.logfolder).mkdir(parents=True, exist_ok=True)

    # Configure Logging for uvicorn
    log_config = uvicorn.config.LOGGING_CONFIG

    # Add thread_id filter to the logging configuration
    log_config["filters"] = {
        "thread_id_filter": {
            "()": "aviator.utils.logging_context.ThreadIdLoggingFilter",
        }
    }

    # Stdout
    log_config["formatters"]["standard"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(levelprefix)s [%(name)s] [%(threadName)s] [%(thread_id)s] %(message)s",
        "use_colors": True,
    }

    log_config["formatters"]["logfile"] = {
        "()": "uvicorn.logging.DefaultFormatter",
        "fmt": "%(asctime)s %(levelname)s [%(name)s] [%(threadName)s] [%(thread_id)s] %(message)s",
        "datefmt": "%d-%b-%Y %H:%M:%S",
        "use_colors": True,
    }

    log_config["formatters"]["accesslog"] = {
        "()": "uvicorn.logging.AccessFormatter",
        "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        "use_colors": False,
    }

    log_config["handlers"]["console"] = {
        "formatter": "standard",
        "class": "logging.StreamHandler",
        "stream": "ext://sys.stdout",
        "filters": ["thread_id_filter"],
    }

    log_config["handlers"]["file"] = {
        "formatter": "logfile",
        "class": "logging.FileHandler",
        "filename": str(Path(settings.logfolder) / "aviator.log"),
        "mode": "a",
    }

    log_config["handlers"]["accesslog"] = {
        "formatter": "accesslog",
        "class": "logging.FileHandler",
        "filename": str(Path(settings.logfolder) / "access.log"),
        "mode": "a",
    }

    log_config["loggers"]["uvicorn.access"]["handlers"] = ["access", "accesslog"]

    log_config["loggers"]["root"] = {
        "level": settings.loglevel,
        "handlers": ["console", "file"],
    }

    uvicorn.run(
        "aviator.main:app",
        host=settings.bind_address,
        port=settings.bind_port,
        workers=settings.workers,
        reload=settings.reload,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_config=log_config,
    )


if __name__ == "__main__":
    run_api()
    # uvicorn.run(app, host="0.0.0.0", port=3000, reload=True)
