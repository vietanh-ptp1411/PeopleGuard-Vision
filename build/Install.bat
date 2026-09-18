@echo off
rem ===================================================================
rem  VisionGuard - cai dat tu thu muc da giai nen
rem
rem  Giai nen file zip ra mot thu muc GHI DUOC (vi du C:\VisionGuard),
rem  KHONG dat trong C:\Program Files - phan mem ghi config, log, lich su
rem  va video ngay canh file exe, ma o Program Files thi tai khoan thuong
rem  khong co quyen ghi.
rem
rem  Sau do bam dup file nay.
rem ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo ================================================================
echo   VisionGuard - cai dat
echo ================================================================
echo.
echo   Thu muc: %CD%
echo.

if not exist "VisionGuard.exe" (
    echo   [LOI] Khong thay VisionGuard.exe trong thu muc nay.
    echo         Hay giai nen ca file zip roi chay Install.bat ben trong.
    echo.
    pause
    exit /b 1
)

echo   [1/3] Tao loi tat...
powershell -NoProfile -Command ^
  "$d=[Environment]::GetFolderPath('Desktop');" ^
  "$m=[Environment]::GetFolderPath('StartMenu')+'\Programs\VisionGuard';" ^
  "New-Item -ItemType Directory -Force -Path $m | Out-Null;" ^
  "foreach($p in @($d+'\VisionGuard.lnk', $m+'\VisionGuard.lnk')){" ^
  "  $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);" ^
  "  $s.TargetPath='%CD%\VisionGuard.exe'; $s.WorkingDirectory='%CD%'; $s.Save() };" ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($m+'\VisionGuard (tu giam sat).lnk');" ^
  "$s.TargetPath='%CD%\VisionGuardLauncher.exe'; $s.WorkingDirectory='%CD%'; $s.Save()"
echo         Xong.
echo.

echo   [2/3] Dang ky tu khoi dong khi dang nhap Windows...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$a=New-ScheduledTaskAction -Execute '%CD%\VisionGuardLauncher.exe' -WorkingDirectory '%CD%';" ^
  "$t=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t.Delay='PT30S';" ^
  "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew;" ^
  "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\$env:USERNAME\" -LogonType Interactive -RunLevel Limited;" ^
  "Unregister-ScheduledTask -TaskName 'VisionGuard' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "Register-ScheduledTask -TaskName 'VisionGuard' -Action $a -Trigger $t -Settings $s -Principal $p -Description 'VisionGuard person-in-area monitoring' | Out-Null;" ^
  "'        Da dang ky tac vu VisionGuard (tre 30 giay sau khi dang nhap).'"
echo.
echo         LUU Y: de sau khi mat dien may tu vao Windows roi tu chay phan mem,
echo         can bat tu dong dang nhap tren may nay (lenh: netplwiz).
echo.

echo   [3/3] Kiem tra he thong...
echo.
"%CD%\VisionGuardLauncher.exe" --check-only
echo.
rem  0 = dat, 2 = dat nhung co canh bao, 1 = con loi phai sua truoc khi chay
if errorlevel 2 goto ready
if errorlevel 1 goto needconfig
goto ready

:needconfig
echo ================================================================
echo   CAN CAU HINH TRUOC KHI CHAY
echo ================================================================
echo.
echo   Phan mem duoc giao o trang thai CHUA CAU HINH, nen dong [FAIL]
echo   o tren la dung - khong phai loi cai dat.
echo.
echo   Lam theo thu tu nay:
echo.
echo     1. Mo VisionGuard.exe  - loi tat ngoai Desktop
echo     2. Tab CAMERA : nhap IP, tai khoan, mat khau camera
echo     3. Tab ROI    : ve vung cam nguoi vao
echo     4. Tab PLC    : nhap IP PLC, roi BO dau tich "Simulation"
echo                     De nguyen Simulation thi KHONG ghi gi ra PLC that.
echo     5. Luu lai, dong phan mem
echo     6. Chay lai:  VisionGuardLauncher.exe --check-only
echo        den khi khong con dong [FAIL] nao.
echo.
echo   Tu khoi dong da dang ky roi - cau hinh xong chi can dang xuat
echo   va dang nhap lai, khong phai cai lai.
echo.
goto paths

:ready
echo ================================================================
echo   XONG
echo ================================================================
echo.

:paths
echo   Chay ngay        : VisionGuard.exe  (hoac loi tat ngoai Desktop)
echo   Chay co giam sat : VisionGuardLauncher.exe
echo   Kiem tra lai     : VisionGuardLauncher.exe --check-only
echo   Nhat ky          : logs\
echo.
pause
