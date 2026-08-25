ARG BASEIMAGE=artifactory.otxlab.net/cs-csai-docker-dev/aviator:latest

FROM ${BASEIMAGE} AS build
ADD --chown=otuser:otuser . /plugins
WORKDIR /plugins
RUN --mount=type=cache,target=/home/otuser/.cache/uv,uid=1000,gid=1000 \
    uv build


FROM ${BASEIMAGE}
ARG VERSION=0.1.0

COPY --from=build --chown=otuser:otuser /plugins/dist /plugins

WORKDIR /app
ENV PYTHONPATH=/app

# Install the plugin (ADT is already in the base image)
RUN --mount=type=cache,target=/home/otuser/.cache/uv,uid=1000,gid=1000 \
    uv pip install /plugins/aviator_plugin_sample-${VERSION}.tar.gz 

CMD ["python", "-m", "aviator.main"]


# -------- debug --------
FROM ${BASEIMAGE} AS debug
ARG VERSION=0.1.0

# Copy built plugin from build stage
COPY --from=build --chown=otuser:otuser /plugins/dist /plugins

WORKDIR /app
ENV PYTHONPATH=/app

# Install debugging tools and plugin
RUN --mount=type=cache,target=/home/otuser/.cache/uv,uid=1000,gid=1000 \
    uv pip install debugpy ipdb pdb-clone && \
    uv pip install /plugins/aviator_plugin_sample-${VERSION}.tar.gz

# Debug environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LOG_LEVEL=DEBUG

# Expose debug port
EXPOSE 5678

# Default debug command with remote debugging
CMD [  "python",  "-Xfrozen_modules=off",  "-m", "debugpy",  "--listen", "0.0.0.0:5678",  "--wait-for-client",  "-m", "aviator.main"]


# -------- test --------
FROM ${BASEIMAGE} AS test
ARG VERSION=0.1.0

# Copy built plugin from build stage
COPY --from=build --chown=otuser:otuser /plugins/dist /plugins

WORKDIR /app
ENV PYTHONPATH=/app

# Install the plugin and test dependencies
RUN --mount=type=cache,target=/home/otuser/.cache/uv,uid=1000,gid=1000 \
    uv pip install /plugins/aviator_plugin_sample-${VERSION}.tar.gz && \
    uv pip install pytest pytest-asyncio pytest-mock pytest-cov httpx

# Copy test files and source code
COPY --chown=otuser:otuser tests /app/tests
COPY --chown=otuser:otuser pytest.ini /app/pytest.ini
COPY --chown=otuser:otuser src /app/src

# Run tests by default
CMD ["pytest", "tests/", "-v", "--cov=aviator_plugin_sample", "--cov-report=term-missing"]
