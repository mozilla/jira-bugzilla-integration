#!/bin/sh

set -e

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"
HAS_GIT="$(command -v git || echo '')"
echo $HAS_GIT

bandit -lll --recursive "${BASE_DIR}" --exclude "${BASE_DIR}/poetry.lock,${BASE_DIR}/.venv,${BASE_DIR}/.mypy,${BASE_DIR}/build"

if [ -n "$HAS_GIT" ]; then
    # Scan only files checked into the repo, omit poetry.lock
    SECRETS_TO_SCAN=`git ls-tree --full-tree -r --name-only HEAD | grep -v poetry.lock`
    detect-secrets-hook $SECRETS_TO_SCAN --baseline .secrets.baseline
fi

mypy "${BASE_DIR}"
black --config "${BASE_DIR}/pyproject.toml" "${BASE_DIR}"
isort --recursive --settings-path "${BASE_DIR}/pyproject.toml" "${BASE_DIR}"
pylint src tests/unit
