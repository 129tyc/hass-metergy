Param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (Test-Path .venv) {
    if ($Force) {
        Remove-Item -Recurse -Force .venv
    }
}

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements-dev.txt

Write-Host "`nVenv ready. To activate later:`n  .\\.venv\\Scripts\\Activate.ps1" -ForegroundColor Green
