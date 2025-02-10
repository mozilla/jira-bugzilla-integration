# Creating a python base with shared environment variables
FROM python:3.13.2 as base
ENV PIP_NO_CACHE_DIR=off \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    PYSETUP_PATH="/opt/pysetup"

ENV PATH="$POETRY_HOME/bin:$PATH"

# Install Poetry - respects $POETRY_VERSION & $POETRY_HOME
RUN python3 -m venv $POETRY_HOME && \
    $POETRY_HOME/bin/pip install poetry==1.7.1 && \
    $POETRY_HOME/bin/poetry --version

# We copy our Python requirements here to cache them
# and install only runtime deps using poetry
WORKDIR $PYSETUP_PATH
COPY ./poetry.lock ./pyproject.toml ./
RUN $POETRY_HOME/bin/poetry install --without dev --no-root

# `production` stage uses the dependencies downloaded in the `base` stage
FROM python:3.13.2-slim as production

# Install pandoc for markdown to Jira conversions.
RUN apt-get -y update && \
    apt-get -y install --no-install-recommends pandoc

ENV PORT=8000 \
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
CMD ["python", "-m", "asgi"]
