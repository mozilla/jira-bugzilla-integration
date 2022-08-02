#!/bin/sh

set -e

POETRY_RUN="poetry run"

bandit () {
  $POETRY_RUN bandit -lll --recursive jbi
}
black () {
  $POETRY_RUN black ${check:+--check} jbi tests
}
detect_secrets () {
  # Scan only files fixed into the repo, omit poetry.lock
  FILES_TO_SCAN=`git ls-tree --full-tree -r --name-only HEAD | grep -v poetry.lock`
  $POETRY_RUN detect-secrets-hook $FILES_TO_SCAN --baseline .secrets.baseline
}
isort () {
  $POETRY_RUN isort ${check:+--check-only} .
}
pylint () {
  $POETRY_RUN pylint jbi tests
}
mypy () {
  $POETRY_RUN mypy jbi
}
yamllint () {
  $POETRY_RUN yamllint -c .yamllint config/*.yaml
}
all () {
  echo "running black"
  black
  echo "running isort"
  isort
  echo "running mypy"
  mypy
  echo "running pylint"
  pylint
  echo "running yamllint"
  yamllint
  echo "running bandit"
  bandit
  echo "running detect_secrets"
  detect_secrets
}

usage () {
  echo "Usage: bin/lint.sh [OPTION]"
  echo " run linting checks"
  echo "Options":
  echo "  bandit"
  echo "  black [--fix]"
  echo "  detect-secrets"
  echo "  isort [--fix]"
  echo "  mypy"
  echo "  pylint"
  echo "  yamllint"
}

subcommand='';
check="true"
if [ -z $1 ]; then
  all
else
  subcommand=$1; shift
  case $subcommand in
    "black" | "isort")
      case $1 in
        "--fix")
          check=""
        ;;
      esac
      case $subcommand in
        "isort") isort;;
        "black") black;;
      esac
    ;;

    "pylint") pylint;;
    "yamllint") yamllint;;
    "mypy") mypy;;
    "bandit") bandit;;
    "detect-secrets") detect_secrets;;
    *) usage;;
  esac
fi
