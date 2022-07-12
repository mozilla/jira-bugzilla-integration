#!/bin/sh

set -e

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"

POETRY_RUN="poetry run"

$POETRY_RUN coverage run --rcfile "${BASE_DIR}/pyproject.toml" -m pytest
$POETRY_RUN coverage report --rcfile "${BASE_DIR}/pyproject.toml" -m --fail-under 75
$POETRY_RUN coverage html --rcfile "${BASE_DIR}/pyproject.toml"
