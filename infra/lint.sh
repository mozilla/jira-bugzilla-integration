#!/bin/sh

set -e

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"

bandit -lll --recursive src --exclude "src/poetry.lock,src/.venv,src/.mypy,src/build"
mypy src
black --config "${BASE_DIR}/pyproject.toml" --check src tests
isort --recursive --settings-path "${BASE_DIR}/pyproject.toml" --check-only src
pylint src tests
yamllint -c "${BASE_DIR}/.yamllint" ./config
./infra/detect_secrets_helper.sh
