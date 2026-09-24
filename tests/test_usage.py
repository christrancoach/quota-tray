"""Usage report: log parsers, incremental index, pricing and aggregation, on synthetic logs only."""
import json
from datetime import date

import pytest

from quota_core.usage import index as ix
from quota_core.usage.pricing import PriceBook, normalize
from quota_core.usage.report import build, fmt_tokens

TS = "2026-09-20T10:00:00.000Z"


@pytest.fixture
def homes(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini"))
    for d in ("claude/projects/p1/sess-a/subagents", "codex/sessions/2026/09/20", "grok/sessions/proj/s1"):
        (tmp_path / d).mkdir(parents=True)
    return tmp_path


def claude_line(mid, out, *, model="claude-opus-5", session="sess-a", inp=3, cr=1000, cw=200, req="r1", ts=TS):
    return json.dumps({"type": "assistant", "sessionId": session, "requestId": req, "timestamp": ts,
                       "message": {"id": mid, "model": model, "content": [{"type": "text", "text": "hi"}],
                                   "usage": {"input_tokens": inp, "cache_read_input_tokens": cr,
                                             "cache_creation_input_tokens": cw, "output_tokens": out}}}) + "\n"


def write(path, text, mode="w"):
    with open(path, mode, encoding="utf-8") as fh:
        fh.write(text)


PRICES = PriceBook(overrides={}, litellm={
    "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6,
                      "cache_read_input_token_cost": 0.5e-6, "cache_creation_input_token_cost": 6.25e-6},
    "gpt-6-astra": {"input_cost_per_token": 10e-6, "output_cost_per_token": 50e-6,
                    "cache_read_input_token_cost": 1e-6},
})


def test_claude_streamed_lines_count_final_output_once(homes):
    f = homes / "claude/projects/p1/a.jsonl"
    # One reply written over three lines: early lines carry a partial output count.
    write(f, claude_line("m1", 7) + claude_line("m1", 7) + claude_line("m1", 502) + claude_line("m2", 40))
    ix.update()
    con = ix.connect()
    assert con.execute("SELECT count(*), sum(output) FROM events").fetchone() == (2, 542)


def test_claude_resumed_session_and_subagents(homes):
    main = homes / "claude/projects/p1/a.jsonl"
    resumed = homes / "claude/projects/p1/b.jsonl"          # a resumed session repeats earlier replies
    sub = homes / "claude/projects/p1/sess-a/subagents/agent-1.jsonl"
    write(main, claude_line("m1", 100))
    write(resumed, claude_line("m1", 100) + claude_line("m3", 10))
    write(sub, claude_line("s1", 50, model="claude-sonnet-5"))
    write(main, json.dumps({"type": "assistant", "message": {"id": "x", "model": "<synthetic>",
                                                            "usage": {"output_tokens": 9}}, "timestamp": TS}) + "\n", "a")
    ix.update()
    rows = dict(ix.connect().execute("SELECT key, output FROM events").fetchall())
    assert sorted(k.split(":")[1] for k in rows) == ["m1", "m3", "s1"]   # m1 once, synthetic skipped


def test_incremental_reads_only_new_lines(homes):
    f = homes / "claude/projects/p1/a.jsonl"
    write(f, claude_line("m1", 10))
    ix.update()
    write(f, claude_line("m2", 20), "a")
    write(f, '{"type": "assistant", "partial": ', "a")   # a line still being written
    stats = ix.update()
    assert stats["changed"] == 1
    assert ix.connect().execute("SELECT sum(output) FROM events").fetchone()[0] == 30
    assert ix.update()["changed"] == 0                      # nothing new: nothing re-read


