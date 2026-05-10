#!/usr/bin/env bash
# Shell wrapper for submit.py — see python file for argparse help.
exec python3 "$(dirname "$0")/submit.py" "$@"
