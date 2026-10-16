# G3Network - Makefile
# Common development commands. `make help` lists them; the Makefile is the
# source of truth for how to run anything in this repo.

.PHONY: help setup infra-up infra-down infra-logs infra-reset backend-install backend-dev telemetry-dev charging-ocpp-dev telematics-monitor-dev telematics-status-dev driving-sessions-autoend-dev identity-bootstrap backend-test backend-test-integration coverage format lint domain-model-check feature-catalog-check api-client api-client-check portal-install portal-dev portal-check check audit install-hooks db-check db-migrate db-reset

COMPOSE := docker compose -f infra/docker-compose.yml
# The web portal is one self-contained pnpm workspace in web-portal/ (TT-31).
# pnpm runs through corepack at the version pinned in web-portal/package.json
# (TT-29), so every pnpm command runs from inside web-portal/; no global
# `corepack enable`, and no download prompt in scripts.
PNPM := cd web-portal && COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack pnpm
API_CLIENT_DIR := web-portal/packages/api-client

# Default: show help
help:
	@echo "=== G3Network Development Commands ==="
	@echo ""
	@echo "First time:"
	@echo "  make setup          - Create .env files, install deps, enable git hooks, start infra, migrate, run make check"
	@echo ""
	@echo "Infrastructure:"
	@echo "  make infra-up       - Start PostgreSQL and EMQX"
	@echo "  make infra-down     - Stop PostgreSQL and EMQX (keep data)"
	@echo "  make infra-logs     - View PostgreSQL and EMQX logs"
	@echo "  make infra-reset    - Remove PostgreSQL, EMQX, and all data"
	@echo ""
	@echo "Backend:"
	@echo "  make backend-install - Install dependencies (runtime + dev tools)"
	@echo "  make backend-dev     - Run the API server (port 8000, auto-reload)"
	@echo "  make telemetry-dev   - Run telemetry ingestion (MQTT consumer + worker)"
	@echo "  make charging-ocpp-dev - Run the OCPP gateway, 2.0.1 + 1.6J (port 9000)"
	@echo "  make telematics-monitor-dev - Run the device-silence health monitor"
	@echo "  make telematics-status-dev  - Run T-Box status-report ingestion (MQTT consumer + worker)"
	@echo "  make driving-sessions-autoend-dev - Run the driving-session auto-end worker"
	@echo "  make identity-bootstrap - Create the internal organization and first HEAD_ADMIN"
	@echo ""
	@echo "Web portal (web-portal/):"
	@echo "  make portal-install  - Install the portal's dependencies (pnpm, frozen lockfile)"
	@echo "  make portal-dev      - Run the portal dev server (http://localhost:5173, /api proxied to :8000)"
	@echo ""
	@echo "Tests and quality:"
	@echo "  make backend-test    - Run the smoke tests"
	@echo "  make backend-test-integration - Run the PostgreSQL integration tests (needs infra-up)"
	@echo "  make coverage        - Smoke tests with a coverage report (terminal + backend/htmlcov/)"
	@echo "  make format          - Sort imports and format (ruff for the backend, Prettier for the portal)"
	@echo "  make lint            - Ruff lint + format check, import-linter, mypy (app + tests)"
	@echo "  make domain-model-check - DBML design source matches the models; generated views are current"
	@echo "  make feature-catalog-check - feature catalog source follows its rules; generated views are current"
	@echo "  make api-client      - Regenerate web-portal/packages/api-client from the backend's OpenAPI document (after an API change)"
	@echo "  make api-client-check - the committed API client matches the backend; type-checks it"
	@echo "  make portal-check    - Portal: Prettier check, ESLint, tsc, Vitest, api-client-check"
	@echo "  make check           - lint + backend-test + domain-model-check + feature-catalog-check + portal-check (the pre-commit gate)"
	@echo "  make audit           - Check locked dependencies for known vulnerabilities (pip-audit)"
	@echo "  make install-hooks   - Make git run .githooks/pre-commit (make check) before every commit"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate      - Apply the baseline migration to an empty database"
	@echo "  make db-reset        - Clear the database and rebuild it from the baseline (wipes all data)"
	@echo "  make db-check        - Check the database built by the migration matches the models (alembic check)"
	@echo ""

# === FIRST-TIME SETUP ===

