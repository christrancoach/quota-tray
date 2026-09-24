"""Token keepalive: let Claude Code and Grok CLI refresh their own logins, with no inference.

Runs as an Engine pre-fetch hook, so it only ever runs in the poller process, right before
that provider's normal usage fetch (which therefore picks up the refreshed token).

- Claude: `claude doctor` when now + 5 min >= expiresAt. That's Claude Code's own refresh
  condition, so it covers both "about to expire" and "already expired".
- Grok: `grok models` with GROK_AUTH_EARLY_INVALIDATION_SECS=1800 when the token is within
  30 minutes of expiry.

This module only ever reads each credential file's expiry (plus Claude Code's own "login
cleared" marker). It never reads refresh tokens and never writes the files: the CLI refreshes
and saves its own login.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import keepalive_enabled, load_config
from .engine import Alert, ProviderState
from .providers.base import parse_time
from .providers.claude import default_credentials_path as claude_creds_path
from .providers.grok import default_auth_path as grok_auth_path
from .providers.grok import select_entry

log = logging.getLogger(__name__)
TIMEOUT_SECONDS = 20
MIN_INTERVAL_SECONDS = 3600      # at most one run per hour per CLI
CREATE_NO_WINDOW = 0x08000000


@dataclass
class ExpiryInfo:
    present: bool                    # credential file exists and has a login entry
    expires_at: datetime | None = None
    dead: bool = False               # the CLI cleared its own login (Claude Code after invalid_grant)


@dataclass
class RunResult:
    exit_code: int | None
    duration: float
    timed_out: bool = False
    error: str | None = None


@dataclass
class CliSpec:
    key: str                         # provider key and config.json keepalive key
    cli_name: str
    exe: str
    args: list[str]
    lead_seconds: int                # run when now + lead >= expiry
    read_expiry: Callable[[dict], ExpiryInfo]
    env: dict[str, str] = field(default_factory=dict)


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_claude_expiry(provider_cfg: dict) -> ExpiryInfo:
    path = Path(provider_cfg["credentials_path"]) if provider_cfg.get("credentials_path") else claude_creds_path()
    data = _read_json(path)
    oauth = data.get("claudeAiOauth") if data else None
    if not isinstance(oauth, dict):
        return ExpiryInfo(present=False)
    expires = oauth.get("expiresAt")
    # Claude Code's dead-login marker (OLn): accessToken "" and expiresAt 0. The refresh token
    # field is deliberately not read.
    dead = expires == 0 and not oauth.get("accessToken")
    return ExpiryInfo(present=True, expires_at=None if dead else parse_time(expires), dead=dead)


def read_grok_expiry(provider_cfg: dict) -> ExpiryInfo:
    path = Path(provider_cfg["auth_path"]) if provider_cfg.get("auth_path") else grok_auth_path()
    data = _read_json(path)
    if not data:
        return ExpiryInfo(present=False)
    try:
        entry = select_entry(data)
    except Exception:
        return ExpiryInfo(present=False)
    return ExpiryInfo(present=True, expires_at=parse_time(entry.get("expires_at")))


DEFAULT_SPECS = {
    "claude": CliSpec("claude", "Claude Code", "claude", ["doctor"], 5 * 60, read_claude_expiry),
    "grok": CliSpec("grok", "Grok CLI", "grok", ["models"], 30 * 60, read_grok_expiry,
                    env={"GROK_AUTH_EARLY_INVALIDATION_SECS": "1800"}),
}


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW, check=False)


def run_cli(argv: list[str], env_extra: dict[str, str], timeout: float = TIMEOUT_SECONDS) -> RunResult:
    """Empty temp cwd, no stdin, no console window, output discarded, whole tree killed on timeout."""
    workdir = tempfile.mkdtemp(prefix="quota-keepalive-")
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, cwd=workdir, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env={**os.environ, **env_extra},
                                creationflags=CREATE_NO_WINDOW)
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return RunResult(None, 0.0, error=f"could not start: {exc.strerror or exc}")
    try:
        code = proc.wait(timeout=timeout)
        return RunResult(code, time.monotonic() - start)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return RunResult(None, time.monotonic() - start, timed_out=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _iso(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "none"


class KeepaliveHook:
    def __init__(self, specs: dict[str, CliSpec] | None = None, *,
                 runner: Callable[[list[str], dict, float], RunResult] = run_cli,
                 enabled: Callable[[str], bool] = keepalive_enabled,
                 clock: Callable[[], float] = time.time):
        self.specs = specs or DEFAULT_SPECS
        self.runner, self.enabled, self.clock = runner, enabled, clock
        self.engine = None

    def attach(self, engine) -> None:
        self.engine = engine

    def _entry(self, key: str) -> dict:
        return self.engine.extras.setdefault("keepalive", {}).setdefault(key, {})

    def _save(self):
        self.engine.save_state()

    def before_fetch(self, provider, state: ProviderState) -> None:
        spec = self.specs.get(provider.key)
        if spec is None or self.engine is None:
            return
        entry = self._entry(spec.key)
        now = self.clock()
        before = spec.read_expiry(getattr(provider, "cfg", {}) or {})

        if entry.get("dead"):
            # Stopped after the CLI cleared its login. Resume once the user has logged in again.
            if before.present and not before.dead and before.expires_at and before.expires_at.timestamp() > now:
                entry.update(dead=False, dead_alerted=False)
                self._save()
                log.info("keepalive %s: new login found, keepalive resumed", spec.key)
            else:
                return
        if not self.enabled(spec.key):
            return
        if not before.present or before.dead or before.expires_at is None:
            return
        if now + spec.lead_seconds < before.expires_at.timestamp():
            return  # not due yet
        if now - float(entry.get("last_run") or 0) < MIN_INTERVAL_SECONDS:
            log.debug("keepalive %s: due but ran within the last hour; skipping", spec.key)
            return

        exe = self._resolve(spec)
        entry["last_run"] = now      # counts toward the hourly limit even if the run fails
        self._save()
        command = " ".join([spec.exe, *spec.args])
        already_expired = before.expires_at.timestamp() <= now
        if exe is None:
            result = RunResult(None, 0.0, error=f"{spec.exe} not found on PATH")
        else:
            result = self.runner([exe, *spec.args], dict(spec.env), TIMEOUT_SECONDS)

        after = spec.read_expiry(getattr(provider, "cfg", {}) or {})
        if after.dead:
            outcome = "login_cleared"
        elif after.expires_at and after.expires_at > before.expires_at:
            outcome = "refreshed"
        elif result.timed_out:
            outcome = "timeout"
        elif result.error:
            outcome = "error"
        else:
            outcome = "not_refreshed"
        entry["last_outcome"] = outcome
        entry["last_already_expired"] = already_expired
        entry["last_error"] = result.error
        if outcome == "refreshed" and already_expired:
            entry["confirmed_after_expiry"] = True   # the open question from the investigation, now observed
        log.info("keepalive %s: command=%r exit=%s duration=%.1fs expiry_before=%s expiry_after=%s "
                 "already_expired=%s outcome=%s%s", spec.key, command, result.exit_code, result.duration,
                 _iso(before.expires_at), _iso(after.expires_at), already_expired, outcome,
                 f" error={result.error}" if result.error else "")

        if outcome == "login_cleared":
            entry["dead"] = True
            if not entry.get("dead_alerted"):
                entry["dead_alerted"] = True
                self.engine.queue_alert(Alert(title=f"{spec.cli_name} login ended",
                                              message=f"Open {spec.cli_name} to log in. Keepalive is paused until you do."))
        self._save()

    def _resolve(self, spec: CliSpec) -> str | None:
        override = (load_config().get("keepalive", {}).get(spec.key, {}) or {}).get("cli_path")
        if override and Path(override).is_file():
            return override
        return shutil.which(spec.exe)
