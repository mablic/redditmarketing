# Dedicated Python 3.12 matches the project's local browser dependencies.
param([switch]$PlanOnly, [switch]$CollectOnly, [switch]$Finalize)
$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Python runtime not found. Install the requirements with your Python and run city_pipeline.py directly.'
}
$taskArgs = @((Join-Path $PSScriptRoot 'city_pipeline.py'))
$taskArgs += @('--source', 'public')
if ($PlanOnly) { $taskArgs += '--plan-only' }
if ($CollectOnly) { $taskArgs += '--collect-only' }
if ($Finalize) { $taskArgs += '--finalize' }
Push-Location $PSScriptRoot
try {
    & $taskPython @taskArgs
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
