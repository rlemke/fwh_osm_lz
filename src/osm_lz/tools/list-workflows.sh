#!/usr/bin/env bash
# Shell wrapper for list_workflows.py — see python file for argparse help.
exec python3 "$(dirname "$0")/list_workflows.py" "$@"
