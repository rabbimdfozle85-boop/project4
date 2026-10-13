#!/bin/sh
# FHONE launcher — works on Termux, Linux, macOS
# Usage: ./fhone [command] [args]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    echo "ERROR: python3 not found. Install it first."
    exit 1
fi

if [ -z "$FHONE_HOME" ]; then
    FHONE_HOME="$HOME/.fhone"
    export FHONE_HOME
fi

cd "$SCRIPT_DIR"
exec "$PYTHON" -m fhone "$@"
