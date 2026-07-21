# This file was modified in 2026 by YiQiao contributors. See NOTICE.

PYTHON ?= python
PNPM ?= pnpm
DOCKER ?= docker

DASHBOARD_DIR := server/dashboard
COMPOSE_DIR := server
COMPOSE := $(DOCKER) compose
BASE_COMPOSE := $(COMPOSE) -f docker-compose.yaml
SOURCE_COMPOSE := $(BASE_COMPOSE) -f docker-compose.build.yaml
PRODUCTION_COMPOSE := $(BASE_COMPOSE) -f docker-compose.production.yaml
RELEASE_COMPOSE := $(BASE_COMPOSE) -f docker-compose.production.yaml -f docker-compose.build.yaml
E2E_COMPOSE := $(SOURCE_COMPOSE) -f docker-compose.e2e.yaml
PYTHON_PATHS := mem0 yiqiao server tests scripts
PYTEST_ARGS ?=

.PHONY: all check install install-python install-dashboard format format-check lint lint-python \
	test test-python python-check dashboard dashboard-check dashboard-install dashboard-format \
	dashboard-lint dashboard-typecheck dashboard-test dashboard-build init compose-config images smoke audit \
	audit-modifications audit-python audit-dashboard docs-check secrets secrets-current secrets-history

all: check

check: docs-check python-check dashboard-check compose-config

docs-check:
	$(PYTHON) scripts/check_docs_localization.py

install: install-python install-dashboard

install-python:
	$(PYTHON) -m pip install -e ".[test,dev]"
	$(PYTHON) -m pip install -r server/requirements.txt

install-dashboard:
	cd $(DASHBOARD_DIR) && $(PNPM) install --frozen-lockfile

format:
	$(PYTHON) -m isort --profile black $(PYTHON_PATHS)
	$(PYTHON) -m ruff check --fix $(PYTHON_PATHS)
	$(PYTHON) -m ruff format $(PYTHON_PATHS)

format-check:
	$(PYTHON) -m isort --check-only --profile black $(PYTHON_PATHS)
	$(PYTHON) -m ruff format --check $(PYTHON_PATHS)

lint: lint-python

lint-python:
	$(PYTHON) -m ruff check $(PYTHON_PATHS)

test: test-python

test-python:
	$(PYTHON) -m pytest -q tests \
		--ignore=tests/embeddings \
		--ignore=tests/llms \
		--ignore=tests/rerankers \
		--ignore=tests/vector_stores \
		$(PYTEST_ARGS)

python-check: format-check lint-python test-python

dashboard: dashboard-check

dashboard-check: dashboard-lint dashboard-typecheck dashboard-test dashboard-build

dashboard-format: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) run format

dashboard-lint: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) run lint

dashboard-typecheck: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) run typecheck

dashboard-test: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) run test:unit

dashboard-build: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) run build

init:
	sh scripts/init.sh

compose-config:
	@test -f $(COMPOSE_DIR)/.env || (echo "error: server/.env is missing; run 'make init' first" && exit 2)
	cd $(COMPOSE_DIR) && $(BASE_COMPOSE) config --quiet
	cd $(COMPOSE_DIR) && $(SOURCE_COMPOSE) config --quiet
	cd $(COMPOSE_DIR) && $(PRODUCTION_COMPOSE) config --quiet
	cd $(COMPOSE_DIR) && $(RELEASE_COMPOSE) config --quiet
	cd $(COMPOSE_DIR) && $(E2E_COMPOSE) config --quiet

images:
	cd $(COMPOSE_DIR) && $(SOURCE_COMPOSE) build --no-cache

smoke:
	$(PYTHON) scripts/full_stack_smoke.py

audit: audit-modifications audit-python audit-dashboard

audit-modifications:
	$(PYTHON) scripts/audit_modification_notices.py --fetch-base

audit-python:
	$(PYTHON) -m pip_audit --local --progress-spinner off

audit-dashboard: install-dashboard
	cd $(DASHBOARD_DIR) && $(PNPM) audit --audit-level high

secrets: secrets-current secrets-history

secrets-current:
	gitleaks dir --redact=100 --no-banner .

secrets-history:
	gitleaks git --redact=100 --no-banner .
