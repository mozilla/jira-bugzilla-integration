FROM python:3.14.3-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

ARG userid=10001
ARG groupid=10001
RUN groupadd --gid $groupid app && \
    useradd -g app --uid $userid --shell /usr/sbin/nologin --create-home app

# Install pandoc for markdown to Jira conversions.
RUN apt-get -y update && \
    apt-get -y install --no-install-recommends pandoc curl

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY ./pyproject.toml /app/
COPY ./uv.lock /app/

ENV UV_CACHE_DIR=/opt/uv-cache
RUN mkdir -p "${UV_CACHE_DIR}" && \
    chown app:app "${UV_CACHE_DIR}"

WORKDIR /app
COPY . /app

# Install dependencies
RUN --mount=type=cache,target="${UV_CACHE_DIR}" \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-dev --locked --no-install-project --no-editable

# run as non priviledged user
USER app

EXPOSE $PORT
CMD ["uv", "run", "--no-dev", "--frozen", "python", "-m", "asgi"]
