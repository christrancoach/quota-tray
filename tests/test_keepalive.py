"""Keepalive hook: trigger condition, hourly limit, success check, dead-login shutoff, timeout kill.

All credential files here are fakes in tmp_path. Real CLI logins are never touched.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from quota_core import keepalive as ka
from quota_core.engine import Engine
from quota_core.models import AuthStale, Reading, utcnow
from quota_core.providers.claude import ClaudeProvider

NOW = 1_800_000_000.0  # fixed fake clock


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


class FakeProvider:
    def __init__(self, key, cfg):
        self.key, self.name, self.cli_name, self.cfg = key, key.title(), f"{key} CLI", cfg

    def fetch(self):
        return Reading(name=self.name, weekly_used_pct=10, weekly_resets_at=utcnow() + timedelta(days=3))


def write_claude(path, expires_in_s=None, *, dead=False):
    oauth = ({"accessToken": "", "refreshToken": "", "expiresAt": 0} if dead else
             {"accessToken": "sk-ant-FAKE-access", "refreshToken": "sk-ant-FAKE-refresh",
              "expiresAt": int((NOW + expires_in_s) * 1000)})
    path.write_text(json.dumps({"claudeAiOauth": oauth}), encoding="utf-8")


def write_grok(path, expires_in_s):
    exp = datetime.fromtimestamp(NOW + expires_in_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f0Z")
    path.write_text(json.dumps({"https://auth.x.ai::c": {"key": "FAKE", "refresh_token": "FAKE", "expires_at": exp}}),
                    encoding="utf-8")


class Runner:
    """Stands in for the CLI: records calls and optionally rewrites the fake credential file."""

    def __init__(self, effect=None, result=None):
        self.calls, self.effect = [], effect
        self.result = result or ka.RunResult(0, 0.5)

    def __call__(self, argv, env, timeout):
        self.calls.append((argv, env, timeout))
        if self.effect:
            self.effect()
        return self.result


def setup(tmp_path, key="claude", runner=None, enabled=True, clock=lambda: NOW):
    creds = tmp_path / ("creds.json" if key == "claude" else "auth.json")
    cfg = {"credentials_path": str(creds)} if key == "claude" else {"auth_path": str(creds)}
    provider = FakeProvider(key, cfg)
    hook = ka.KeepaliveHook(runner=runner or Runner(), enabled=lambda _k: enabled, clock=clock)
    hook._resolve = lambda spec: f"C:/fake/{spec.exe}.exe"
    engine = Engine([provider], hooks=[hook], clock=clock)
    return creds, provider, hook, engine


def fire(engine, provider, hook):
    hook.before_fetch(provider, engine.states[provider.key])


# ---------- trigger condition ----------
@pytest.mark.parametrize("expires_in,should_run", [
    (6 * 60, False),        # outside Claude Code's 5-minute window
    (4 * 60, True),         # inside it
    (0, True),              # expiring now
    (-3600, True),          # already expired an hour ago
])
def test_claude_trigger_mirrors_claude_codes_condition(tmp_path, expires_in, should_run):
    runner = Runner()
    creds, provider, hook, engine = setup(tmp_path, runner=runner)
    write_claude(creds, expires_in)
    fire(engine, provider, hook)
    assert bool(runner.calls) == should_run
    if should_run:
        argv, env, timeout = runner.calls[0]
        assert argv[1:] == ["doctor"] and timeout == 20 and env == {}


@pytest.mark.parametrize("expires_in,should_run", [(31 * 60, False), (29 * 60, True), (-60, True)])
def test_grok_trigger_within_30_minutes(tmp_path, expires_in, should_run):
    runner = Runner()
    creds, provider, hook, engine = setup(tmp_path, key="grok", runner=runner)
    write_grok(creds, expires_in)
    fire(engine, provider, hook)
    assert bool(runner.calls) == should_run
    if should_run:
        argv, env, _ = runner.calls[0]
        assert argv[1:] == ["models"] and env == {"GROK_AUTH_EARLY_INVALIDATION_SECS": "1800"}


def test_other_providers_and_disabled_toggle_never_run(tmp_path):
    runner = Runner()
    creds, provider, hook, engine = setup(tmp_path, runner=runner, enabled=False)
    write_claude(creds, 60)
    fire(engine, provider, hook)
    codex = FakeProvider("codex", {})
    engine.states["codex"] = engine.states["claude"]
    hook.before_fetch(codex, engine.states["claude"])
    assert runner.calls == []


# ---------- rate limit ----------
def test_at_most_once_per_hour_and_persisted(tmp_path):
    t = [NOW]
    runner = Runner()
    creds, provider, hook, engine = setup(tmp_path, runner=runner, clock=lambda: t[0])
    write_claude(creds, 60)  # due, and the fake runner doesn't refresh it
    fire(engine, provider, hook)
    t[0] += 30 * 60
    fire(engine, provider, hook)
    assert len(runner.calls) == 1

    # A new poller process (e.g. after a hand-off) reads the last run from state.json.
    runner2 = Runner()
    hook2 = ka.KeepaliveHook(runner=runner2, enabled=lambda _k: True, clock=lambda: t[0])
    hook2._resolve = hook._resolve
    engine2 = Engine([provider], hooks=[hook2], clock=lambda: t[0])
    fire(engine2, provider, hook2)
    assert runner2.calls == []
    t[0] += 31 * 60  # now more than an hour after the first run
    fire(engine2, provider, hook2)
    assert len(runner2.calls) == 1


# ---------- success check ----------
def test_success_means_expiry_moved_forward(tmp_path):
    creds = tmp_path / "creds.json"
    runner = Runner(effect=lambda: write_claude(creds, 8 * 3600))
    creds, provider, hook, engine = setup(tmp_path, runner=runner)
    write_claude(creds, -600)
    fire(engine, provider, hook)
    assert engine.extras["keepalive"]["claude"]["last_outcome"] == "refreshed"


@pytest.mark.parametrize("result,outcome", [
    (ka.RunResult(0, 1.0), "not_refreshed"),                 # ran fine but expiry unchanged
    (ka.RunResult(None, 20.0, timed_out=True), "timeout"),
    (ka.RunResult(None, 0.0, error="could not start"), "error"),
])
def test_failed_runs_are_not_success(tmp_path, result, outcome):
    creds, provider, hook, engine = setup(tmp_path, runner=Runner(result=result))
    write_claude(creds, 60)
    before = creds.read_bytes()
    fire(engine, provider, hook)
    assert engine.extras["keepalive"]["claude"]["last_outcome"] == outcome
    assert creds.read_bytes() == before  # the hook itself never writes the credential file


# ---------- dead-login shutoff ----------
def test_cleared_login_stops_keepalive_and_alerts_once(tmp_path):
    t = [NOW]
    creds = tmp_path / "creds.json"
    runner = Runner(effect=lambda: write_claude(creds, dead=True))  # Claude Code got invalid_grant
    creds, provider, hook, engine = setup(tmp_path, runner=runner, clock=lambda: t[0])
    write_claude(creds, -60)
    fire(engine, provider, hook)
    entry = engine.extras["keepalive"]["claude"]
    assert entry["last_outcome"] == "login_cleared" and entry["dead"] is True
    alerts = engine.pending_alerts()
    assert len(alerts) == 1 and "log in" in alerts[0].message

    # Hours later: still stopped, no second alert, card says to log in.
    t[0] += 5 * 3600
    fire(engine, provider, hook)
    assert len(runner.calls) == 1 and engine.pending_alerts() == []
    with pytest.raises(AuthStale, match="to log in"):
        ClaudeProvider({"credentials_path": str(creds)}).fetch()

    # The user logs in again: keepalive resumes by itself.
    runner.effect = None
    write_claude(creds, (t[0] - NOW) + 8 * 3600)  # valid for 8 h from the current fake time
    fire(engine, provider, hook)
    assert engine.extras["keepalive"]["claude"]["dead"] is False


def test_already_dead_login_is_not_run(tmp_path):
    runner = Runner()
    creds, provider, hook, engine = setup(tmp_path, runner=runner)
    write_claude(creds, dead=True)
    fire(engine, provider, hook)
    assert runner.calls == []


# ---------- real subprocess: timeout kills the whole tree ----------
def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def test_run_cli_timeout_kills_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "parent.py"
    script.write_text(
        "import subprocess, sys, time, pathlib\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path(r'{pidfile}').write_text(str(c.pid))\n"
        "time.sleep(60)\n", encoding="utf-8")
    t0 = time.monotonic()
    result = ka.run_cli([sys.executable, str(script)], {}, timeout=3)
    assert result.timed_out and result.exit_code is None
    assert time.monotonic() - t0 < 15
    child = int(pidfile.read_text())
    time.sleep(0.5)
    assert not _alive(child)


def test_run_cli_reports_missing_executable():
    result = ka.run_cli([r"C:\definitely\not\here.exe"], {}, timeout=3)
    assert result.error and not result.timed_out
