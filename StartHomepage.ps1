$ErrorActionPreference = 'Stop'
$taskPython = 'C:\Users\mablic\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { $taskPython = 'python' }
Push-Location $PSScriptRoot
try {
    try {
        $homepageState = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 2
        if ($homepageState.cities -and $homepageState.job) {
            & $taskPython -c "import webbrowser; webbrowser.open('http://127.0.0.1:8765')"
            return
        }
    } catch { }
    & $taskPython -u local_app.py
    exit $LASTEXITCODE
}
finally { Pop-Location }
