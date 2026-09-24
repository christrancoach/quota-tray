"""Launcher used by PyInstaller and by Start-with-Windows when running from source."""
import sys

from quota_widget.app import main

sys.exit(main())
