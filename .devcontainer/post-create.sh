#!/usr/bin/env bash

set -Eeuo pipefail

SOCKET_GID=$(stat -c "%g" /var/run/docker.sock)
if ! grep -q ":${SOCKET_GID}:" /etc/group
then
    sudo groupadd --gid "${SOCKET_GID}" docker-host
fi
if ! id -G vscode | grep -qw "${SOCKET_GID}"
then
    sudo usermod -aG "${SOCKET_GID}" vscode
fi

# shellcheck disable=SC1091
source poetry_install_vars.sh

pipx_tools=(
    "poetry==$POETRY_VERSION"
    "pre-commit"
)

for tool in "${pipx_tools[@]}"
do
    pipx install "$tool"
done

poetry install
