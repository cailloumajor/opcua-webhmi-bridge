FROM python:3.8 as builder

ENV PYTHONUNBUFFERED 1

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG POETRY_URL=https://raw.githubusercontent.com/python-poetry/poetry/master/get-poetry.py
RUN curl -sSL $POETRY_URL | python - --version=1.0.5

WORKDIR /app

COPY pyproject.toml poetry.lock ./

# hadolint ignore=SC1091
RUN python -m venv .venv \
    && . .venv/bin/activate \
    && "$HOME"/.poetry/bin/poetry install --no-dev --no-interaction

COPY . ./

FROM python:3.8-slim

LABEL maintainer="Arnaud Rocher <arnaud.roche3@gmail.com>"

ENV PYTHONUNBUFFERED 1

RUN useradd --user-group --system --create-home --no-log-init pythonapp \
    && mkdir /app \
    && chown pythonapp:pythonapp /app
USER pythonapp
WORKDIR /app

COPY --from=builder --chown=pythonapp:pythonapp /app /app

CMD [".venv/bin/python", "-m", "opcua_webhmi_bridge"]
