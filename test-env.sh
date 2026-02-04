#!/bin/bash


# source env/aparcar.env

uv run labgrid-client lock

uv run pytest tests/ \
    --lg-log \
    --log-cli-level=CONSOLE \
    --lg-colored-steps \
    --reportportal \
    -s

uv run labgrid-client unlock