def test_codex_counts_deltas_of_running_totals(homes):
    f = homes / "codex/sessions/2026/09/20/rollout-x.jsonl"

    def tc(inp, cached, out, reasoning=0):
        return json.dumps({"timestamp": TS, "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
                                  "reasoning_output_tokens": reasoning, "cache_write_input_tokens": 0}}}}) + "\n"
    write(f, json.dumps({"type": "session_meta", "payload": {"id": "codex-1"}}) + "\n" +
          json.dumps({"type": "turn_context", "payload": {"model": "gpt-6-astra"}}) + "\n" +
          tc(1000, 800, 50) + tc(1000, 800, 50) +              # repeated event: no double count
          tc(3000, 2500, 120, 30))
    ix.update()
    row = ix.connect().execute("SELECT count(*), sum(uncached), sum(cache_read), sum(output), min(model), min(session) "
                               "FROM events").fetchone()
    assert row == (2, 500, 2500, 120, "gpt-6-astra", "codex-1")


def test_grok_uses_the_clis_own_cost(homes):
    usage = {"sessionId": "g1", "turns": [
        {"turnNumber": 1, "endedAt": TS, "modelUsage": {"grok-4.6-build": {
            "inputTokens": 1000, "cachedReadTokens": 900, "cacheCreationTokens": 0, "outputTokens": 40,
            "reasoningTokens": 10, "costUsdTicks": 2_500_000_000}}}]}
    write(homes / "grok/sessions/proj/s1/usage.json", json.dumps(usage))
    ix.update()
    rep = build(30, today=date(2026, 9, 24), prices=PRICES)
    assert rep.cost == pytest.approx(0.25)                 # 2.5e9 ticks = $0.25
    assert rep.tokens.uncached == 100 and rep.tokens.cache_read == 900
    assert rep.models[0].priced


def test_report_totals_shares_days_and_unpriced(homes):
    write(homes / "claude/projects/p1/a.jsonl",
          claude_line("m1", 1000, inp=100, cr=10000, cw=0) +
          claude_line("m2", 10, model="claude-unknown-9", ts="2026-09-22T10:00:00Z"))
    ix.update()
    rep = build(7, today=date(2026, 9, 24), prices=PRICES)
    expected = 100 * 5e-6 + 10000 * 0.5e-6 + 1000 * 25e-6
    assert rep.cost == pytest.approx(expected)
    assert rep.cache_savings == pytest.approx(10000 * (5e-6 - 0.5e-6))
    assert len(rep.days) == 7 and rep.days[-1].key == "2026-09-24"
    assert rep.daily["claude"][2] == pytest.approx(expected)          # window Sep 18..24: Sep 20 is index 2
    assert rep.unpriced_models == ["claude-unknown-9"]
    assert rep.share(rep.providers[0].cost) == pytest.approx(1.0)
    assert rep.sessions == 1
    old = build(1, today=date(2026, 9, 24), prices=PRICES)
    assert old.cost == 0 and old.tokens.total == 0                    # outside the range


def test_pricing_overrides_normalization_and_missing():
    pb = PriceBook(overrides={"gpt-6-astra": {"input": 1, "output": 2}}, litellm={
        "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6},
        "anthropic/claude-haiku-4-5": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6},
        "gpt-6-astra": {"input_cost_per_token": 10e-6, "output_cost_per_token": 50e-6}})
    assert pb.get("gpt-6-astra").input == pytest.approx(1e-6)          # override wins
    assert pb.get("gpt-6-astra").cache_read == pytest.approx(1e-6)     # omitted cache rate = input rate
    assert pb.get("claude-opus-5[1m]").output == pytest.approx(25e-6)  # [1m] suffix normalized
    assert pb.get("claude-haiku-4-5-20251001").input == pytest.approx(1e-6)  # dated id + provider prefix
    assert pb.get("mystery-model") is None
    assert normalize("claude-opus-5[1m]") == "claude-opus-5"


@pytest.mark.parametrize("n,text", [(18_200_000_000, "18.2B"), (660_000_000, "660M"), (56_800_000, "56.8M"),
                                    (1500, "1.5K"), (42, "42")])
def test_token_formatting(n, text):
    assert fmt_tokens(n) == text


