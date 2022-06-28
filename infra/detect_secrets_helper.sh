#!/bin/sh

set -ex

CURRENT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BASE_DIR="$(dirname "$CURRENT_DIR")"
HAS_GIT="$(command -v git || echo '')"

if [ -n "$HAS_GIT" ]; then
    # Scan only files checked into the repo, omit poetry.lock
    SECRETS_TO_SCAN=`git ls-tree --full-tree -r --name-only HEAD | grep -v poetry.lock`
    detect-secrets-hook $SECRETS_TO_SCAN --baseline .secrets.baseline
fi
