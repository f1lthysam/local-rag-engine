$workspaceRoot = Split-Path -Parent $PSScriptRoot
$activateScript = Join-Path $workspaceRoot "venv\Scripts\Activate.ps1"

if (Test-Path $activateScript) {
    . $activateScript
}