def test_gemini_cli_chat_records(homes, monkeypatch):
    monkeypatch.setenv("GEMINI_CLI_HOME", str(homes / "gem"))
    chats = homes / "gem" / "tmp" / "proj" / "chats"
    chats.mkdir(parents=True)
    reply = {"id": "g1", "timestamp": TS, "type": "gemini", "model": "gemini-3.8-flash", "content": "hi"}
    tokens = {"input": 1200, "output": 300, "cached": 1000, "thoughts": 50, "tool": 0, "total": 1550}
    lines = [{"sessionId": "gs1", "projectHash": "x", "startTime": TS},
             {"id": "u1", "timestamp": TS, "type": "user", "content": "q"},
             reply,                                     # appended first without tokens...
             dict(reply, tokens=tokens)]                # ...then again once the tokens arrive
    (chats / "session-1.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    (chats / "old.json").write_text(json.dumps({"sessionId": "gs0", "messages": [
        dict(reply, id="g0", tokens={"input": 10, "output": 5, "cached": 0})]}), encoding="utf-8")
    ix.update()
    rows = ix.connect().execute("SELECT session, uncached, cache_read, output, reasoning FROM events "
                                "WHERE provider='gemini' ORDER BY session").fetchall()
    assert rows == [("gs0", 10, 0, 5, 0), ("gs1", 200, 1000, 350, 50)]   # thoughts billed as output


def test_premium_pricing_long_context_1h_cache_and_fast():
    pb = PriceBook(overrides={"_fast_multiplier": 2.0}, litellm={
        "gpt-6-astra": {"input_cost_per_token": 10e-6, "output_cost_per_token": 50e-6,
                        "cache_read_input_token_cost": 1e-6,
                        "input_cost_per_token_above_272k_tokens": 20e-6, "output_cost_per_token_above_272k_tokens": 75e-6,
                        "cache_read_input_token_cost_above_272k_tokens": 2e-6},
        "claude-opus-5": {"input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6,
                          "cache_read_input_token_cost": 0.5e-6, "cache_creation_input_token_cost": 6.25e-6,
                          "cache_creation_input_token_cost_above_1hr": 10e-6}})
    gpt = pb.get("gpt-6-astra")
    assert gpt.long_threshold == 272_000 and gpt.long.input == pytest.approx(20e-6)
    assert gpt.cost(1000, 0, 0, 100, long=True) == pytest.approx(1000 * 20e-6 + 100 * 75e-6)
    assert gpt.cost(1000, 0, 0, 100) == pytest.approx(1000 * 10e-6 + 100 * 50e-6)
    opus = pb.get("claude-opus-5")
    assert opus.cost(0, 0, 1000, 0, cw_1h=600) == pytest.approx(400 * 6.25e-6 + 600 * 10e-6)
    assert opus.cost(0, 0, 0, 100, fast=True) == pytest.approx(2 * 100 * 25e-6)


def test_index_records_request_size_and_1h_writes_and_report_applies_them(homes):
    usage = {"input_tokens": 10, "cache_read_input_tokens": 300_000, "cache_creation_input_tokens": 1000,
             "cache_creation": {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0}, "output_tokens": 5}
    line = json.dumps({"type": "assistant", "sessionId": "s", "requestId": "r", "timestamp": TS,
                       "message": {"id": "m", "model": "claude-opus-5", "usage": usage}}) + "\n"
    write(homes / "claude/projects/p1/a.jsonl", line)
    ix.update()
    assert ix.connect().execute("SELECT ctx, cw_1h, speed FROM events").fetchone() == (301_010, 1000, "standard")
    pb = PriceBook(overrides={}, litellm={"claude-opus-5": {
        "input_cost_per_token": 5e-6, "output_cost_per_token": 25e-6, "cache_read_input_token_cost": 0.5e-6,
        "cache_creation_input_token_cost": 6.25e-6, "cache_creation_input_token_cost_above_1hr": 10e-6,
        "input_cost_per_token_above_200k_tokens": 10e-6, "cache_read_input_token_cost_above_200k_tokens": 1e-6,
        "cache_creation_input_token_cost_above_200k_tokens": 12.5e-6}})
    rep = build(30, today=date(2026, 9, 24), prices=pb)
    # long-context rates (ctx > 200k); the 1-hour write rate comes from the base price as the long tier has none
    assert rep.cost == pytest.approx(10 * 10e-6 + 300_000 * 1e-6 + 1000 * 10e-6 + 5 * 25e-6)
