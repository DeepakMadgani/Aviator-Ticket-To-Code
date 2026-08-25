# syntax=docker/dockerfile:1.4
ARG BASEIMAGE=artifactory.otxlab.net/dockerhub/redhat/ubi9-minimal:latest

FROM artifactory.otxlab.net/dockerhub/node:alpine AS build-node

COPY .npmrc package.json package-lock.json /aviator_adt/
WORKDIR /aviator_adt

RUN mkdir -p /aviator_adt/src/aviator/statics

RUN npm ci && ls

# hadolint ignore=DL3006
FROM ${BASEIMAGE}

ARG VERSION=0.0.0
ARG BUILD=0000

# Install system dependencies
RUN microdnf update -y && \
    microdnf clean all

# Set working directory
WORKDIR /aviator_adt

# Create user and group (rarely changes)
RUN groupadd -g 1000 otuser && useradd otuser -g otuser -m -d /home/otuser
RUN chown -R otuser:otuser /home/otuser /aviator_adt

# Add container files (entrypoint, probes, etc.)
COPY --chown=otuser:otuser container/ /
RUN chmod +x /entrypoint.sh /probes/*.py

# Install uv package manager (rarely changes)
COPY --chown=otuser:otuser --from=artifactory.otxlab.net/ghcr.io/astral-sh/uv:latest /uv /bin/

# Switch to non-root user
USER otuser

# Copy only dependency files first (changes less frequently than source code)
COPY --chown=otuser:otuser pyproject.toml uv.lock README.md .python-version ./

# Create minimal package structure for dependency installation
RUN mkdir -p src/aviator && touch src/aviator/__init__.py

# Install dependencies with cache mount for faster rebuilds
# This layer will be cached unless pyproject.toml or uv.lock changes
RUN --mount=type=cache,target=/home/otuser/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked --no-dev && uv version ${VERSION}-b${BUILD}

# Expose scripts and python from the venv as default
ENV VIRTUAL_ENV=/aviator_adt/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy source code (changes most frequently - should be last)
COPY --chown=otuser:otuser src/ ./src/

COPY --from=build-node --chown=otuser:otuser /aviator_adt/src/aviator/statics/lib /aviator_adt/src/aviator/statics/lib

# Expose port
EXPOSE 3000

# Health check
# HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD curl -f http://localhost:3000/docs || exit 1

ARG COMMIT_SHORT_SHA
ARG RELEASE_VERSION

ENV COMMIT_SHORT_SHA=$COMMIT_SHORT_SHA
ENV RELEASE_VERSION=$RELEASE_VERSION

# Set UV cache directory to a writable location
ENV UV_CACHE_DIR=/home/otuser/uv_cache

# Run the application
ENTRYPOINT ["/entrypoint.sh"]
