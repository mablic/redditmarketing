param([Parameter(Mandatory=$true)][string]$ProjectRoot, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\','/')
$prefix = [regex]::Escape($root) -replace '\\\\','[\\/]'
$taskPattern = $prefix + '[\\/](run_remaining|city_pipeline|scenic_pipeline)\.py(?:"|\s|$)'
$all = @(Get-CimInstance Win32_Process)
$wrapperPattern = '(?i)-File\s+"?' + $prefix + '[\\/]StartCityPipeline\.ps1(?:"|\s|$)'
$roots = @($all | Where-Object {
    ($_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match $taskPattern) -or
    ($_.Name -match '^(powershell|pwsh)\.exe$' -and $_.CommandLine -match $wrapperPattern)
})
$ids = [System.Collections.Generic.HashSet[int]]::new()
foreach ($p in $roots) { [void]$ids.Add([int]$p.ProcessId) }
do {
    $added = $false
    foreach ($p in $all) {
        if ($ids.Contains([int]$p.ParentProcessId) -and -not $ids.Contains([int]$p.ProcessId)) {
            [void]$ids.Add([int]$p.ProcessId); $added = $true
        }
    }
} while ($added)
if ($DryRun) {
    @{roots=@($roots | ForEach-Object { $_.ProcessId }); descendants=@($ids); stopped=0} | ConvertTo-Json -Compress
    exit 0
}
# Stop controllers first so they cannot launch more city/browser workers.
$handles = @{}
foreach ($taskId in $ids) {
    $process = Get-Process -Id $taskId -ErrorAction SilentlyContinue
    if ($process) { $handles[$taskId] = $process }
}
foreach ($p in $roots) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
foreach ($taskId in $ids) { Stop-Process -Id $taskId -Force -ErrorAction SilentlyContinue }
# Windows may retain exited processes while a parent still holds their handles.
# Wait for exit and inspect HasExited instead of treating a visible PID as alive.
$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    $alive = @($handles.Values | Where-Object {
        try { -not $_.HasExited } catch { $false }
    })
    if (-not $alive.Count) { break }
    foreach ($process in $alive) {
        try { [void]$process.WaitForExit(200) } catch {}
    }
} while ([DateTime]::UtcNow -lt $deadline)
$alive = @($handles.Values | Where-Object {
    try { -not $_.HasExited } catch { $false }
})
if ($alive.Count) {
    $details = ($alive | ForEach-Object { "$($_.ProcessName) (PID $($_.Id))" }) -join ', '
    throw "These project processes are still running after waiting: $details"
}
foreach ($process in $handles.Values) { $process.Dispose() }
@{stopped=$ids.Count} | ConvertTo-Json -Compress
