FROM python:3.8-slim as base

LABEL maintainer "Arnaud Rocher <arnaud.roche3@gmail.com>"

RUN python -m pip install --upgrade pip

RUN useradd --user-group --system --create-home --no-log-init pythonapp \
    && mkdir /app \
    && chown pythonapp:pythonapp /app
USER pythonapp
WORKDIR /app

FROM base as builder

ENV PATH "/home/pythonapp/.local/bin:$PATH"

RUN pip install --user poetry==1.0.5

COPY --chown=pythonapp:pythonapp pyproject.toml poetry.lock ./

# hadolint ignore=SC1091
RUN python -m venv .venv \
    && . .venv/bin/activate \
    && poetry install --no-dev --no-interaction

COPY --chown=pythonapp:pythonapp . ./

FROM base

COPY --from=builder /app /app

CMD [".venv/bin/python", "opcua_websocket_bridge.py"]
