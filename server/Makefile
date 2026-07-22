# This file was modified in 2026 by YiQiao contributors. See NOTICE.

.PHONY: init config up up-build up-production down clean logs seed wait-api wait-dashboard bootstrap health build run_local reset-admin-password prune-logs prune-oauth

OUTPUT ?= text
API_PORT ?= 8888
DASHBOARD_PORT ?= 3000
API_URL ?= http://localhost:$(API_PORT)
DASHBOARD_URL ?= http://localhost:$(DASHBOARD_PORT)
COMPOSE = docker compose
SOURCE_COMPOSE = $(COMPOSE) -f docker-compose.yaml -f docker-compose.build.yaml
PRODUCTION_COMPOSE = $(COMPOSE) -f docker-compose.yaml -f docker-compose.production.yaml

init:
	@sh ../scripts/init.sh

config:
	@test -f .env || (echo "error: server/.env is missing; run ../scripts/init.sh first" && exit 2)
	@$(COMPOSE) config --quiet
	@$(SOURCE_COMPOSE) config --quiet
	@$(PRODUCTION_COMPOSE) config --quiet

up: config
	$(COMPOSE) up -d
	@$(MAKE) --no-print-directory wait-api
	@$(MAKE) --no-print-directory wait-dashboard
	@echo ""
	@echo "Stack is ready."
	@echo "  Dashboard:  $(DASHBOARD_URL)"
	@echo "  API:        $(API_URL)"

up-build: config
	$(SOURCE_COMPOSE) up -d --build
	@$(MAKE) --no-print-directory wait-api
	@$(MAKE) --no-print-directory wait-dashboard

up-production: config
	$(PRODUCTION_COMPOSE) up -d
	@$(MAKE) --no-print-directory wait-api
	@$(MAKE) --no-print-directory wait-dashboard

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f

seed:
	@API_URL="$(API_URL)" DASHBOARD_URL="$(DASHBOARD_URL)" EMAIL="$(EMAIL)" PASSWORD="$(PASSWORD)" NAME="$(NAME)" OUTPUT="$(OUTPUT)" ./scripts/seed.sh

wait-api:
	@echo "Waiting for API at $(API_URL)..."
	@attempt=0; until curl -fsS "$(API_URL)/api/health" >/dev/null 2>&1; do \
		attempt=$$((attempt + 1)); \
		if [ $$attempt -ge 60 ]; then \
			echo "error: API did not become healthy within 120 seconds"; \
			$(COMPOSE) ps; \
			exit 1; \
		fi; \
		sleep 2; \
	done

wait-dashboard:
	@echo "Waiting for dashboard at $(DASHBOARD_URL)..."
	@attempt=0; until curl -fsS "$(DASHBOARD_URL)/api/health" >/dev/null 2>&1; do \
		attempt=$$((attempt + 1)); \
		if [ $$attempt -ge 60 ]; then \
			echo "error: dashboard did not become healthy within 120 seconds"; \
			$(COMPOSE) ps; \
			exit 1; \
		fi; \
		sleep 2; \
	done

bootstrap: up seed

health:
	@echo "API:       $$(curl -s -o /dev/null -w '%{http_code}' "$(API_URL)/api/health")"
	@echo "Dashboard: $$(curl -s -o /dev/null -w '%{http_code}' "$(DASHBOARD_URL)/api/health")"
	@echo "Postgres:  $$(docker compose exec -T postgres pg_isready -q && echo 'ok' || echo 'down')"
	@echo "Neo4j:     $$(docker compose exec -T neo4j sh -c 'cypher-shell -u \"$${NEO4J_USERNAME:-neo4j}\" -p \"$${NEO4J_AUTH#*/}\" \"RETURN 1\"' >/dev/null 2>&1 && echo 'ok' || echo 'down')"

reset-admin-password:
	@test -n "$(EMAIL)" || (echo "usage: make reset-admin-password EMAIL=<email> PASSWORD=<password>" && exit 2)
	@test -n "$(PASSWORD)" || (echo "usage: make reset-admin-password EMAIL=<email> PASSWORD=<password>" && exit 2)
	@$(COMPOSE) exec -T -e EMAIL="$(EMAIL)" -e PASSWORD="$(PASSWORD)" -e PYTHONPATH=/app yiqiao python scripts/reset_admin_password.py

prune-logs:
	@$(COMPOSE) exec -T -e REQUEST_LOG_RETENTION_DAYS="$(REQUEST_LOG_RETENTION_DAYS)" -e PYTHONPATH=/app yiqiao python scripts/prune_request_logs.py

prune-oauth:
	@$(COMPOSE) exec -T \
		-e OAUTH_CLEANUP_BATCH_SIZE="$(OAUTH_CLEANUP_BATCH_SIZE)" \
		-e OAUTH_AUDIT_RETENTION_DAYS="$(OAUTH_AUDIT_RETENTION_DAYS)" \
		-e OAUTH_REFRESH_REPLAY_GRACE_SECONDS="$(OAUTH_REFRESH_REPLAY_GRACE_SECONDS)" \
		-e PYTHONPATH=/app yiqiao python scripts/prune_oauth.py

build:
	$(SOURCE_COMPOSE) build

run_local: up-build
