FROM python:3.8-buster as builder

ENV PYTHONUNBUFFERED 1

SHELL ["/bin/bash", "-Eeuo", "pipefail", "-c"]

COPY poetry_install_vars.sh /usr/local/lib
# hadolint ignore=SC1091
RUN source /usr/local/lib/poetry_install_vars.sh \
    && curl -sSL -o get-poetry.py "$POETRY_URL" \
    && python get-poetry.py --yes --no-modify-path --version="$POETRY_VERSION"

WORKDIR /app

COPY src ./src
COPY poetry.lock pyproject.toml ./

# hadolint ignore=SC1091
RUN python -m venv .venv \
    && . .venv/bin/activate \
    && "$HOME"/.poetry/bin/poetry install --no-dev --no-interaction

FROM python:3.8-slim-buster

LABEL maintainer="Arnaud Rocher <arnaud.roche3@gmail.com>"
LABEL org.opencontainers.image.source https://github.com/cailloumajor/opcua_webhmi_bridge

ENV PYTHONUNBUFFERED 1

RUN useradd --user-group --system --create-home --no-log-init pythonapp \
    && mkdir /app \
    && chown pythonapp:pythonapp /app
USER pythonapp
WORKDIR /app

COPY --from=builder --chown=pythonapp:pythonapp /app /app

EXPOSE 8008

CMD [".venv/bin/opcua-agent"]
