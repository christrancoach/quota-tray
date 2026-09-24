"""A headless stand-in for a front end, used by test_leadership.py in separate processes.

Usage: python frontend_harness.py <name> <leader_mutex> <out_dir> <used_pct>
APPDATA must point at a scratch directory (set by the test).
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quota_core.models import Reading, utcnow  # noqa: E402
from quota_core.service import CoreService  # noqa: E402

name, mutex, out_dir, used = sys.argv[1], sys.argv[2], Path(sys.argv[3]), float(sys.argv[4])


class FakeProvider:
    key, name, cli_name = "codex", "ChatGPT (Codex)", "Codex CLI"

    def fetch(self):
        with open(out_dir / "fetches.log", "a", encoding="utf-8") as fh:
            fh.write(f"{name}\n")
        from datetime import timedelta
        return Reading(name=self.name, weekly_used_pct=used, weekly_resets_at=utcnow() + timedelta(days=3))


def notify(title, message):
    with open(out_dir / "alerts.log", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"frontend": name, "title": title}) + "\n")


svc = CoreService({"refresh_interval_minutes": 10, "thresholds": [20, 10]}, name, notify,
                  leader_name=mutex, retry_seconds=1.0, providers_factory=lambda _cfg: [FakeProvider()])
role_file = out_dir / f"{name}.role"
while True:  # killed by the test
    svc.tick()
    role_file.write_text("poller" if svc.is_poller else "follower", encoding="utf-8")
    time.sleep(0.2)
