ruff:
		uv run ruff format 
		uv run ruff check  --ignore FIX

ruff-fix:
		uv run ruff format 
		uv run ruff check --fix

studio:
		LANGGRAPH_API=true langgraph dev --debug-port 5678

test:
		uv run pytest --cov=aviator

check:
		uv run ruff format
		uv run ruff check --fix
		uv run pytest --cov=aviator

# Docker commands with BuildKit enabled for optimal caching
.PHONY: check build up down restart logs docs k8s-up k8s-down

K8S_NAMESPACE ?= aviator
K8S_PROFILE ?= docker-desktop

build:
		DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose -f docker-compose.dev.yml build

up-backend:
		DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose -f docker-compose.yml up -d --build

up:
		DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose -f docker-compose.dev.yml up -d --build

up-migration:
		MIGRATION_ENABLED=true DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose -f docker-compose.dev.yml --profile migration up -d --build

down:
		docker compose -f docker-compose.dev.yml down

down-backend:
		docker compose -f docker-compose.yml down

restart:
		DOCKER_BUILDKIT=1 COMPOSE_DOCKER_CLI_BUILD=1 docker compose -f docker-compose.dev.yml restart

logs:
		docker compose -f docker-compose.dev.yml logs -f

docs:
		uv run python -m aviator draw
		uv run python -m aviator openapi
		uv run settings-doc generate --class aviator.settings.Settings --heading-offset 2 --output-format markdown > settings_doc.md
		uv run mkdocs serve

k8s-up:
		kubectl create namespace $(K8S_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
		kubectl -n $(K8S_NAMESPACE) create secret generic aviator-files --from-file=gsa=./otl-cs-csai.json --dry-run=client -o yaml | kubectl apply -f -
		docker build --target final -t artifactory.otxlab.net/cs-csai-docker-dev/aviator-plugin-sample:latest ../aviator-plugin-sample
		skaffold -f ./skaffold-backend.yaml run -p $(K8S_PROFILE) -n $(K8S_NAMESPACE)
		skaffold run -p $(K8S_PROFILE) -n $(K8S_NAMESPACE)

k8s-down:
		skaffold delete -p $(K8S_PROFILE) -n $(K8S_NAMESPACE)
		skaffold -f ./skaffold-backend.yaml delete -p $(K8S_PROFILE) -n $(K8S_NAMESPACE)
		kubectl -n $(K8S_NAMESPACE) delete secret aviator-files --ignore-not-found
		kubectl delete namespace $(K8S_NAMESPACE) --ignore-not-found
