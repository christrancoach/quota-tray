"""Per-user install/uninstall (no admin, no installer toolkit needed).

Install copies QuotaTray.exe and QuotaWidget.exe (from the folder the running exe is in) to
%LOCALAPPDATA%\\Programs\\QuotaTray, adds Start menu shortcuts, registers an "Apps & features" entry
(HKCU, uninstall = "QuotaTray.exe --uninstall"), and re-points any Start-with-Windows values at
the installed copies. Settings and data in %APPDATA%\\quota-tray are left alone by both.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from . import __version__, startup

APP_EXES = ("QuotaTray.exe", "QuotaWidget.exe")
RUN_VALUES = {"QuotaTray": "QuotaTray.exe", "QuotaWidget": "QuotaWidget.exe"}
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\QuotaTray"
CREATE_NO_WINDOW = 0x08000000


def install_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / "QuotaTray"


def start_menu_dir() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Quota Tray"


def running_exe_dir() -> Path | None:
    """Folder of the running frozen exe, or None when running from source."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def is_installed_copy() -> bool:
    d = running_exe_dir()
    return d is not None and d.resolve() == install_dir().resolve()


def make_shortcut(link: Path, target: Path, description: str) -> None:
    """Create a .lnk via the Windows Script Host COM object (no extra Python packages)."""
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
          f"$s.TargetPath='{target}';$s.WorkingDirectory='{target.parent}';"
          f"$s.Description='{description}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True,
                   creationflags=CREATE_NO_WINDOW, capture_output=True)


def install(source_dir: Path, dest: Path | None = None, menu: Path | None = None, *, register: bool = True,
            repoint_run: bool = True, shortcut=make_shortcut) -> list[Path]:
    """Copy both exes (whichever are present) and create shortcuts. Returns the installed exe paths."""
    dest = dest or install_dir()
    menu = menu or start_menu_dir()
    dest.mkdir(parents=True, exist_ok=True)
    menu.mkdir(parents=True, exist_ok=True)
    installed = []
    for exe in APP_EXES:
        src = source_dir / exe
        if not src.is_file():
            continue
        target = dest / exe
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        installed.append(target)
        shortcut(menu / (exe.replace(".exe", "").replace("Quota", "Quota ") + ".lnk"), target,
                 "LLM subscription quota")
    if not installed:
        raise FileNotFoundError(f"No QuotaTray.exe / QuotaWidget.exe next to {source_dir}")
    for value, exe in (RUN_VALUES.items() if repoint_run else ()):   # Start-with-Windows -> installed copy
        if startup.is_enabled(value) and (dest / exe).is_file():
            _set_run(value, f'"{dest / exe}"')
    if register:
        _register(dest)
    return installed


def _set_run(value: str, command: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup.RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, value, 0, winreg.REG_SZ, command)


def _register(dest: Path) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", "Quota Tray and Quota Widget"), ("DisplayVersion", __version__),
                          ("Publisher", "Quota Tray"), ("InstallLocation", str(dest)),
                          ("DisplayIcon", str(dest / "QuotaTray.exe")),
                          ("UninstallString", f'"{dest / "QuotaTray.exe"}" --uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)


def uninstall(dest: Path | None = None, menu: Path | None = None, *, deregister: bool = True,
              run_values=RUN_VALUES, remove_folder: bool = True) -> None:
    """Remove shortcuts, Start-with-Windows values, the Apps entry, then the program folder (after exit)."""
    dest = dest or install_dir()
    menu = menu or start_menu_dir()
    shutil.rmtree(menu, ignore_errors=True)
    for value in run_values:
        try:
            startup.set_enabled(value, False, "")
        except OSError:
            pass
    if deregister:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except OSError:
            pass
    if not remove_folder:
        return
    # The running exe can't delete itself: stop both apps and remove the folder once we've exited.
    subprocess.Popen(["cmd", "/c", "timeout /t 2 /nobreak >nul & taskkill /F /IM QuotaWidget.exe >nul 2>&1"
                      " & taskkill /F /IM QuotaTray.exe >nul 2>&1"], creationflags=CREATE_NO_WINDOW)
    subprocess.Popen(["cmd", "/c", f'timeout /t 4 /nobreak >nul & rmdir /s /q "{dest}"'],
                     creationflags=CREATE_NO_WINDOW)


def launch_installed_and_exit(exe: Path, quit_app) -> None:
    """Start the installed copy once this one has quit (single-instance guards would stop it otherwise)."""
    subprocess.Popen(["cmd", "/c", f'timeout /t 2 /nobreak >nul & start "" "{exe}"'], creationflags=CREATE_NO_WINDOW)
    quit_app()
