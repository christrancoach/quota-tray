"""Launcher used by PyInstaller and by Start-with-Windows when running from source."""
import sys

from quota_tray.app import main

sys.exit(main())
