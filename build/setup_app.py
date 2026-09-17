"""VisionGuard Setup - the installer the customer double-clicks.

Built into its own exe with the whole application zipped inside it, so a target machine
needs nothing at all: no Python, no pip, no internet.

What it does:

    1. asks where to install (default C:\\VisionGuard - deliberately NOT Program Files,
       because config, logs, events and recorded clips live beside the exe and a standard
       user cannot write there)
    2. unpacks, keeping any config that is already on the machine - reinstalling must never
       throw away a commissioned site's cameras, zones and PLC addresses
    3. makes Start Menu and Desktop shortcuts
    4. registers the logon task so it comes back after a power cut
    5. runs the pre-flight check and shows the result
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APP_NAME = "VisionGuard"
DEFAULT_DIR = Path(r"C:\VisionGuard")
PAYLOAD = "payload.zip"
#: never overwritten on an upgrade - this is the commissioned site
KEEP = ("config", "events", "logs", "models")


def resource(name: str) -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base or Path(__file__).parent) / name


def say(text: str = "") -> None:
    print(text, flush=True)


def rule(text: str = "") -> None:
    say("=" * 64)
    if text:
        say("  " + text)
        say("=" * 64)


def ask(prompt: str, default: str) -> str:
    try:
        answer = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        return default
    return answer or default


def yes(prompt: str, default: bool = True) -> bool:
    hint = "C/k" if default else "c/K"
    try:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer[0] in "cy1"


def unpack(target: Path) -> None:
    payload = resource(PAYLOAD)
    if not payload.exists():
        raise SystemExit(f"Thieu {PAYLOAD} trong bo cai.")
    target.mkdir(parents=True, exist_ok=True)

    keep_dir = Path(tempfile.mkdtemp(prefix="vg_keep_"))
    preserved = []
    for name in KEEP:
        src = target / name
        if src.exists() and any(src.iterdir()):
            shutil.copytree(src, keep_dir / name)
            preserved.append(name)
    if preserved:
        say(f"  Giu lai: {', '.join(preserved)}")

    say("  Dang giai nen...")
    with zipfile.ZipFile(payload) as z:
        members = z.infolist()
        total = len(members)
        for i, member in enumerate(members, 1):
            z.extract(member, target)
            if i % 250 == 0 or i == total:
                say(f"    {i}/{total} tep")

    for name in preserved:                       # put the site's own settings back
        src, dst = keep_dir / name, target / name
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
    shutil.rmtree(keep_dir, ignore_errors=True)


def shortcut(path: Path, target: Path, working: Path, description: str) -> bool:
    ps = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');"
        f"$s.TargetPath='{target}';"
        f"$s.WorkingDirectory='{working}';"
        f"$s.Description='{description}';"
        f"$s.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       check=True, capture_output=True, timeout=60)
        return True
    except Exception:
        return False


def make_shortcuts(target: Path) -> None:
    exe = target / f"{APP_NAME}.exe"
    launcher = target / f"{APP_NAME}Launcher.exe"
    desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
    start = (Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" /
             "Start Menu" / "Programs" / APP_NAME)
    start.mkdir(parents=True, exist_ok=True)

    made = 0
    made += shortcut(desktop / f"{APP_NAME}.lnk", exe, target, "VisionGuard")
    made += shortcut(start / f"{APP_NAME}.lnk", exe, target, "VisionGuard")
    made += shortcut(start / "VisionGuard (tu giam sat).lnk", launcher, target,
                     "Chay co kiem tra va tu khoi dong lai")
    say(f"  Da tao {made} loi tat.")


def register_task(target: Path) -> bool:
    launcher = target / f"{APP_NAME}Launcher.exe"
    ps = f"""
$ErrorActionPreference='Stop'
$a = New-ScheduledTaskAction -Execute '{launcher}' -WorkingDirectory '{target}'
$t = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t.Delay = 'PT30S'
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
     -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
     -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName '{APP_NAME}' -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName '{APP_NAME}' -Action $a -Trigger $t -Settings $s -Principal $p `
     -Description 'VisionGuard person-in-area monitoring' | Out-Null
"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       check=True, capture_output=True, timeout=120)
        return True
    except subprocess.CalledProcessError as exc:
        say("  Loi: " + (exc.stderr or b"").decode(errors="replace").strip()[:300])
        return False
    except Exception as exc:
        say(f"  Loi: {exc}")
        return False


def preflight(target: Path) -> int:
    exe = target / f"{APP_NAME}.exe"
    try:
        out = subprocess.run([str(exe), "--check"], capture_output=True, text=True,
                             cwd=str(target), timeout=300)
        say(out.stdout.strip())
        return out.returncode
    except Exception as exc:
        say(f"  Khong chay duoc kiem tra: {exc}")
        return 1


def main() -> int:
    rule(f"{APP_NAME} - Cai dat")
    say()
    say("  Phan mem giam sat nguoi trong vung nguy hiem, xuat tin hieu ra PLC Mitsubishi.")
    say("  Bo cai nay da kem san moi thu - may dich khong can cai Python.")
    say()

    target = Path(ask("Cai vao thu muc", str(DEFAULT_DIR))).expanduser()
    if target.exists() and any(target.iterdir()):
        say(f"  '{target}' da co du lieu. Cai de len se GIU LAI config, models, logs, events.")
        if not yes("  Tiep tuc?", True):
            say("Da huy.")
            return 1

    rule("1/4  Giai nen")
    try:
        unpack(target)
    except Exception as exc:
        say(f"  That bai: {exc}")
        return 1
    say("  Xong.")

    rule("2/4  Loi tat")
    make_shortcuts(target)

    rule("3/4  Tu khoi dong khi bat may")
    if yes("  Dang ky chay tu dong khi dang nhap Windows?", True):
        if register_task(target):
            say(f"  Da dang ky tac vu '{APP_NAME}' (tre 30 giay sau khi dang nhap).")
            say()
            say("  LUU Y: de sau khi mat dien may tu vao Windows roi tu chay phan mem,")
            say("  can bat tu dong dang nhap tren may nay (lenh: netplwiz).")
        else:
            say("  Dang ky that bai. Chay lai bo cai bang quyen Administrator neu can.")
    else:
        say("  Bo qua.")

    rule("4/4  Kiem tra he thong")
    code = preflight(target)

    say()
    rule("CAI DAT XONG")
    say()
    say(f"  Thu muc      : {target}")
    say(f"  Chay ngay    : {target / (APP_NAME + '.exe')}")
    say(f"  Tu giam sat  : {target / (APP_NAME + 'Launcher.exe')}")
    say(f"  Kiem tra lai : {APP_NAME}.exe --check")
    say(f"  Nhat ky      : {target / 'logs'}")
    say()
    if code == 1:
        say("  Con muc [FAIL] o tren - sua roi chay lai kiem tra truoc khi dua vao san xuat.")
    say()

    if yes("  Chay phan mem bay gio?", code != 1):
        subprocess.Popen([str(target / f"{APP_NAME}.exe")], cwd=str(target))
        say("  Dang mo...")
    try:
        input("\n  Nhan Enter de dong.")
    except EOFError:
        pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nDa huy.")
        sys.exit(1)
