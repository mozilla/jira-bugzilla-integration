#!/bin/sh

set -ex

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"
HAS_GIT="$(command -v git || echo '')"


if [ -n "$HAS_GIT" ]; then
	pre-commit run --all-files
else
	bandit -lll --recursive src --exclude "src/poetry.lock"
	mypy src tests
	black --check src tests
	isort --recursive --check-only src tests
	pylint src tests
	yamllint -c "${BASE_DIR}/.yamllint" ./config

    echo "⚠️  detect-secrets running on partial file list: \`git\` command missing"
    SECRETS_TO_SCAN=`find ./ -type f \( -iname \*.py -o -iname \*.md -o -iname \*.toml -o -iname \*.yaml  -o -iname \*.env \)`
    detect-secrets-hook --baseline .secrets.baseline --verbose $SECRETS_TO_SCAN
fi
