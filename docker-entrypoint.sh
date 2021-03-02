#!/bin/sh
set -e

if [ "$1" = '.venv/bin/opcua-bridge' ] && [ "$(id -u)" = '0' ]; then
    if [ -d "/certs" ]; then
        chown pythonapp:pythonapp /certs/cert.der /certs/key.pem
        chmod 644 /certs/cert.der
        chmod 600 /certs/key.pem
    fi
    exec gosu pythonapp "$0" "$@"
fi

exec "$@"
