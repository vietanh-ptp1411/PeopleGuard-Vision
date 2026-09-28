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
rem  KHONG dung mang / dau phay trong khoi powershell nay. cmd noi cac dong ^ lai roi
rem  powershell.exe tach lai theo dau phay, nen @($a, $b) den noi thanh MOT chuoi
rem  "$a $b" - vong foreach chay dung mot lan voi ca hai duong dan dinh vao nhau va
rem  Save() bao FileNotFoundException. Viet thang tung cai, khong vong lap.
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$d=[Environment]::GetFolderPath('Desktop');" ^
  "$m=[Environment]::GetFolderPath('StartMenu')+'\Programs\VisionGuard';" ^
  "New-Item -ItemType Directory -Force -Path $m | Out-Null;" ^
  "$s=$w.CreateShortcut($d+'\VisionGuard.lnk');" ^
  "$s.TargetPath='%CD%\VisionGuard.exe'; $s.WorkingDirectory='%CD%'; $s.Save();" ^
  "$s=$w.CreateShortcut($m+'\VisionGuard.lnk');" ^
  "$s.TargetPath='%CD%\VisionGuard.exe'; $s.WorkingDirectory='%CD%'; $s.Save();" ^
  "$s=$w.CreateShortcut($m+'\VisionGuard (tu giam sat).lnk');" ^
  "$s.TargetPath='%CD%\VisionGuardLauncher.exe'; $s.WorkingDirectory='%CD%'; $s.Save()"

rem  Bao cao do chinh powershell in ra, khong qua file tam: truoc day cho nao cung in
rem  "Xong." ke ca khi powershell vua bao loi ngay dong tren - buoc 1 bao thanh cong con
rem  loi tat thi khong co, va nguoi cai doc tiep xuong "mo loi tat ngoai Desktop".
powershell -NoProfile -Command ^
  "$p=[Environment]::GetFolderPath('Desktop')+'\VisionGuard.lnk';" ^
  "if(Test-Path $p){ '        Xong - da tao loi tat Desktop va Start Menu.' }" ^
  "else { '        [CANH BAO] Khong tao duoc loi tat ngoai Desktop.';" ^
  "       '                   Van chay duoc: bam dup VisionGuard.exe trong thu muc nay.' }"
echo.

echo   [2/3] Dang ky tu khoi dong khi dang nhap Windows...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$a=New-ScheduledTaskAction -Execute '%CD%\VisionGuardLauncher.exe' -WorkingDirectory '%CD%';" ^
  "$t=New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $t.Delay='PT15S';" ^
  "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew;" ^
  "$p=New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\$env:USERNAME\" -LogonType Interactive -RunLevel Limited;" ^
  "Unregister-ScheduledTask -TaskName 'VisionGuard' -Confirm:$false -ErrorAction SilentlyContinue;" ^
  "Register-ScheduledTask -TaskName 'VisionGuard' -Action $a -Trigger $t -Settings $s -Principal $p -Description 'VisionGuard person-in-area monitoring' | Out-Null;" ^
  "'        Da dang ky tac vu VisionGuard (tre 15 giay sau khi dang nhap).'"
echo.
echo         LUU Y: de sau khi mat dien may tu vao Windows roi tu chay phan mem,
echo         can bat tu dong dang nhap tren may nay (lenh: netplwiz).
echo.

rem  Lop thu hai, doc lap voi Task Scheduler: mot loi tat trong thu muc Startup cua
rem  Windows. Neu tac vu bi tat, bi xoa, hay bi chinh sach chan thi cai nay van chay.
rem  Hai co che cung ban luc khoi dong khong sao: ban thu hai thay da co ban dang chay
rem  thi tu thoat im lang (khong hien hop thoai vi khong co ai o do de bam).
powershell -NoProfile -Command ^
  "$w=New-Object -ComObject WScript.Shell;" ^
  "$u=[Environment]::GetFolderPath('Startup');" ^
  "$s=$w.CreateShortcut($u+'\VisionGuard.lnk');" ^
  "$s.TargetPath='%CD%\VisionGuardLauncher.exe'; $s.WorkingDirectory='%CD%'; $s.Save();" ^
  "if(Test-Path ($u+'\VisionGuard.lnk')){ '        Da them vao Startup cua Windows (lop du phong).' }" ^
  "else { '        [CANH BAO] Khong them duoc vao thu muc Startup.' }"
echo.
echo         Mo may la phan mem tu chay - nhung CHI KHI Windows tu dang nhap.
echo         Chua bat thi chay lenh  netplwiz  roi bo tich o "Users must enter...".
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
echo     3. Tab ZONES  : ve vung cam nguoi vao
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