# Idempotent: safe to re-run. Never overwrites an existing .env, and uses
# db-migrate (not db-reset), so it never wipes an existing local database.
setup:
	@test -f backend/.env || { cp backend/.env.example backend/.env && echo "✓ Created backend/.env from backend/.env.example"; }
	@test -f infra/.env || { cp infra/.env.example infra/.env && echo "✓ Created infra/.env from infra/.env.example"; }
	@# The API refuses to start without a token signing key (hardening D1):
	@# generate one into backend/.env when the line is missing or empty.
	@python3 -c "import pathlib, re, secrets; \
	path = pathlib.Path('backend/.env'); text = path.read_text(); \
	pattern = re.compile(r'^IDENTITY_TOKEN_SECRET_KEY=(.*)$$', re.M); found = pattern.search(text); \
	ok = found is not None and len(found.group(1).strip()) >= 32; \
	line = 'IDENTITY_TOKEN_SECRET_KEY=' + secrets.token_urlsafe(48); \
	text = text if ok else (pattern.sub(line, text, count=1) if found else text.rstrip(chr(10)) + chr(10) + line + chr(10)); \
	path.write_text(text); \
	print('✓ IDENTITY_TOKEN_SECRET_KEY is set in backend/.env' if ok else '✓ Generated IDENTITY_TOKEN_SECRET_KEY in backend/.env')"
	cd backend && uv sync
	$(PNPM) install --frozen-lockfile
	git config core.hooksPath .githooks
	$(COMPOSE) up -d
	@echo "Waiting for PostgreSQL to accept connections..."
	@for attempt in $$(seq 1 60); do \
		$(COMPOSE) exec -T db pg_isready -q && break; \
		if [ $$attempt -eq 60 ]; then echo "PostgreSQL did not become ready in 60 s"; exit 1; fi; \
		sleep 1; \
	done
	cd backend && uv run alembic upgrade head
	$(MAKE) check
	@echo ""
	@echo "✓ Setup complete. Next: 'make backend-dev' (API docs at http://localhost:8000/docs)."
	@echo "  Claude Code extras: Node.js for the Context7 MCP server, 'npm install -g pyright' for the Pyright plugin."

# === INFRASTRUCTURE ===

infra-up:
	@echo "Starting PostgreSQL and EMQX..."
	$(COMPOSE) up -d
	@echo "✓ PostgreSQL: localhost:5432"
	@echo "✓ EMQX MQTT: localhost:1883"
	@echo "✓ EMQX Dashboard: http://localhost:18083"
	@echo "  Database: g3network"
	@echo "  User: g3network"
	@echo "  Password: g3network123"

infra-down:
	@echo "Stopping PostgreSQL and EMQX..."
	$(COMPOSE) down
	@echo "✓ Stopped (data is preserved)"

infra-logs:
	$(COMPOSE) logs -f db broker

infra-reset:
	@echo "⚠️  WARNING: This operation will DELETE ALL DATA!"
	@echo "Press Ctrl+C to cancel, or Enter to continue..."
	@read confirm
	$(COMPOSE) down -v
	@echo "✓ Containers and volumes removed"

# === BACKEND ===

# `uv sync` installs the runtime dependencies plus the `dev` dependency group
# (ruff, mypy, pytest, import-linter, pytest-cov) declared in pyproject.toml.
backend-install:
	@echo "Installing backend dependencies..."
	cd backend && uv sync
	@echo "✓ Installation complete"

backend-dev:
	@echo "Starting backend server..."
	@echo "API Docs: http://localhost:8000/docs"
	cd backend && uv run uvicorn app.api.main:app --reload --port 8000

telemetry-dev:
	@echo "Starting telemetry ingestion..."
	cd backend && uv run python -m app.domains.telemetry.ingestion.entrypoint

charging-ocpp-dev:
	@echo "Starting OCPP gateway (2.0.1 + 1.6J)..."
	cd backend && uv run python -m app.domains.charging_stations.ocpp.entrypoint

telematics-status-dev:
	@echo "Starting T-Box status-report ingestion..."
	cd backend && uv run python -m app.domains.telematics.ingestion.entrypoint

telematics-monitor-dev:
	@echo "Starting telematics device health monitor..."
	cd backend && uv run python -m app.domains.telematics.monitoring.entrypoint

driving-sessions-autoend-dev:
	@echo "Starting driving-session auto-end worker..."
	cd backend && uv run python -m app.domains.drivers.monitoring.entrypoint

identity-bootstrap:
	@echo "Creating the internal organization and the first HEAD_ADMIN (IDENTITY_BOOTSTRAP_* in backend/.env)..."
	cd backend && uv run python -m app.domains.identity.bootstrap

# === WEB PORTAL ===

portal-install:
	$(PNPM) install --frozen-lockfile

portal-dev:
	@echo "Portal: http://localhost:5173 (start the API with 'make backend-dev')"
	$(PNPM) dev

# === TESTS AND QUALITY ===

backend-test:
	@echo "Running backend tests..."
	cd backend && uv run pytest

backend-test-integration:
	@echo "Running PostgreSQL integration tests (creates and drops a temporary database)..."
	cd backend && RUN_DB_INTEGRATION=1 uv run pytest tests/test_postgres_*.py

