"""Start-with-Windows via HKCU\\...\\Run (no admin rights needed).

Each front end has its own Run value, so the tray and the widget are toggled independently.
"""
from __future__ import annotations

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command(source_script: str) -> str:
    """Frozen: the running exe. From source: pythonw + the given launcher script in the repo root."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f'"{exe}" "{os.path.join(root, source_script)}"'


def is_enabled(value_name: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, value_name)
            return bool(value)
    except OSError:
        return False


def set_enabled(value_name: str, enabled: bool, source_script: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, launch_command(source_script))
        else:
            try:
                winreg.DeleteValue(k, value_name)
            except FileNotFoundError:
                pass
