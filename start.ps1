$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    $PythonCmd = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in @("-3.12", "-3.11", "-3.10")) {
            & py $ver --version *> $null
            if ($LASTEXITCODE -eq 0) {
                $PythonCmd = @("py", $ver)
                break
            }
        }
    }
    if (-not $PythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $PythonCmd = @("python")
    }
    if (-not $PythonCmd) {
        Write-Error "Python 3.10-3.12 is required but was not found in PATH."
        exit 1
    }

    Write-Host "Creating virtual environment with $($PythonCmd -join ' ')..."
    if ($PythonCmd.Count -eq 2) {
        & $PythonCmd[0] $PythonCmd[1] -m venv venv
    } else {
        & $PythonCmd[0] -m venv venv
    }

    & .\venv\Scripts\python.exe -m pip install --upgrade pip
    & .\venv\Scripts\pip.exe install -r requirements.txt

    $InstallCuda = $env:INSTALL_CUDA
    $HasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($InstallCuda -eq "1" -or ([string]::IsNullOrEmpty($InstallCuda) -and $HasNvidia)) {
        Write-Host "CUDA GPU detected - installing vieneu[cuda]..."
        & .\venv\Scripts\pip.exe install "vieneu[cuda]"
    }

    & .\venv\Scripts\pip.exe install --no-deps -e .
}

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$ScriptDir\src;$env:PYTHONPATH" } else { "$ScriptDir\src" }
if (-not $env:GRPC_ENABLE_FORK_SUPPORT) { $env:GRPC_ENABLE_FORK_SUPPORT = "0" }
if (-not $env:GRPC_VERBOSITY) { $env:GRPC_VERBOSITY = "ERROR" }

& .\venv\Scripts\python.exe -m tts_service
