#!/bin/sh
set -e

if [ "$1" = '.venv/bin/opcua-bridge' ] && [ "$(id -u)" = '0' ]; then
    if [ -d "/tmp/certs" ]; then
        mkdir /certs
        cp /tmp/certs/cert.der /tmp/certs/key.pem /certs
        chown pythonapp:pythonapp /certs/cert.der /certs/key.pem
        chmod 644 /certs/cert.der
        chmod 600 /certs/key.pem
    fi
    exec gosu pythonapp "$0" "$@"
fi

exec "$@"
