"""Poller leadership across real processes: one poller, hand-off on exit, no duplicate alerts."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "frontend_harness.py"


@pytest.fixture
def env(tmp_path):
    appdata = tmp_path / "appdata"
    out = tmp_path / "out"
    appdata.mkdir()
    out.mkdir()
    mutex = rf"Local\QuotaTest-{uuid.uuid4().hex}"
    procs = []

    def start(name, used=85.0):
        e = dict(os.environ, APPDATA=str(appdata))
        p = subprocess.Popen([sys.executable, str(HARNESS), name, mutex, str(out), str(used)], env=e,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        procs.append(p)
        return p

    yield start, out
    for p in procs:
        p.kill()
        p.wait()


def roles(out, *names):
    got = {}
    for n in names:
        f = out / f"{n}.role"
        got[n] = f.read_text(encoding="utf-8") if f.exists() else None
    return got


def wait_for(cond, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def alerts(out):
    f = out / "alerts.log"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_two_frontends_one_poller_and_handoff(env):
    start, out = env
    tray, widget = start("tray"), start("widget")
    assert wait_for(lambda: None not in roles(out, "tray", "widget").values(), 20)
    time.sleep(2.5)  # several leadership retries by the follower
    r = roles(out, "tray", "widget")
    assert sorted(r.values()) == ["follower", "poller"], r

    poller = tray if r["tray"] == "poller" else widget
    survivor = "widget" if poller is tray else "tray"
    poller.kill()
    poller.wait()
    t0 = time.monotonic()
    assert wait_for(lambda: roles(out, survivor)[survivor] == "poller", 60)
    assert time.monotonic() - t0 < 60
    # state.json names the new poller immediately, even though the 5-minute floor blocks a poll.
    appdata = out.parent / "appdata"
    assert wait_for(lambda: json.loads((appdata / "quota-tray" / "state.json").read_text())["poller"]["frontend"]
                    == survivor, 5)


def test_no_duplicate_alerts_with_both_running_or_after_handoff(env):
    start, out = env
    tray, widget = start("tray", used=85.0), start("widget", used=85.0)  # 15% left: crosses 20
    assert wait_for(lambda: len(alerts(out)) >= 1, 20)
    time.sleep(3)
    assert len(alerts(out)) == 1, alerts(out)

    r = roles(out, "tray", "widget")
    poller = tray if r["tray"] == "poller" else widget
    survivor = "widget" if poller is tray else "tray"
    poller.kill()
    poller.wait()
    assert wait_for(lambda: roles(out, survivor)[survivor] == "poller", 60)
    time.sleep(3)
    # The new poller resumes the alert history from state.json: no repeat.
    assert len(alerts(out)) == 1, alerts(out)
    # ...and the 5-minute floor carried over too: the provider was fetched exactly once.
    assert (out / "fetches.log").read_text(encoding="utf-8").count("\n") == 1
