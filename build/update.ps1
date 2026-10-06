<# Apply a verified differential release to an existing VisionGuard installation. #>
param(
    [string]$InstallDir = "",
    [switch]$VerifyOnly
)
$ErrorActionPreference = "Stop"

function SafePath([string]$Root, [string]$Relative) {
    # ZIP paths always use forward slashes. Reject Windows aliases before comparing
    # names, since '.' / trailing dots or spaces can resolve to another manifest entry.
    if (-not $Relative -or [IO.Path]::IsPathRooted($Relative) -or $Relative -match '[\\<>:"|?*\x00-\x1f]' -or
        ($Relative -split '/' | Where-Object {
            $_ -eq '.' -or $_ -eq '..' -or $_ -eq '' -or $_ -match '[ .]$' -or
            $_ -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)'
        })) {
        throw "Invalid package path: $Relative"
    }
    $base = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath((Join-Path $base $Relative))
    if (-not $full.StartsWith($base, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path outside installation: $Relative"
    }
    # A junction inside the install must not redirect writes outside that folder.
    $part = $full
    while ($part.Length -gt $base.TrimEnd('\').Length) {
        if (Test-Path -LiteralPath $part) {
            $item = Get-Item -LiteralPath $part -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Junction/symlink is not supported: $part"
            }
        }
        $part = Split-Path -Parent $part
    }
    return $full
}

function Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# Rebuild a file from the installed copy and a VGDELTA1 patch (see build/vgdelta.py):
# 0x01 offset:int64 length:uint32 copies from the old file, 0x02 length:uint32 data appends.
function ApplyDelta([string]$Old, [string]$Patch, [string]$Out) {
    $oldStream = [IO.File]::OpenRead($Old)
    $reader = [IO.BinaryReader]::new([IO.File]::OpenRead($Patch))
    $writer = [IO.File]::Create($Out)
    $buffer = [byte[]]::new(65536)
    try {
        if ([Text.Encoding]::ASCII.GetString($reader.ReadBytes(8)) -ne 'VGDELTA1') { throw "Ban va khong hop le: $Patch" }
        $end = $reader.BaseStream.Length
        while ($reader.BaseStream.Position -lt $end) {
            $op = $reader.ReadByte()
            if ($op -eq 1) {
                $offset = $reader.ReadInt64()
                $count = $reader.ReadUInt32()
                if ($offset -lt 0 -or $offset + $count -gt $oldStream.Length) { throw "Ban va khong khop tep dang cai: $Patch" }
                $oldStream.Position = $offset
                $remaining = [long]$count
                while ($remaining -gt 0) {
                    $read = $oldStream.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                    if ($read -eq 0) { throw "Tep goc bi cat ngan: $Old" }
                    $writer.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } elseif ($op -eq 2) {
                $count = $reader.ReadUInt32()
                $remaining = [long]$count
                while ($remaining -gt 0) {
                    $read = $reader.Read($buffer, 0, [int][Math]::Min($buffer.Length, $remaining))
                    if ($read -eq 0) { throw "Ban va bi cat ngan: $Patch" }
                    $writer.Write($buffer, 0, $read)
                    $remaining -= $read
                }
            } else {
                throw "Ban va hong: $Patch"
            }
        }
    } finally {
        $reader.Dispose()
        $oldStream.Dispose()
        $writer.Dispose()
    }
}

function CheckClosed([string]$Root) {
    $running = Get-CimInstance Win32_Process -Filter "Name='VisionGuard.exe' OR Name='VisionGuardLauncher.exe'"
    foreach ($process in $running) {
        if (-not $process.ExecutablePath -or
            [IO.Path]::GetDirectoryName($process.ExecutablePath).Equals($Root, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Hay dong VisionGuard va doi launcher thoat, sau do chay Update.bat lai."
        }
    }
}

$backup = $null
$attempted = [Collections.Generic.List[object]]::new()
try {
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.format -ne 1 -and $manifest.format -ne 2) { throw "Unsupported update format" }
    if (-not $InstallDir) {
        $parent = Split-Path -Parent $PSScriptRoot
        $default = if (Test-Path -LiteralPath (Join-Path $parent 'VisionGuard.exe')) { $parent } else { 'C:\VisionGuard' }
        $answer = Read-Host "Thu muc phan mem dang dung [$default]"
        $InstallDir = if ($answer) { $answer.Trim('"') } else { $default }
    }
    $target = (Resolve-Path -LiteralPath $InstallDir).ProviderPath.TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $target 'VisionGuard.exe') -PathType Leaf)) {
        throw "Khong thay VisionGuard.exe trong thu muc da chon."
    }
    if ($target.Equals($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Hay giai nen goi cap nhat vao thu muc rieng."
    }
    if (-not $VerifyOnly) { CheckClosed $target }
    Write-Host "Kiem tra ban dang cai va tep cap nhat..."
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in @($manifest.required) + @($manifest.files)) {
        $relative = [string]$entry.path
        if (-not $seen.Add($relative)) { throw "Duplicate package path: $relative" }
        if ($relative -notmatch '^(_internal/|VisionGuard\.exe$|VisionGuardLauncher\.exe$|Install\.bat$|BAT-DAU\.md$|HUONG-DAN\.md$|RELEASE-NOTES\.txt$)') {
            throw "Package cannot modify this path: $relative"
        }
        $null = SafePath $target $relative
    }
    # Program files carry accepted before/after hashes, so a repeat update is safe.
    # Every new file is prepared and verified here - whole from payload, or rebuilt from
    # the installed copy and a delta into 'staged' - before anything installed is touched.
    $staging = SafePath $PSScriptRoot 'staged'
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    $sources = @{}
    foreach ($entry in $manifest.files) {
        $dest = SafePath $target $entry.path
        $actual = if (Test-Path -LiteralPath $dest -PathType Leaf) { Hash $dest } else { $null }
        if ($actual -eq $entry.sha256) { continue }          # already this version
        if ($entry.program) {
            $accepted = @(@($entry.accepted_sha256) + @($entry.base_sha256) | Where-Object { $_ })
            if ($actual -and $accepted -notcontains $actual) {
                throw "Ban dang cai khong tuong thich: $($entry.path). Chua thay doi tep nao."
            }
            if (-not $actual -and $entry.base_sha256) {
                throw "Ban dang cai thieu tep: $($entry.path). Chua thay doi tep nao."
            }
        }
        $delta = @($entry.deltas) | Where-Object { $_ -and $actual -and $_.base_sha256 -eq $actual } | Select-Object -First 1
        if ($delta) {
            $patch = SafePath $PSScriptRoot $delta.patch
            if (-not (Test-Path -LiteralPath $patch -PathType Leaf) -or (Hash $patch) -ne $delta.sha256) {
                throw "Tep cap nhat hong/thieu: $($delta.patch). Hay giai nen lai."
            }
            $built = SafePath $staging $entry.path
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $built) -Force
            Write-Host "  Dang dung $($entry.path) tu ban va..."
            ApplyDelta $dest $patch $built
            if ((Hash $built) -ne $entry.sha256) {
                throw "Ban va khong tao dung tep: $($entry.path). Chua thay doi tep nao."
            }
            $sources[$entry.path] = $built
        } else {
            $source = SafePath (Join-Path $PSScriptRoot 'payload') $entry.path
            if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Hash $source) -ne $entry.sha256) {
                throw "Tep cap nhat hong/thieu: $($entry.path). Hay giai nen lai."
            }
            $sources[$entry.path] = $source
        }
    }
    $checked = 0
    foreach ($entry in $manifest.required) {
        $path = SafePath $target $entry.path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Hash $path) -ne $entry.sha256) {
            throw "Thu vien dang cai khong tuong thich: $($entry.path). Chua thay doi tep nao."
        }
        $checked++
        if ($checked % 1000 -eq 0) { Write-Host "  Da kiem tra $checked tep thu vien." }
    }
    if ($VerifyOnly) {
        Write-Host "VERIFY OK - goi cap nhat tuong thich, chua thay doi tep nao."
        exit 0
    }
    CheckClosed $target
    $backupName = '_updates/' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
    $backup = SafePath $target $backupName
    $null = New-Item -ItemType Directory -Path $backup
    # Preserve configuration for a later manual rollback after the new app migrates it.
    $config = SafePath $target 'config'
    if (Test-Path -LiteralPath $config -PathType Container) {
        Copy-Item -LiteralPath $config -Destination (Join-Path $backup 'config') -Recurse
    }
    foreach ($entry in $manifest.files) {
        if (-not $sources.ContainsKey($entry.path)) { continue }
        $dest = SafePath $target $entry.path
        $old = SafePath (Join-Path $backup 'files') $entry.path
        $existed = Test-Path -LiteralPath $dest -PathType Leaf
        if ($existed) {
            $null = New-Item -ItemType Directory -Path (Split-Path -Parent $old) -Force
            Copy-Item -LiteralPath $dest -Destination $old
        }
        $attempted.Add([pscustomobject]@{ Destination = $dest; Backup = $old; Existed = $existed })
        $null = New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force
        Copy-Item -LiteralPath $sources[$entry.path] -Destination $dest -Force
        if ((Hash $dest) -ne $entry.sha256) { throw "Copy verification failed: $($entry.path)" }
    }
    Write-Host "CAP NHAT THANH CONG: $($manifest.version)" -ForegroundColor Green
    Write-Host "Ban sao luu: $backup"
    Write-Host "Mo VisionGuard.exe. Vao PLC > Devices de kiem tra/gan bit rieng cho tung ROI."
    Write-Host "Khong ghi de config, models, logs, events cua khach."
    exit 0
} catch {
    Write-Host "CAP NHAT CHUA HOAN TAT: $($_.Exception.Message)" -ForegroundColor Red
    for ($i = $attempted.Count - 1; $i -ge 0; $i--) {
        $item = $attempted[$i]
        try {
            if ($item.Existed) {
                if (-not (Test-Path -LiteralPath $item.Destination -PathType Leaf) -or
                    (Hash $item.Destination) -ne (Hash $item.Backup)) {
                    Copy-Item -LiteralPath $item.Backup -Destination $item.Destination -Force
                }
            } elseif (Test-Path -LiteralPath $item.Destination -PathType Leaf) {
                Remove-Item -LiteralPath $item.Destination
            }
        } catch {
            Write-Host "Khong khoi phuc duoc $($item.Destination): $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    if ($backup) { Write-Host "Ban sao luu de khoi phuc: $backup" }
    exit 1
}
