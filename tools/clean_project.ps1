<# List generated caches and obsolete build work. -Apply archives metadata and removes only validated targets. #>
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).ProviderPath.TrimEnd('\')
$prefix = $workspace + '\'
$targets = [Collections.Generic.List[string]]::new()
foreach ($relative in @('.pytest_cache', 'build\work-gpu', 'build\work-gpu-2026-10-04',
                         'release\.update-build-2026-10-02', 'release\.update-build-2026-10-04')) {
    $candidate = Join-Path $workspace $relative
    if (Test-Path -LiteralPath $candidate) { $targets.Add($candidate) }
}
foreach ($relative in @('visionguard', 'tests', 'tools')) {
    $folder = Join-Path $workspace $relative
    Get-ChildItem -LiteralPath $folder -Directory -Recurse -Filter '__pycache__' | ForEach-Object {
        $targets.Add($_.FullName)
    }
}
$cache = Join-Path $workspace 'build\__pycache__'
if (Test-Path -LiteralPath $cache) { $targets.Add($cache) }

function CheckedTarget([string]$path) {
    $resolved = (Resolve-Path -LiteralPath $path).ProviderPath
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing target outside workspace: $resolved"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse point: $resolved" }
    $links = @(Get-ChildItem -LiteralPath $resolved -Force -Recurse |
        Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
    if ($links.Count) { throw "Refusing tree with junction/symlink: $resolved" }
    $relative = $resolved.Substring($prefix.Length).Replace('\', '/')
    $tracked = @(git -C $workspace ls-files -- "$relative/")
    if ($LASTEXITCODE -ne 0 -or $tracked.Count) { throw "Refusing tracked/unknown tree: $relative" }
    return $resolved
}

$plan = @()
foreach ($target in $targets) {
    $resolved = CheckedTarget $target
    $files = @(Get-ChildItem -LiteralPath $resolved -File -Force -Recurse)
    $bytes = ($files | Measure-Object Length -Sum).Sum
    $plan += [pscustomobject]@{ Path = $resolved; Files = $files.Count; Bytes = [long]$bytes }
}
$plan | Select-Object Path,Files,@{N='GiB';E={[Math]::Round($_.Bytes/1GB,4)}} | Format-Table -AutoSize
$total = ($plan | Measure-Object Bytes -Sum).Sum
Write-Host ('Total generated data: {0:N3} GiB' -f ($total / 1GB))
if (-not $Apply) { Write-Host 'DRY RUN. Run with -Apply to archive metadata and clean these targets.'; exit 0 }

$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(VisionGuard|VisionGuardLauncher|python|pythonw)\.exe$' -and
    ($_.Name -like 'VisionGuard*' -or $_.CommandLine -match 'PyInstaller|pytest|build_update|benchmark_runtime')
})
if ($running.Count) { throw 'Stop application, tests and builds before cleaning generated files.' }
$audit = Join-Path $workspace ('release\cleanup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$null = New-Item -ItemType Directory -Path $audit
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::Open((Join-Path $audit 'preserved-metadata.zip'), [IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($item in $plan) {
        if ($item.Path -notlike '*\.update-build-*') { continue }
        foreach ($file in Get-ChildItem -LiteralPath $item.Path -File -Recurse -Force) {
            $relative = $file.FullName.Substring($prefix.Length).Replace('\', '/')
            if ($relative -match '/_internal/' -or $file.Extension -notin @('.json','.txt','.md','.log','.db','.pt','.xml')) { continue }
            $null = [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $relative,
                                                                         [IO.Compression.CompressionLevel]::Optimal)
        }
    }
} finally { $archive.Dispose() }
$plan | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $audit 'manifest.json') -Encoding UTF8
foreach ($item in $plan) {
    # Recheck resolved targets immediately before recursive deletion.
    $resolved = CheckedTarget $item.Path
    Remove-Item -LiteralPath $resolved -Recurse -Force
}
Write-Host ('CLEANED {0:N3} GiB. Audit and preserved site metadata: {1}' -f ($total/1GB), $audit)
