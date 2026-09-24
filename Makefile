# =============================================================================
# Trinity Guard — Makefile
# =============================================================================
# Build, test, lint, and deploy the full polyglot AML stack.
#
# Usage:
#   make setup   — First-time setup (copy .env, pull Ollama model)
#   make up      — Start all services
#   make down    — Stop all services
#   make demo    — Run the live demo
#   make test    — Run all tests
#   make lint    — Lint all services
#   make build   — Build all Docker images
#   make clean   — Remove build artifacts
# =============================================================================

# Directories
ROOT_DIR := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
DEPLOY_DIR := $(ROOT_DIR)deploy
SERVICES_DIR := $(ROOT_DIR)services
DEMO_DIR := $(ROOT_DIR)demo

# Docker Compose
DOCKER_COMPOSE := docker compose -f $(DEPLOY_DIR)/docker-compose.yml --env-file $(ROOT_DIR).env

# Colors
COLOR := \033[36m
BOLD := \033[1m
END := \033[0m

.DEFAULT_GOAL := help

##@ Help
help: ## Show this help
	@printf "$(BOLD)Trinity Guard — Makefile$(END)\n\n"
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	/^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

##@ Setup
.PHONY: setup
setup: ## First-time setup: copy .env and pull Ollama model
	@if [ ! -f $(ROOT_DIR).env ]; then \
		cp $(ROOT_DIR).env.example $(ROOT_DIR).env; \
		printf "$(COLOR)Created .env from .env.example$(END)\n"; \
	else \
		printf "$(COLOR).env already exists$(END)\n"; \
	fi
	@printf "$(COLOR)Pulling Ollama model (llama3.2)...$(END)\n"
	@docker run --rm -v ollama_models:/root/.ollama ollama/ollama:latest ollama pull llama3.2 || true
	@printf "$(COLOR)Setup complete! Run 'make up' to start services.$(END)\n"

##@ Docker
.PHONY: up
up: ## Start all services
	$(DOCKER_COMPOSE) up -d
	@printf "$(COLOR)Services starting. Check health: make status$(END)\n"

.PHONY: down
down: ## Stop all services
	$(DOCKER_COMPOSE) down

.PHONY: status
status: ## Check service status
	$(DOCKER_COMPOSE) ps

.PHONY: logs
logs: ## Tail logs from all services
	$(DOCKER_COMPOSE) logs -f

.PHONY: logs-go
logs-go: ## Tail Go backend logs
	$(DOCKER_COMPOSE) logs -f go-backend

.PHONY: build
build: ## Build all Docker images
	$(DOCKER_COMPOSE) build

.PHONY: rebuild
rebuild: ## Rebuild all Docker images (no cache)
	$(DOCKER_COMPOSE) build --no-cache

##@ Demo
.PHONY: demo
demo: ## Run the live demo script
	cd $(DEMO_DIR) && python -m trinity_demo.live_demo

.PHONY: demo-check
demo-check: ## Check service health only
	cd $(DEMO_DIR) && python -m trinity_demo.live_demo --check-only

.PHONY: demo-sanctions
demo-sanctions: ## Run only the sanctions evasion scenario
	cd $(DEMO_DIR) && python -m trinity_demo.live_demo --scenario sanctions

.PHONY: demo-volume
demo-volume: ## Run high-volume scenario with 5000 transactions
	cd $(DEMO_DIR) && python -m trinity_demo.live_demo --scenario high-volume --count 5000

##@ Testing
.PHONY: test
test: test-rust test-go test-python ## Run all tests

.PHONY: test-rust
test-rust: ## Run Rust ZK tests
	cd $(SERVICES_DIR)/rust-zk && cargo test

.PHONY: test-go
test-go: ## Run Go backend tests
	cd $(SERVICES_DIR)/go-backend && go test ./...

.PHONY: test-python
test-python: test-ner test-llm ## Run all Python tests

.PHONY: test-ner
test-ner: ## Run NER service tests
	cd $(SERVICES_DIR)/python-ner && pytest

.PHONY: test-llm
test-llm: ## Run LLM service tests
	cd $(SERVICES_DIR)/llm-service && pytest

##@ Linting
.PHONY: lint
lint: lint-rust lint-go lint-python ## Lint all services

.PHONY: lint-rust
lint-rust: ## Lint Rust ZK service
	cd $(SERVICES_DIR)/rust-zk && cargo fmt --check && cargo clippy -- -D warnings

.PHONY: lint-go
lint-go: ## Lint Go backend
	cd $(SERVICES_DIR)/go-backend && go vet ./...

.PHONY: lint-python
lint-python: lint-ner lint-llm ## Lint all Python services

.PHONY: lint-ner
lint-ner: ## Lint NER service
	cd $(SERVICES_DIR)/python-ner && ruff check .

.PHONY: lint-llm
lint-llm: ## Lint LLM service
	cd $(SERVICES_DIR)/llm-service && ruff check .

##@ Code Generation
.PHONY: proto
proto: ## Regenerate protobuf stubs
	@printf "$(COLOR)Generating Go stubs...$(END)\n"
	protoc --go_out=. --go-grpc_out=. $(ROOT_DIR)contracts/proto/trinity.proto || \
		printf "$(COLOR)Install protoc: brew install protobuf$(END)\n"
	@printf "$(COLOR)Generating Rust stubs...$(END)\n"
	cd $(SERVICES_DIR)/rust-zk && cargo build || true

##@ Cleanup
.PHONY: clean
clean: ## Remove build artifacts
	$(DOCKER_COMPOSE) down -v
	cd $(SERVICES_DIR)/rust-zk && cargo clean
	find $(SERVICES_DIR) -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(SERVICES_DIR) -name ".venv" -type d -exec rm -rf {} + 2>/dev/null || true
	find $(DEMO_DIR) -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@printf "$(COLOR)Cleaned.$(END)\n"

##@ Database
.PHONY: db-shell
db-shell: ## Connect to PostgreSQL shell
	docker exec -it $$(docker compose -f $(DEPLOY_DIR)/docker-compose.yml ps -q postgres) psql -U marble_user -d marble_trinity

.PHONY: redis-shell
redis-shell: ## Connect to Redis CLI
	docker exec -it $$(docker compose -f $(DEPLOY_DIR)/docker-compose.yml ps -q redis) redis-cli
