#!/usr/bin/env bash

pipx_tools=(
    "poetry==1.0.10"
    "pre-commit"
)

for tool in "${pipx_tools[@]}"
do
    pipx install "$tool"
done
