.DEFAULT_GOAL := help
.PHONY: help install env lint format test cov audit check seed run-api run-ui up down logs clean

UV ?= uv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (API, dashboard, Bedrock extra, dev tools)
	$(UV) sync --all-extras

env: ## Create .env from .env.example with generated secrets
	$(UV) run --no-sync python scripts/make_env.py

lint: ## Ruff lint + format check
	$(UV) run --no-sync ruff check .
	$(UV) run --no-sync ruff format --check .

format: ## Apply Ruff formatting and safe fixes
	$(UV) run --no-sync ruff format .
	$(UV) run --no-sync ruff check --fix .

test: ## Run the test suite (offline, mock LLM)
	$(UV) run --no-sync pytest

cov: ## Tests with coverage report
	$(UV) run --no-sync pytest --cov=app --cov-report=term-missing

audit: ## Scan locked dependencies for known vulnerabilities
	./scripts/audit.sh

check: lint test audit ## Everything CI runs

seed: ## Create demo tenants, users and documents
	$(UV) run --no-sync python -m app.demo

run-api: ## Start the API on :8000 with reload
	$(UV) run --no-sync uvicorn app.main:app_factory --factory --reload --port 8000

run-ui: ## Start the Streamlit dashboard on :8501
	SAO_API_URL=$${SAO_API_URL:-http://localhost:8000} STREAMLIT_CLIENT_TOOLBAR_MODE=minimal \
	STREAMLIT_SERVER_MAX_UPLOAD_SIZE=5 STREAMLIT_THEME_PRIMARY_COLOR="#2563eb" \
	$(UV) run --no-sync streamlit run frontend/streamlit_app.py --server.headless=true --browser.gatherUsageStats=false

up: ## Build and start the Docker Compose stack
	docker compose up --build -d
	@echo "API: http://localhost:8000/docs   Dashboard: http://localhost:8501"

down: ## Stop the stack (keeps the data volume)
	docker compose down

logs: ## Follow container logs
	docker compose logs -f

clean: ## Remove local data, caches and coverage files
	rm -rf data .pytest_cache .ruff_cache .coverage htmlcov
