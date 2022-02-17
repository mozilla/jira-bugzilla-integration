#!/bin/sh

set -e

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"

coverage run --rcfile "${BASE_DIR}/pyproject.toml" -m pytest
coverage report --rcfile "${BASE_DIR}/pyproject.toml" -m --fail-under 80
coverage html --rcfile "${BASE_DIR}/pyproject.toml"
