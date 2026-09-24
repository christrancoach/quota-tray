"""Incremental index of token usage from the CLIs' local session logs.

Reads only usage metadata (model, token counts, timestamp, session id). The text of prompts and
replies is never stored. Everything lives in %APPDATA%\\quota-tray\\usage.db:

- files:  one row per log file with its size/mtime, how far it has been read (append-only JSONL
          is resumed from that byte offset) and small per-file parser state.
- events: one row per model call / turn with token counts, keyed so re-reading never double
          counts (Claude Code writes the same reply on several lines; resumed sessions repeat it).

Sources:
- Claude Code: <config dir>/projects/**/*.jsonl, assistant entries with message.usage
- Codex:       <CODEX_HOME>/sessions/**/rollout-*.jsonl, deltas of token_count totals
- Grok:        ~/.grok/sessions/*/*/usage.json, per turn, including Grok's own cost
- Gemini CLI:  ~/.gemini/tmp/*/chats/*.json[l], per reply
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from ..config import app_dir
from ..providers.base import parse_time

log = logging.getLogger(__name__)
SCHEMA_VERSION = 2   # v2: ctx (request size), cw_1h (1-hour cache writes), speed
TICKS_PER_USD = 1e10  # Grok CLI cost unit


def db_path() -> Path:
    return app_dir() / "usage.db"


def claude_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(env) if env else Path.home() / ".claude") / "projects"


def codex_dir() -> Path:
    env = os.environ.get("CODEX_HOME")
    return (Path(env) if env else Path.home() / ".codex") / "sessions"


def grok_dir() -> Path:
    env = os.environ.get("GROK_HOME")
    return (Path(env) if env else Path.home() / ".grok") / "sessions"


def gemini_dir() -> Path:
    env = os.environ.get("GEMINI_CLI_HOME")
    return (Path(env) if env else Path.home() / ".gemini") / "tmp"


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or db_path()), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver != SCHEMA_VERSION:
        con.executescript("""
            DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS events;
            CREATE TABLE files (path TEXT PRIMARY KEY, provider TEXT, mtime REAL, size INTEGER,
                                offset INTEGER, state TEXT);
            CREATE TABLE events (key TEXT PRIMARY KEY, provider TEXT, session TEXT, model TEXT,
                                 ts REAL, day TEXT, uncached INTEGER, cache_read INTEGER,
                                 cache_write INTEGER, output INTEGER, reasoning INTEGER,
                                 native_cost REAL, file TEXT, ctx INTEGER, cw_1h INTEGER, speed TEXT);
            CREATE INDEX events_day ON events(day);
        """)
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        con.commit()
    return con


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")  # local calendar day


UPSERT = """INSERT INTO events (key, provider, session, model, ts, day, uncached, cache_read, cache_write,
                                output, reasoning, native_cost, file, ctx, cw_1h, speed)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
              uncached=max(uncached, excluded.uncached), cache_read=max(cache_read, excluded.cache_read),
              cache_write=max(cache_write, excluded.cache_write), output=max(output, excluded.output),
              reasoning=max(reasoning, excluded.reasoning), ctx=max(ctx, excluded.ctx),
              cw_1h=max(cw_1h, excluded.cw_1h)"""


def _row(key, provider, session, model, ts, uncached=0, cache_read=0, cache_write=0, output=0, reasoning=0,
         native_cost=None, file="", ctx=0, cw_1h=0, speed="standard"):
    return (key, provider, session, model, ts, _day(ts), int(uncached), int(cache_read), int(cache_write),
            int(output), int(reasoning), native_cost, file, int(ctx), int(cw_1h), speed or "standard")


# ---------------------------------------------------------------- readers
def _read_new_lines(path: Path, offset: int) -> tuple[list[tuple[int, str]], int]:
    """Complete lines appended since `offset` -> ([(line_offset, text)], new_offset)."""
    out = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        pos = offset
        for raw in fh:
            if not raw.endswith(b"\n"):  # still being written; pick it up next time
                break
            out.append((pos, raw.decode("utf-8", "replace")))
            pos += len(raw)
    return out, pos


def parse_claude_lines(lines: Iterable[tuple[int, str]], file: str) -> list[tuple]:
    rows = []
    for pos, line in lines:
        if '"usage"' not in line or '"assistant"' not in line:
            continue  # cheap skip: most lines are user turns and tool output
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = d.get("message")
        if d.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        u, model = msg.get("usage"), msg.get("model")
        if not isinstance(u, dict) or not isinstance(model, str) or model.startswith("<"):
            continue
        ts = parse_time(d.get("timestamp"))
        if ts is None:
            continue
        mid = msg.get("id")
        key = f"claude:{mid}:{d.get('requestId', '')}" if mid else f"claude:{file}:{pos}"
        unc, cr, cw = (int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                     "cache_creation_input_tokens"))
        split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
        rows.append(_row(key, "claude", d.get("sessionId") or file, model, ts.timestamp(),
                         uncached=unc, cache_read=cr, cache_write=cw, output=u.get("output_tokens") or 0,
                         file=file, ctx=unc + cr + cw, cw_1h=min(cw, int(split.get("ephemeral_1h_input_tokens") or 0)),
                         speed=u.get("speed") if isinstance(u.get("speed"), str) else "standard"))
    return rows


CODEX_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
                "reasoning_output_tokens")


def parse_codex_lines(lines: Iterable[tuple[int, str]], file: str, state: dict) -> list[tuple]:
    """Usage = change in the running total between token_count events (robust to repeats)."""
    rows = []
    for pos, line in lines:
        if '"turn_context"' not in line and '"token_count"' not in line and '"session_meta"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        pl = d.get("payload") or {}
        if d.get("type") == "session_meta" and isinstance(pl.get("id"), str):
            state["session"] = pl["id"]
        elif d.get("type") == "turn_context" and isinstance(pl.get("model"), str):
            state["model"] = pl["model"]
        elif pl.get("type") == "token_count" and isinstance(pl.get("info"), dict):
            total = pl["info"].get("total_token_usage")
            ts = parse_time(d.get("timestamp"))
            if not isinstance(total, dict) or ts is None:
                continue
            prev = state.get("prev") or {}
            cur = {f: int(total.get(f) or 0) for f in CODEX_FIELDS}
            if any(cur[f] < prev.get(f, 0) for f in CODEX_FIELDS):
                prev = {}  # counter reset (new context): count the new total from zero
            delta = {f: cur[f] - prev.get(f, 0) for f in CODEX_FIELDS}
            state["prev"] = cur
            if not any(delta.values()):
                continue
            cached, cw = delta["cached_input_tokens"], delta["cache_write_input_tokens"]
            last = pl["info"].get("last_token_usage") if isinstance(pl["info"].get("last_token_usage"), dict) else {}
            ctx = int(last.get("input_tokens") or delta["input_tokens"])   # size of the request (long-context pricing)
            rows.append(_row(f"codex:{file}:{pos}", "codex", state.get("session") or Path(file).stem,
                             state.get("model") or "unknown", ts.timestamp(),
                             uncached=max(0, delta["input_tokens"] - cached - cw), cache_read=cached, cache_write=cw,
                             output=delta["output_tokens"], reasoning=delta["reasoning_output_tokens"], file=file,
                             ctx=ctx))
    return rows


def parse_grok_usage(data: dict, file: str) -> list[tuple]:
    session = data.get("sessionId") or Path(file).parent.name
    rows = []
    for turn in data.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        ts = parse_time(turn.get("endedAt"))
        if ts is None:
            continue
        per_model = turn.get("modelUsage") if isinstance(turn.get("modelUsage"), dict) else {
            turn.get("primaryModelId") or "unknown": turn}
        for model, u in per_model.items():
            if not isinstance(u, dict):
                continue
            inp, cr, cw = (int(u.get(k) or 0) for k in ("inputTokens", "cachedReadTokens", "cacheCreationTokens"))
            ticks = u.get("costUsdTicks")
            rows.append(_row(f"grok:{session}:{turn.get('turnNumber')}:{model}", "grok", session, model, ts.timestamp(),
                             uncached=max(0, inp - cr - cw), cache_read=cr, cache_write=cw,
                             output=int(u.get("outputTokens") or 0), reasoning=int(u.get("reasoningTokens") or 0),
                             native_cost=ticks / TICKS_PER_USD if isinstance(ticks, (int, float)) else None,
                             file=file))
    return rows


def parse_gemini_chat(text: str, file: str) -> list[tuple]:
    """Gemini CLI chat recording: .jsonl records (a reply is appended again once its tokens arrive)
    or an older .json document with a messages list."""
    records, session = [], Path(file).stem
    stripped = text.lstrip()
    if stripped.startswith("{") and file.endswith(".json"):
        try:
            doc = json.loads(text)
            session = doc.get("sessionId") or session
            records = doc.get("messages") or []
        except json.JSONDecodeError:
            records = []
    else:
        for line in text.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("sessionId") and "type" not in d:
                session = d["sessionId"]
            records.append(d)
    rows = []
    for m in records:
        if not isinstance(m, dict) or m.get("type") != "gemini" or not isinstance(m.get("tokens"), dict):
            continue
        t = m["tokens"]
        ts = parse_time(m.get("timestamp"))
        if ts is None:
            continue
        cached = int(t.get("cached") or 0)
        prompt = int(t.get("input") or 0) + int(t.get("tool") or 0)
        rows.append(_row(f"gemini:{session}:{m.get('id')}", "gemini", session, m.get("model") or "gemini",
                         ts.timestamp(), uncached=max(0, prompt - cached), cache_read=cached,
                         output=int(t.get("output") or 0) + int(t.get("thoughts") or 0),
                         reasoning=int(t.get("thoughts") or 0), file=file, ctx=prompt))
    return rows


# ---------------------------------------------------------------- indexing
def _sources() -> list[tuple[str, Path, str]]:
    out = []
    if claude_dir().is_dir():
        out += [("claude", p, "append") for p in claude_dir().rglob("*.jsonl")]
    if codex_dir().is_dir():
        out += [("codex", p, "append") for p in codex_dir().rglob("rollout-*.jsonl")]
    if grok_dir().is_dir():
        out += [("grok", p, "whole") for p in grok_dir().glob("*/*/usage.json")]
    if gemini_dir().is_dir():
        out += [("gemini", p, "whole") for pat in ("*/chats/*.json", "*/chats/*.jsonl") for p in gemini_dir().glob(pat)]
    return out


def update(progress: Callable[[int, int], None] | None = None, path: Path | None = None) -> dict:
    """Bring the index up to date. Returns {"files": n, "changed": n, "events_added": n, "seconds": s}."""
    t0 = time.monotonic()
    con = connect(path)
    known = {r[0]: r for r in con.execute("SELECT path, provider, mtime, size, offset, state FROM files")}
    sources = _sources()
    changed = 0
    before = con.execute("SELECT count(*) FROM events").fetchone()[0]
    for i, (provider, p, mode) in enumerate(sources):
        if progress and i % 25 == 0:
            progress(i, len(sources))
        sp = str(p)
        try:
            st = p.stat()
        except OSError:
            continue
        prev = known.get(sp)
        if prev and prev[2] == st.st_mtime and prev[3] == st.st_size:
            continue
        changed += 1
        state = json.loads(prev[5]) if prev and prev[5] else {}
        try:
            if mode == "whole":
                text = p.read_text(encoding="utf-8", errors="replace")
                con.execute("DELETE FROM events WHERE file=?", (sp,))
                if provider == "gemini":
                    rows = parse_gemini_chat(text, sp)
                else:
                    data = json.loads(text)
                    rows = parse_grok_usage(data if isinstance(data, dict) else {}, sp)
                new_offset = st.st_size
            else:
                offset = prev[4] if prev and st.st_size >= prev[3] else 0
                if offset == 0 and prev:  # truncated or rewritten: start over for this file
                    con.execute("DELETE FROM events WHERE file=?", (sp,))
                    state = {}
                lines, new_offset = _read_new_lines(p, offset)
                rows = (parse_claude_lines(lines, sp) if provider == "claude"
                        else parse_codex_lines(lines, sp, state))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.info("usage index: skipping %s: %s", p.name, exc)
            continue
        con.executemany(UPSERT, rows)
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                    (sp, provider, st.st_mtime, st.st_size, new_offset, json.dumps(state)))
        if changed % 50 == 0:
            con.commit()
    con.commit()
    if progress:
        progress(len(sources), len(sources))
    added = con.execute("SELECT count(*) FROM events").fetchone()[0] - before
    con.close()
    return {"files": len(sources), "changed": changed, "events_added": added, "seconds": time.monotonic() - t0}
