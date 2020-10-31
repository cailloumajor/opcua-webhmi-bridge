#!/usr/bin/env bash

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
