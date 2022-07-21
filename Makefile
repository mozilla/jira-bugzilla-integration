# Set these in the environment to override them. This is helpful for
# development if you have file ownership problems because the user
# in the container doesn't match the user on your host.
_UID ?= 10001
_GID ?= 10001

VENV := $(shell echo $${VIRTUAL_ENV-.venv})
INSTALL_STAMP = $(VENV)/.install.stamp
DOTENV_FILE = .env

.PHONY: help
help:
	@echo "Usage: make RULE"
	@echo ""
	@echo "JBI make rules:"
	@echo ""
	@echo "Local"
	@echo "  clean         - clean local cache folders"
	@echo "  format        - run formatters (black, isort), fix in place"
	@echo "  lint          - run linters"
	@echo "  start         - run the API service locally"
	@echo "  test          - run test suite"
	@echo ""
	@echo "Docker"
	@echo "  build         - build docker container"
	@echo "  docker-start  - run the API service through docker"
	@echo "  docker-shell  - open a shell in the web container"
	@echo ""
	@echo "  help          - see this text"

.PHONY: clean
clean:
	find . -name "__pycache__" | xargs rm -rf
	rm -rf .mypy_cache .pytest_cache .coverage .venv


install: $(INSTALL_STAMP)
$(INSTALL_STAMP): poetry.lock
	@if [ -z $(shell command -v poetry 2> /dev/null) ]; then echo "Poetry could not be found. See https://python-poetry.org/docs/"; exit 2; fi
	POETRY_VIRTUALENVS_IN_PROJECT=1 poetry install --no-root
	touch $(INSTALL_STAMP)

.PHONY: build
build:
	docker-compose build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID}

.PHONY: format
format: $(INSTALL_STAMP)
	bin/lint.sh black --fix
	bin/lint.sh isort --fix

.PHONY: lint
lint: $(INSTALL_STAMP)
	bin/lint.sh

.PHONY: start
start: $(INSTALL_STAMP) $(DOTENV_FILE)
	poetry run python -m src.app.api

$(DOTENV_FILE):
	cp .env.example $(DOTENV_FILE)

.PHONY: docker-shell
docker-shell: $(DOTENV_FILE)
	docker-compose run --rm web /bin/sh

.PHONY: docker-start
docker-start: $(DOTENV_FILE)
	docker-compose up

.PHONY: test
test: $(INSTALL_STAMP)
	bin/test.sh
