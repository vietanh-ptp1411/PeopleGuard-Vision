<#  Go tac vu tu khoi dong cua VisionGuard (khong xoa phan mem hay du lieu). #>
param([string]$TaskName = "VisionGuard")
$ErrorActionPreference = "Stop"
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host ("Da go tac vu '" + $TaskName + "'.") -ForegroundColor Green
} catch {
    Write-Host ("Khong tim thay tac vu '" + $TaskName + "'.") -ForegroundColor Yellow
}
Write-Host "Phan mem va du lieu van con nguyen trong thu muc nay."
