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
	@echo "  start   - run the API service"
	@echo ""
	@echo "  test        - run test suite"
	@echo "  shell       - open a shell in the web container"
	@echo "  test-shell  - open a shell in test environment"
	@echo ""
	@echo "  help    - see this text"


.PHONY: build
build:
	docker-compose -f ./docker-compose.yaml -f ./tests/infra/docker-compose.test.yaml build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID}

.PHONY: lint
lint:
	docker-compose -f ./docker-compose.yaml -f ./tests/infra/docker-compose.lint.yaml build \
		--build-arg userid=${_UID} --build-arg groupid=${_GID} lint


.PHONY: shell
shell:
	docker-compose -f ./docker-compose.yaml run web

.PHONY: start
start:
	docker-compose up

.PHONY: test
test:
	docker-compose -f ./docker-compose.yaml -f ./tests/infra/docker-compose.test.yaml run tests
ifneq (1, ${MK_KEEP_DOCKER_UP})
	# Due to https://github.com/docker/compose/issues/2791 we have to explicitly
	# rm all running containers
	docker-compose down
endif

.PHONY: test-shell
test-shell: .env
	docker-compose -f ./docker-compose.yaml -f ./tests/infra/docker-compose.test.yaml run web
