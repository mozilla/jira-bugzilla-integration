# Creating a python base with shared environment variables
FROM python:3.10.5 as base
ENV PIP_NO_CACHE_DIR=off \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    PYSETUP_PATH="/opt/pysetup"

ENV PATH="$POETRY_HOME/bin:$PATH"

# Install Poetry - respects $POETRY_VERSION & $POETRY_HOME
ENV POETRY_VERSION=1.1.14
RUN curl -sSL https://install.python-poetry.org | python3 -

# We copy our Python requirements here to cache them
# and install only runtime deps using poetry
WORKDIR $PYSETUP_PATH
COPY ./poetry.lock ./pyproject.toml ./
RUN poetry install --no-dev --no-root

# 'production' stage uses the clean 'base' stage and copyies
# in only our runtime deps that were installed in the 'builder-base'
FROM python:3.10.5-slim as production
ENV PROMETHEUS_MULTIPROC=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/pysetup/.venv

ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=base $VIRTUAL_ENV $VIRTUAL_ENV

ARG userid=10001
ARG groupid=10001
RUN groupadd --gid $groupid app && \
    useradd -g app --uid $userid --shell /usr/sbin/nologin --create-home app
USER app

WORKDIR /app
COPY . .

EXPOSE $PORT
ENTRYPOINT ["bin/docker-entrypoint.sh"]
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "-c", "config/gunicorn_conf.py", "src.app.api:app"]
