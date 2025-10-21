#!/bin/sh

set -e

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"

UV_RUN="uv run"

$UV_RUN coverage run --rcfile "${BASE_DIR}/pyproject.toml" -m pytest
$UV_RUN coverage report --rcfile "${BASE_DIR}/pyproject.toml" -m --fail-under 75
$UV_RUN coverage html --rcfile "${BASE_DIR}/pyproject.toml"
