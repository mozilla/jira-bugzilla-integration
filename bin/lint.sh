#!/bin/sh

set -e

POETRY_RUN="poetry run"

BANDIT_CMD="$POETRY_RUN bandit -lll --recursive jbi"

FORMAT_CMD="$POETRY_RUN ruff format ."

# Scan only files fixed into the repo, omit poetry.lock
DETECT_SECRETS_FILES="$(git ls-tree --full-tree -r --name-only HEAD | grep -v poetry.lock)"
DETECT_SECRETS_CMD="$POETRY_RUN detect-secrets-hook $DETECT_SECRETS_FILES --baseline .secrets.baseline"

LINT_CMD="$POETRY_RUN ruff check ."

MYPY_CMD="$POETRY_RUN mypy jbi"

YAMLLINT_CMD="$POETRY_RUN yamllint -c .yamllint config/*.yaml"

ACTIONS_LINT_CMD="$POETRY_RUN jbi lint local && $POETRY_RUN jbi lint nonprod && $POETRY_RUN jbi lint prod"

all () {
  echo "running bandit"
  $BANDIT_CMD
  echo "running format"
  $FORMAT_CMD
  echo "running detect-secrets"
  $DETECT_SECRETS_CMD
  echo "running lint"
  $LINT_CMD
  echo "running mypy"
  $MYPY_CMD
  echo "running yamllint"
  $YAMLLINT_CMD
  echo "running actions lint"
  $ACTIONS_LINT_CMD
}

usage () {
  echo "Usage: bin/lint.sh [subcommand] [--fix]"
  echo " run linting checks, and optionally fix in place (if available)"
  echo "Subcommand":
  echo "  bandit"
  echo "  detect-secrets"
  echo "  format"
  echo "  lint"
  echo "  mypy"
  echo "  yamllint"
  echo "  actions"
}

if [ -z "$1" ]; then
  all
else
  subcommand=$1; shift
  case $subcommand in
    "format")
      if [ -n "$1" ] && [ "$1" != "--fix" ]; then
        usage
      else
        check_flag="--check"
        [ "$1" = "--fix" ] && check_flag=""
        $FORMAT_CMD ${check_flag:-}
      fi
      ;;
    "lint")
      if [ -n "$1" ] && [ "$1" != "--fix" ]; then
        usage
      else
        $LINT_CMD ${1:-}
      fi
      ;;
    "yamllint")
      $YAMLLINT_CMD
      ;;
    "mypy")
      $MYPY_CMD
      ;;
    "bandit")
      $BANDIT_CMD
      ;;
    "detect-secrets")
      $DETECT_SECRETS_CMD
      ;;
    "actions")
      $ACTIONS_LINT_CMD
      ;;
    *)
      usage
      ;;
  esac
fi

