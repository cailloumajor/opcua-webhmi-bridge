#!/bin/sh
set -e

if [ "$1" = '.venv/bin/opcua-agent' ] && [ "$(id -u)" = '0' ]; then
    if [ -s "/tmp/certs/cert.der" ] && [ -s "/tmp/certs/key.pem" ]; then
        mkdir -p /certs
        cp /tmp/certs/cert.der /tmp/certs/key.pem /certs
        chown pythonapp:pythonapp /certs/cert.der /certs/key.pem
        chmod 644 /certs/cert.der
        chmod 600 /certs/key.pem
        export OPC_CERT_FILE=/certs/cert.der
        export OPC_PRIVATE_KEY_FILE=/certs/key.pem
    fi
    exec gosu pythonapp "$0" "$@"
fi

exec "$@"
