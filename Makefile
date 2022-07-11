# Set these in the environment to override them. This is helpful for
# development if you have file ownership problems because the user
# in the container doesn't match the user on your host.
_UID ?= $(shell id -u)
_GID ?= 10001

.PHONY: help
help:
	@echo "Usage: make RULE"
	@echo ""
	@echo "JBI make rules:"
	@echo ""
	@echo "  build   - build docker containers"
	@echo "  lint    - lint check for code"
	@echo "  start   - run the API service"
	@echo ""
	@echo "  test        - run test suite"
	@echo "  shell       - open a shell in the web container"
	@echo "  test-shell  - open a shell in test environment"
	@echo ""
	@echo "  generate    - create json file from TEMPLATE"
	@echo ""
	@echo "  help    - see this text"


.PHONY: build
build:
	docker-compose -f ./docker-compose.yaml build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID}

.PHONY: lint
lint:
	mkdir -p ./.mypy_cache && chmod -R o+w ./.mypy_cache
	docker-compose -f ./docker-compose.yaml run web /app/infra/lint.sh

.PHONY: shell
shell:
	docker-compose -f ./docker-compose.yaml run web /bin/sh

.PHONY: start
start:
	docker-compose up

.PHONY: test
test:
	touch ./.coverage && chmod o+w ./.coverage
	docker-compose -f ./docker-compose.yaml run web /app/infra/test.sh
ifneq (1, ${MK_KEEP_DOCKER_UP})
	# Due to https://github.com/docker/compose/issues/2791 we have to explicitly
	# rm all running containers
	docker-compose down
endif

.PHONY: test-shell
test-shell:
	docker-compose -f ./docker-compose.yaml run web
