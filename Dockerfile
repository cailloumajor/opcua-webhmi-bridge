FROM python:3.8.7-buster as builder

SHELL ["/bin/bash", "-Eeuv", "-o", "pipefail", "-c"]

ENV PYTHONUNBUFFERED=1 \
    POETRY_HOME=/opt/poetry

COPY poetry_install_vars.sh /usr/local/lib
# hadolint ignore=SC1091
RUN . /usr/local/lib/poetry_install_vars.sh \
    && curl -sSL -o get-poetry.py "$POETRY_URL" \
    && python get-poetry.py --yes --no-modify-path --version="$POETRY_VERSION"
ENV PATH="${POETRY_HOME}/bin:$PATH"

WORKDIR /app

COPY poetry.lock pyproject.toml ./
# hadolint ignore=SC1091
RUN python -m venv .venv \
    && . .venv/bin/activate \
    && poetry install --no-ansi --no-dev --no-interaction --no-root

COPY src ./src
# hadolint ignore=SC1091
RUN . .venv/bin/activate \
    && poetry install --no-ansi --no-dev --no-interaction

FROM python:3.8.7-slim-buster

LABEL maintainer="Arnaud Rocher <arnaud.roche3@gmail.com>"
LABEL org.opencontainers.image.source https://github.com/cailloumajor/opcua-webhmi-bridge

ENV PYTHONUNBUFFERED 1

RUN useradd --user-group --system --create-home --no-log-init pythonapp \
    && mkdir /app \
    && chown pythonapp:pythonapp /app
USER pythonapp
WORKDIR /app

COPY --from=builder --chown=pythonapp:pythonapp /app /app

EXPOSE 8008

CMD [".venv/bin/opcua-agent"]
