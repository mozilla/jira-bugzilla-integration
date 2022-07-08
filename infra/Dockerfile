# Creating a python base with shared environment variables
FROM python:3.9-slim as python-base
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PYSETUP_PATH="/opt/pysetup" \
    VENV_PATH="/opt/pysetup/.venv" \
    PORT=8000

ENV PATH="$POETRY_HOME/bin:$VENV_PATH/bin:$PATH"

# Set up user and group
ARG userid=10001
ARG groupid=10001
WORKDIR /app
RUN groupadd --gid $groupid app && \
    useradd -g app --uid $userid --shell /usr/sbin/nologin --create-home app

RUN mkdir -p $POETRY_HOME && \
    chown app:app /opt/poetry && \
    mkdir -p $PYSETUP_PATH && \
    chown app:app $PYSETUP_PATH && \
    mkdir -p /app && \
    chown app:app /app

RUN apt-get update && \
    apt-get install --assume-yes apt-utils && \
    echo 'debconf debconf/frontend select Noninteractive' | debconf-set-selections && \
    apt-get install --no-install-recommends -y \
      libpq5

# builder-base is used to build dependencies
FROM python-base as builder-base
RUN apt-get install --no-install-recommends -y \
      curl \
      build-essential \
      libpq-dev

# Install Poetry - respects $POETRY_VERSION & $POETRY_HOME
USER app
ENV POETRY_VERSION=1.1.5
RUN curl -sSL https://raw.githubusercontent.com/sdispater/poetry/master/get-poetry.py | python

# We copy our Python requirements here to cache them
# and install only runtime deps using poetry
WORKDIR $PYSETUP_PATH
COPY --chown=app:app ./poetry.lock ./pyproject.toml ./
RUN poetry install --no-dev --no-root


# 'development' stage installs all dev deps and can be used to develop code.
# For example using docker-compose to mount local volume under /app
FROM python-base as development
ENV FASTAPI_ENV=development

# Copying poetry and venv into image
USER app
COPY --from=builder-base $POETRY_HOME $POETRY_HOME
COPY --from=builder-base $PYSETUP_PATH $PYSETUP_PATH

# Copying in our entrypoint
COPY --chown=app:app ./infra/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# venv already has runtime deps installed we get a quicker install
WORKDIR $PYSETUP_PATH
RUN poetry install --no-root

WORKDIR /app
COPY --chown=app:app . .

EXPOSE $PORT
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD uvicorn src.app.api:app --reload --host=0.0.0.0 --port=$PORT


# 'lint' stage runs similar checks to pre-commit
# running in check mode means build will fail if any linting errors occur
FROM development AS lint
CMD ./infra/lint.sh


# 'test' stage runs our unit tests with pytest and
# coverage.
FROM development AS test
CMD ./infra/test.sh


# 'production' stage uses the clean 'python-base' stage and copyies
# in only our runtime deps that were installed in the 'builder-base'
FROM python-base as production
ENV FASTAPI_ENV=production \
    IS_GUNICORN=1 \
    PROMETHEUS_MULTIPROC=1

COPY --from=builder-base $VENV_PATH $VENV_PATH
COPY ./infra/gunicorn_conf.py /gunicorn_conf.py

COPY ./infra/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

COPY . /app
WORKDIR /app

EXPOSE $PORT
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD gunicorn -k uvicorn.workers.UvicornWorker -c /gunicorn_conf.py src.app.api:app
