# Set these in the environment to override them. This is helpful for
# development if you have file ownership problems because the user
# in the container doesn't match the user on your host.
_UID ?= 10001
_GID ?= 10001

VENV := $(shell echo $${VIRTUAL_ENV-.venv})
INSTALL_STAMP = $(VENV)/.install.stamp
POETRY_VIRTUALENVS_IN_PROJECT = true

.PHONY: help
help:
	@echo "Usage: make RULE"
	@echo ""
	@echo "JBI make rules:"
	@echo ""
	@echo "  build   - build docker containers"
	@echo "  lint    - lint check for code"
	@echo "  format  - run formatters (black, isort), fix in place"
	@echo "  start   - run the API service"
	@echo ""
	@echo "  test        - run test suite"
	@echo "  shell       - open a shell in the web container"
	@echo ""
	@echo "  help    - see this text"

install: $(INSTALL_STAMP)
$(INSTALL_STAMP): poetry.lock
	@if [ -z $(shell command -v poetry 2> /dev/null) ]; then echo "Poetry could not be found. See https://python-poetry.org/docs/"; exit 2; fi
	poetry install --no-root
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

.PHONY: shell
shell:
	docker-compose run --rm web /bin/sh

.PHONY: start
start:
	docker-compose up

.PHONY: test
test: $(INSTALL_STAMP)
	bin/test.sh
