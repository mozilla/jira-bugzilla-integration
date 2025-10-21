# Set these in the environment to override them. This is helpful for
# development if you have file ownership problems because the user
# in the container doesn't match the user on your host.
_UID ?= 10001
_GID ?= 10001

VENV := $(shell echo $${VIRTUAL_ENV-.venv})
INSTALL_STAMP = $(VENV)/.install.stamp
DOTENV_FILE = .env


INSTALL_STAMP := .install.stamp
UV := $(shell command -v uv 2> /dev/null)

help:
	@echo "Please use 'make <target>' where <target> is one of the following commands.\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' Makefile | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo "\nCheck the Makefile to know exactly what each target is doing."

.PHONY: clean
clean: ## Delete cache files
	find . -name "__pycache__" | xargs rm -rf
	rm -rf .mypy_cache .ruff_cache .coverage .venv

install: $(INSTALL_STAMP)
$(INSTALL_STAMP): pyproject.toml uv.lock  ## Install dependencies
	@if [ -z $(UV) ]; then echo "uv could not be found. See https://docs.astral.sh/uv/"; exit 2; fi
	$(UV) --version
	$(UV) sync --frozen --verbose
	touch $(INSTALL_STAMP)

.PHONY: build
build: ## Build docker container
	docker-compose build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID}

.PHONY: format
format: $(INSTALL_STAMP)  ## Format code base
	bin/lint.sh lint --fix
	bin/lint.sh format --fix

.PHONY: lint
lint: $(INSTALL_STAMP)  ## Analyze code base
	bin/lint.sh

.PHONY: start
start: $(INSTALL_STAMP) $(DOTENV_FILE) ## Start local
	$(UV) run python -m asgi

$(DOTENV_FILE):  ## Initialize default configuration
	cp .env.example $(DOTENV_FILE)

.PHONY: docker-shell
docker-shell: $(DOTENV_FILE) ## Run shell from container
	docker compose run --rm web /bin/sh

.PHONY: docker-start
docker-start: $(DOTENV_FILE) ## Start container
	docker compose up

.PHONY: test
test: $(INSTALL_STAMP) ## Run unit tests
	$(UV) run pytest tests --cov-report term-missing --cov-fail-under 75 --cov jbi --cov checks
