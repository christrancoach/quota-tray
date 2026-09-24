"""Settings window: providers, refresh, alerts, quiet hours, keepalive and prices, saved to config.json.

Running front ends pick the changes up within a few seconds (CoreService watches config.json).
"""
from __future__ import annotations

import copy
import json
import os

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout, QWidget)

from quota_core.config import DEFAULT_CONFIG, MIN_POLL_SECONDS, app_dir, config_path, load_config
from quota_core.usage.pricing import overrides_path

CLAUDE_RESETS_WARNING = (
    "Anthropic only returns banked-reset details to Claude Code. To show them, this app presents itself "
    "as Claude Code by sending Claude Code's User-Agent on one read-only request, using your existing "
    "Claude Code login.\n\n"
    "This is not an official or supported API. It may violate Anthropic's terms of service, and it can "
    "stop working at any time. Your usage bars don't depend on it.\n\nTurn it on anyway?")

PROVIDER_LABELS = {"claude": "Claude", "codex": "ChatGPT (Codex)", "grok": "Grok"}


def parse_thresholds(text: str) -> list[int]:
    """'20, 10' -> [20, 10]; ignores junk and values outside 1..99."""
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip().rstrip("%")
        if part.isdigit() and 0 < int(part) < 100:
            out.append(int(part))
    return sorted(set(out), reverse=True)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quota settings")
        self.setMinimumWidth(520)
        self.cfg = load_config()
        lay = QVBoxLayout(self)

        # Providers
        box = QGroupBox("Providers")
        form = QFormLayout(box)
        self.enabled, self.names = {}, {}
        for key, default in PROVIDER_LABELS.items():
            pc = self.cfg["providers"].get(key, {})
            row = QHBoxLayout()
            cb = QCheckBox("Show")
            cb.setChecked(bool(pc.get("enabled", True)))
            name = QLineEdit(pc.get("display_name") or default)
            name.setAccessibleName(f"{default} display name")
            row.addWidget(cb)
            row.addWidget(name, 1)
            w = QWidget()
            w.setLayout(row)
            form.addRow(default, w)
            self.enabled[key], self.names[key] = cb, name
        self.codex_resets = QCheckBox("Show ChatGPT (Codex) banked resets")
        self.codex_resets.setChecked(bool(self.cfg["providers"]["codex"].get("reset_info", True)))
        form.addRow(self.codex_resets)
        lay.addWidget(box)

        # Unofficial sources: off by default, each asks for confirmation before it's turned on
        box = QGroupBox("Unofficial sources (off by default)")
        form = QFormLayout(box)
        self.claude_resets = QCheckBox("Claude banked resets via Claude Code client (unofficial)")
        self.claude_resets.setChecked(self.cfg["providers"]["claude"].get("reset_info") is True)
        self.claude_resets.clicked.connect(lambda on: self._confirm(self.claude_resets, on, CLAUDE_RESETS_WARNING))
        form.addRow(self.claude_resets)
        lay.addWidget(box)

        # Refresh
        box = QGroupBox("Refresh")
        form = QFormLayout(box)
        self.interval = QSpinBox()
        self.interval.setRange(MIN_POLL_SECONDS // 60, 240)
        self.interval.setSuffix(" min")
        self.interval.setValue(max(MIN_POLL_SECONDS // 60, int(self.cfg.get("refresh_interval_minutes", 10))))
        form.addRow("Check every", self.interval)
        self.update_url = QLineEdit(self.cfg.get("update_url") or "")
        self.update_url.setPlaceholderText("off (GitHub releases API URL or a version.json you publish)")
        form.addRow("Update check", self.update_url)
        lay.addWidget(box)

        # Alerts
        a = self.cfg.get("alerts") or {}
        box = QGroupBox("Alerts")
        form = QFormLayout(box)
        self.weekly = QLineEdit(", ".join(str(x) for x in self.cfg.get("thresholds", [20, 10])))
        self.weekly.setPlaceholderText("e.g. 20, 10")
        self.short = QLineEdit(", ".join(str(x) for x in a.get("short_thresholds") or []))
        self.short.setPlaceholderText("off (e.g. 20)")
        self.soon = QSpinBox()
        self.soon.setRange(0, 48)
        self.soon.setSuffix(" h")
        self.soon.setSpecialValueText("off")
        self.soon.setValue(int(a.get("resets_soon_hours", 3) or 0))
        self.back = QCheckBox("Tell me when a weekly pool that ran low has reset")
        self.back.setChecked(bool(a.get("reset_back", True)))
        self.banked = QCheckBox("Remind me of a usable banked reset when I'm low")
        self.banked.setChecked(bool(a.get("banked_reset_hint", True)))
        q = a.get("quiet_hours") or {}
        self.quiet = QCheckBox("Quiet hours (alerts are held until they end)")
        self.quiet.setChecked(bool(q.get("enabled")))
        self.q_start, self.q_end = QTimeEdit(), QTimeEdit()
        for w_, v in ((self.q_start, q.get("start", "22:00")), (self.q_end, q.get("end", "08:00"))):
            w_.setDisplayFormat("HH:mm")
            w_.setTime(QTime.fromString(v, "HH:mm") if QTime.fromString(v, "HH:mm").isValid() else QTime(22, 0))
        qrow = QHBoxLayout()
        qrow.addWidget(self.quiet)
        qrow.addWidget(QLabel("from"))
        qrow.addWidget(self.q_start)
        qrow.addWidget(QLabel("to"))
        qrow.addWidget(self.q_end)
        qw = QWidget()
        qw.setLayout(qrow)
        form.addRow("Weekly % left", self.weekly)
        form.addRow("5-hour % left", self.short)
        form.addRow("Low pool resets within", self.soon)
        form.addRow(self.back)
        form.addRow(self.banked)
        form.addRow(qw)
        lay.addWidget(box)

        # Keepalive
        k = self.cfg.get("keepalive") or {}
        box = QGroupBox("Keepalive (let the CLI renew its own login; no prompts, no quota)")
        form = QFormLayout(box)
        self.ka_claude = QCheckBox("Claude Code (runs `claude doctor` when the token is about to expire)")
        self.ka_claude.setChecked(bool((k.get("claude") or {}).get("enabled", True)))
        self.ka_grok = QCheckBox("Grok CLI (runs `grok models` within 30 minutes of expiry)")
        self.ka_grok.setChecked(bool((k.get("grok") or {}).get("enabled", True)))
        form.addRow(self.ka_claude)
        form.addRow(self.ka_grok)
        lay.addWidget(box)

        # Prices + buttons
        links = QHBoxLayout()
        prices = QPushButton("Edit usage-report prices…")
        prices.clicked.connect(self.open_prices)
        folder = QPushButton("Open data folder")
        folder.clicked.connect(lambda: os.startfile(str(app_dir())))
        links.addWidget(prices)
        links.addWidget(folder)
        links.addStretch(1)
        lay.addLayout(links)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def result_config(self) -> dict:
        """The edited configuration (everything else in config.json is kept as it was)."""
        try:
            base = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            base = copy.deepcopy(DEFAULT_CONFIG)
        cfg = copy.deepcopy(base)
        provs = cfg.setdefault("providers", {})
        for key in PROVIDER_LABELS:
            pc = provs.setdefault(key, {})
            pc["enabled"] = self.enabled[key].isChecked()
            pc["display_name"] = self.names[key].text().strip() or PROVIDER_LABELS[key]
        provs["claude"]["reset_info"] = self.claude_resets.isChecked()
        provs["codex"]["reset_info"] = self.codex_resets.isChecked()
        cfg["refresh_interval_minutes"] = self.interval.value()
        url = self.update_url.text().strip()
        cfg["update_url"] = url if url.startswith("https://") else None
        cfg["thresholds"] = parse_thresholds(self.weekly.text()) or [20, 10]
        al = cfg.setdefault("alerts", {})
        al["short_thresholds"] = parse_thresholds(self.short.text())
        al["resets_soon_hours"] = self.soon.value()
        al["reset_back"] = self.back.isChecked()
        al["banked_reset_hint"] = self.banked.isChecked()
        al["quiet_hours"] = {"enabled": self.quiet.isChecked(), "start": self.q_start.time().toString("HH:mm"),
                             "end": self.q_end.time().toString("HH:mm")}
        ka = cfg.setdefault("keepalive", {})
        ka.setdefault("claude", {})["enabled"] = self.ka_claude.isChecked()
        ka.setdefault("grok", {})["enabled"] = self.ka_grok.isChecked()
        return cfg

    def _confirm(self, box: QCheckBox, on: bool, text: str) -> None:
        """Turning an unofficial source on needs an explicit Yes; anything else leaves it off."""
        if not on:
            return
        answer = QMessageBox.warning(self, "Unofficial source", text,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            box.setChecked(False)

    def save(self):
        path = config_path()
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.result_config(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def save_and_close(self):
        self.save()
        self.accept()

    @staticmethod
    def open_prices():
        path = overrides_path()
        if not path.exists():
            path.write_text('{\n  "_example-model": {"input": 1.0, "output": 5.0, "cache_read": 0.1, '
                            '"cache_write": 1.25}\n}\n', encoding="utf-8")
        os.startfile(str(path))


_dialog: SettingsDialog | None = None


def open_settings() -> SettingsDialog:
    global _dialog
    if _dialog is None or not _dialog.isVisible():
        _dialog = SettingsDialog()
    _dialog.show()
    _dialog.raise_()
    _dialog.activateWindow()
    return _dialog
