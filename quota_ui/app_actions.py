"""Install / uninstall / update actions shared by the tray and the widget."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from quota_core import install


def can_install() -> bool:
    """Offer 'Install to this PC' when running a standalone exe that isn't the installed copy."""
    return install.running_exe_dir() is not None and not install.is_installed_copy()


def run_install(this_exe_name: str) -> None:
    src = install.running_exe_dir()
    if src is None:
        return
    dest = install.install_dir()
    answer = QMessageBox.question(
        None, "Install Quota Tray",
        f"Copy Quota Tray and Quota Widget to\n{dest}\n\nand add them to the Start menu and Apps & features?\n"
        "Your settings and data stay where they are.")
    if answer != QMessageBox.StandardButton.Yes:
        return
    try:
        installed = install.install(src)
    except Exception as exc:
        QMessageBox.warning(None, "Install Quota Tray", f"Install failed: {exc}")
        return
    target = next((p for p in installed if p.name == this_exe_name), installed[0])
    QMessageBox.information(None, "Install Quota Tray",
                            f"Installed. Switching to the installed copy now.\n\n"
                            f"Uninstall any time from Settings > Apps > Installed apps.")
    install.launch_installed_and_exit(target, QApplication.quit)


def handle_uninstall_arg() -> bool:
    """`QuotaTray.exe --uninstall` (what Apps & features runs). Returns True if it handled the request."""
    if "--uninstall" not in sys.argv:
        return False
    app = QApplication.instance() or QApplication(sys.argv)
    answer = QMessageBox.question(None, "Uninstall Quota Tray",
                                  "Remove Quota Tray and Quota Widget from this PC?\n\n"
                                  "Your settings and usage history in %APPDATA%\\quota-tray are kept.")
    if answer == QMessageBox.StandardButton.Yes:
        install.uninstall()
        QMessageBox.information(None, "Uninstall Quota Tray", "Quota Tray has been removed.")
    del app
    return True


def open_update(update: dict | None) -> None:
    if update and update.get("url"):
        os.startfile(update["url"])
