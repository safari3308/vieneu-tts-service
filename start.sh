#!/bin/zsh
set -euo pipefail

source ./venv/bin/activate
export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"

exec python -m tts_service
