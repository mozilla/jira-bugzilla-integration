# Set these in the environment to override them. This is helpful for
# development if you have file ownership problems because the user
# in the container doesn't match the user on your host.
_UID ?= 10001
_GID ?= 10001

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


.PHONY: build
build:
	docker-compose build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID}

.PHONY: format
format:
	bin/lint.sh black --fix
	bin/lint.sh isort --fix

.PHONY: lint
lint:
	docker-compose run --rm web bin/lint.sh

.PHONY: shell
shell:
	docker-compose run web /bin/sh

.PHONY: start
start:
	docker-compose up

.PHONY: test
test:
	docker-compose run --rm web bin/test.sh
