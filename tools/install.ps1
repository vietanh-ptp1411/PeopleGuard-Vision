<#
    VisionGuard - cai dat tren may khach

    Tao moi truong Python rieng, cai thu vien, kiem tra he thong va dang ky
    tu khoi dong khi dang nhap Windows.

        powershell -ExecutionPolicy Bypass -File tools\install.ps1
        powershell -ExecutionPolicy Bypass -File tools\install.ps1 -NoAutoStart
        powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Gpu

    -Gpu         cai ban PyTorch CUDA (can cho che do YOLO tren card NVIDIA)
    -NoAutoStart chi cai, khong dang ky tu chay
    -TaskName    doi ten tac vu (mac dinh VisionGuard)
#>
param(
    [switch]$NoAutoStart,
    [switch]$Gpu,
    [string]$TaskName = "VisionGuard"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Say([string]$text, [string]$colour = "White") { Write-Host $text -ForegroundColor $colour }
function Step([string]$text) { Write-Host ""; Say ("=== " + $text + " ===") "Cyan" }

Say "VisionGuard - cai dat" "Green"
Say ("Thu muc: " + $Root)

# ---------------------------------------------------------------- Python
Step "1/5  Kiem tra Python"
$py = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3", "python")) {
    $parts = $candidate.Split(" ")
    $exe = $parts[0]
    $argv = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
    try {
        $version = & $exe @argv -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.10") {
            $py = $candidate
            Say ("  Tim thay Python " + $version + " qua '" + $candidate + "'") "Green"
            break
        }
    } catch { }
}
if (-not $py) {
    Say "  KHONG tim thay Python 3.10 tro len." "Red"
    Say "  Tai tai https://www.python.org/downloads/  (nho tick 'Add python.exe to PATH')" "Yellow"
    exit 1
}

# ---------------------------------------------------------------- venv
Step "2/5  Tao moi truong rieng (.venv)"
if (Test-Path ".venv\Scripts\python.exe") {
    Say "  Da co san, dung lai." "Gray"
} else {
    $parts = $py.Split(" ")
    $exe = $parts[0]
    $argv = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
    & $exe @argv -m venv .venv
    if ($LASTEXITCODE -ne 0) { Say "  Tao venv that bai." "Red"; exit 1 }
    Say "  Xong." "Green"
}
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"

# ---------------------------------------------------------------- packages
Step "3/5  Cai thu vien"
& $VenvPy -m pip install --upgrade pip --quiet
if ($Gpu) {
    Say "  Cai PyTorch ban CUDA (dung luong lon, cho vai phut)..." "Yellow"
    & $VenvPy -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
    if ($LASTEXITCODE -ne 0) { Say "  Cai PyTorch CUDA that bai - se dung ban CPU." "Yellow" }
}
& $VenvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Say "  Cai thu vien that bai." "Red"; exit 1 }
Say "  Xong." "Green"

# ---------------------------------------------------------------- preflight
Step "4/5  Kiem tra he thong"
& $VenvPy tools\preflight.py
$check = $LASTEXITCODE
if ($check -eq 1) {
    Say ""
    Say "  Co muc [FAIL]. Van cai xong, nhung phan mem chua chay duoc cho den khi sua." "Yellow"
}

# ---------------------------------------------------------------- autostart
Step "5/5  Dang ky tu khoi dong"
if ($NoAutoStart) {
    Say "  Bo qua theo yeu cau (-NoAutoStart)." "Gray"
} else {
    $launcher = Join-Path $Root "tools\launcher.py"
    $action = New-ScheduledTaskAction -Execute $VenvPy -Argument ("`"" + $launcher + "`"") -WorkingDirectory $Root
    # Dang nhap, khong phai khoi dong may: day la ung dung co giao dien, can phien lam viec co man hinh.
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = "PT30S"
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Principal $principal -Description "VisionGuard person-in-area monitoring" | Out-Null
        Say ("  Da dang ky tac vu '" + $TaskName + "' - chay khi dang nhap Windows (tre 30 giay).") "Green"
        Say ""
        Say "  LUU Y: may khach nen bat tu dong dang nhap (auto-logon) de sau khi mat dien" "Yellow"
        Say "  may tu vao Windows roi phan mem tu chay. Xem netplwiz hoac Sysinternals Autologon." "Yellow"
    } catch {
        Say ("  Dang ky that bai: " + $_.Exception.Message) "Red"
        Say "  Chay lai PowerShell bang quyen Administrator." "Yellow"
    }
}

Write-Host ""
Say "================================================" "Green"
Say " CAI DAT XONG" "Green"
Say "================================================" "Green"
Say ""
Say "  Chay ngay        :  tools\start.bat"
Say "  Kiem tra he thong:  .venv\Scripts\python.exe tools\preflight.py"
Say "  Go tu khoi dong  :  powershell -ExecutionPolicy Bypass -File tools\uninstall.ps1"
Say "  Nhat ky          :  logs\launcher.log  va  logs\<ngay>.log"
Write-Host ""
