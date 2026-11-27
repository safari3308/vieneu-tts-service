#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -d "./venv" ]; then
    PYTHON_BIN=""
    for candidate in python3.12 python3.11 python3.10 python3; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            PYTHON_BIN="${candidate}"
            break
        fi
    done

    if [ -z "${PYTHON_BIN}" ]; then
        echo "Error: Python 3.10-3.12 is required but was not found in PATH." >&2
        exit 1
    fi

    echo "Creating virtual environment with ${PYTHON_BIN}..."
    "${PYTHON_BIN}" -m venv ./venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt

    if [ "${INSTALL_CUDA:-auto}" = "1" ] || { [ "${INSTALL_CUDA:-auto}" = "auto" ] && command -v nvidia-smi >/dev/null 2>&1; }; then
        echo "CUDA GPU detected — installing vieneu[cuda]..."
        ./venv/bin/pip install "vieneu[cuda]"
    fi

    ./venv/bin/pip install --no-deps -e .
fi

# shellcheck disable=SC1091
source ./venv/bin/activate
export PYTHONPATH="${SCRIPT_DIR}/src:${PYTHONPATH:-}"
export GRPC_ENABLE_FORK_SUPPORT="${GRPC_ENABLE_FORK_SUPPORT:-0}"
export GRPC_VERBOSITY="${GRPC_VERBOSITY:-ERROR}"

exec python -m tts_service