# Informational only: no coverage threshold is enforced (yet).
coverage:
	cd backend && uv run pytest --cov --cov-report=term --cov-report=html
	@echo "✓ HTML report: backend/htmlcov/index.html"

# Ruff is the only formatter: "--select I --fix" sorts imports, "format" lays out code.
format:
	cd backend && uv run ruff check --select I --fix .
	cd backend && uv run ruff format .
	$(PNPM) format

lint:
	cd backend && uv run ruff check .
	cd backend && uv run ruff format --check .
	cd backend && uv run lint-imports
	cd backend && uv run mypy app tests

# Verifies the DBML design source still matches the SQLAlchemy models and the
# generated domain-model views are current.
domain-model-check:
	uv run --project backend --with pydbml --with openpyxl python .claude/skills/domain-model/scripts/domain_model.py check

# Verifies the feature catalog source follows its rules and the generated
# checklists and workbook are current.
feature-catalog-check:
	uv run --project backend --with pyyaml --with openpyxl python .claude/skills/feature-catalog/scripts/feature_catalog.py check

# Regenerates the portal's typed API client (web-portal-scaffold.md D6): dumps
# the OpenAPI document without running the server, then generates the types.
# Run after any backend route or schema change; commit both files.
api-client:
	cd backend && uv run python -m app.api.openapi_export ../$(API_CLIENT_DIR)/openapi.json
	$(PNPM) --filter @g3/api-client generate

# Fails when the committed client no longer matches the backend's API: builds
# both files again in a temporary folder and compares them byte for byte.
api-client-check:
	@tmp_dir=$$(mktemp -d) && trap 'rm -rf "$$tmp_dir"' EXIT && \
	(cd backend && uv run python -m app.api.openapi_export "$$tmp_dir/openapi.json") && \
	(export OPENAPI_INPUT="$$tmp_dir/openapi.json" OPENAPI_OUTPUT="$$tmp_dir/schema.d.ts" && \
		$(PNPM) --silent --filter @g3/api-client generate > /dev/null) && \
	if cmp -s "$$tmp_dir/openapi.json" $(API_CLIENT_DIR)/openapi.json && \
		cmp -s "$$tmp_dir/schema.d.ts" $(API_CLIENT_DIR)/src/schema.d.ts; then \
		echo "OK: the API client matches the backend's OpenAPI document"; \
	else \
		echo "✗ $(API_CLIENT_DIR) is stale: run 'make api-client' and commit the result"; exit 1; \
	fi
	$(PNPM) --filter @g3/api-client typecheck

# The portal's part of the gate (web-portal-scaffold.md D9): formatting, lint
# (import boundaries, i18n, no hand-picked colors), types, unit tests, and the
# generated client's freshness. Playwright is not part of it (D10).
portal-check: api-client-check
	$(PNPM) format:check
	$(PNPM) lint
	$(PNPM) typecheck
	$(PNPM) test

# The gate the pre-commit hook runs. The PostgreSQL integration tests are not
# part of it (they need the database); run backend-test-integration for schema work.
check: lint backend-test domain-model-check feature-catalog-check portal-check
	@echo "✓ All checks passed"

# Audits every locked package (runtime + dev groups) against the PyPA advisory
# database. pip-audit runs through uvx, so it is not a project dependency.
audit:
	@mkdir -p backend/.audit
	cd backend && uv export --frozen --all-groups --no-emit-project --format requirements-txt -q > .audit/requirements.txt
	cd backend && uvx pip-audit -r .audit/requirements.txt --disable-pip --progress-spinner off

install-hooks:
	git config core.hooksPath .githooks
	@echo "✓ Git now runs .githooks/pre-commit (make check) before every commit"

# === DATABASE ===

# Applies the baseline to an empty database. On a database built from an older
# version of the baseline, use db-reset instead.
db-migrate:
	@echo "Running Alembic migrations..."
	cd backend && uv run alembic upgrade head
	@echo "✓ Database migrated"

# Bootstrap phase: the schema is one baseline migration that is edited in place,
# so the recorded revision may no longer exist. "stamp --purge" forgets it without
# running any downgrade; the baseline's upgrade then clears every application
# object and recreates the schema. Never run this on a database with real data.
db-reset:
	@echo "⚠️  Clearing the database and rebuilding it from the baseline migration..."
	cd backend && uv run alembic stamp --purge base
	cd backend && uv run alembic upgrade head
	@echo "✓ Database reset"

# Compares the live database (after make db-reset) with the SQLAlchemy models;
# "No new upgrade operations detected" means the baseline migration and the
# models agree. env.py filters out PostGIS/TimescaleDB-owned objects.
db-check:
	cd backend && uv run alembic check
