
FROM mcr.microsoft.com/devcontainers/base:debian

RUN apt install curl git -y

RUN curl -fsSL https://deb.nodesource.com/setup_23.x -o nodesource_setup.sh && \
    bash nodesource_setup.sh && \
    apt install -y nodejs npm

COPY --from=artifactory.otxlab.net/ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=artifactory.otxlab.net/ghcr.io/astral-sh/ruff:latest /ruff /bin/

RUN mkdir -p /aviator_adt/.venv /home/vscode /home/vscode && chown -R vscode:vscode /aviator_adt/.venv /home/vscode

ENV UV_LINK_MODE=symlink

# Expose port
EXPOSE 3000