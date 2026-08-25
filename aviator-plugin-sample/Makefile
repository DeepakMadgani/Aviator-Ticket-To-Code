ruff:
		uv run ruff format 
		uv run ruff check  --ignore FIX

ruff-fix:
		uv run ruff format 
		uv run ruff check --fix

build:
		docker compose -f docker-compose.yml build

up-parallel:
		AVIATOR_APP_PLUGIN_PORT=3001 docker compose -f docker-compose.yml up -d --build

up:
		docker compose -f docker-compose.yml up -d --build

debug:
		docker compose -f docker-compose.debug.yml up --build

debug-down:
		docker compose -f docker-compose.debug.yml down

down:
		docker compose -f docker-compose.yml down

test:
		uv run pytest

test-docker:
		docker compose -f docker-compose.test.yml build
		docker compose -f docker-compose.test.yml run --rm test

restart:
		docker compose -f docker-compose.yml restart

logs:
		docker compose -f docker-compose.yml logs -f
