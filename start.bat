@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    set "PYTHON_CMD="
    where py >nul 2>&1
    if !errorlevel! equ 0 (
        py -3.12 --version >nul 2>&1 && set "PYTHON_CMD=py -3.12"
        if not defined PYTHON_CMD (
            py -3.11 --version >nul 2>&1 && set "PYTHON_CMD=py -3.11"
        )
        if not defined PYTHON_CMD (
            py -3.10 --version >nul 2>&1 && set "PYTHON_CMD=py -3.10"
        )
    )
    if not defined PYTHON_CMD (
        where python >nul 2>&1 && set "PYTHON_CMD=python"
    )
    if not defined PYTHON_CMD (
        echo Error: Python 3.10-3.12 is required but was not found in PATH.
        exit /b 1
    )

    echo Creating virtual environment with !PYTHON_CMD!...
    !PYTHON_CMD! -m venv venv || exit /b 1
    venv\Scripts\python.exe -m pip install --upgrade pip || exit /b 1
    venv\Scripts\pip.exe install -r requirements.txt || exit /b 1

    if /i "%INSTALL_CUDA%"=="1" (
        echo Installing vieneu[cuda]...
        venv\Scripts\pip.exe install "vieneu[cuda]" || exit /b 1
    ) else if not "%INSTALL_CUDA%"=="0" (
        where nvidia-smi >nul 2>&1
        if !errorlevel! equ 0 (
            echo CUDA GPU detected - installing vieneu[cuda]...
            venv\Scripts\pip.exe install "vieneu[cuda]" || exit /b 1
        )
    )

    venv\Scripts\pip.exe install --no-deps -e . || exit /b 1
)

call venv\Scripts\activate.bat
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
if not defined GRPC_ENABLE_FORK_SUPPORT set "GRPC_ENABLE_FORK_SUPPORT=0"
if not defined GRPC_VERBOSITY set "GRPC_VERBOSITY=ERROR"

python -m tts_service
