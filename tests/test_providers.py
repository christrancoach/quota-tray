import json
from datetime import timedelta
from pathlib import Path

import pytest

from quota_core.models import SOURCE_LOG, AuthStale, ShapeError, classify_window, utcnow
from quota_core.providers import claude, codex, grok

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def test_classify_window():
    assert classify_window(18000) == "short"
    assert classify_window(604800) == "weekly"
    assert classify_window(30 * 86400) is None
    assert classify_window(None) is None


def test_claude_parse_real_response():
    r = claude.parse_usage(load("claude_usage"))
    assert r.weekly_used_pct == 36.0 and r.short_used_pct == 9.0
    assert r.weekly_resets_at.isoformat().startswith("2026-09-28T15:00")
    assert r.breakdown == {"Fable weekly": 20.0}
    assert r.detail["Claude Code"] == 96 and r.detail["Chats"] == 3


def test_claude_falls_back_to_limits_array():
    data = {"limits": [{"kind": "weekly_all", "percent": 40, "resets_at": "2026-09-28T15:00:00Z"},
                       {"kind": "session", "percent": 5, "resets_at": None}]}
    r = claude.parse_usage(data)
    assert r.weekly_used_pct == 40 and r.short_used_pct == 5


def test_claude_shape_error():
    with pytest.raises(ShapeError):
        claude.parse_usage({"unexpected": True})


def test_codex_parse_weekly_only():
    r = codex.parse_usage(load("codex_usage"))
    assert r.weekly_used_pct == 95 and r.short_used_pct is None
    assert r.plan == "Pro Lite"


def test_codex_classifies_by_length_not_slot():
    data = {"rate_limit": {
        "primary_window": {"used_percent": 70, "limit_window_seconds": 604800, "reset_at": 1790471273},
        "secondary_window": {"used_percent": 12, "limit_window_seconds": 18000, "reset_at": 1790200000}}}
    r = codex.parse_usage(data)
    assert r.weekly_used_pct == 70 and r.short_used_pct == 12


def test_codex_account_id_from_id_token():
    import base64
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token, account = codex.read_auth({"tokens": {"accessToken": "a.b.c", "id_token": f"x.{body}.y"}})
    assert token == "a.b.c" and account == "acct-1"


def test_codex_log_fallback(tmp_path):
    day = tmp_path / "2026" / "09" / "23"
    day.mkdir(parents=True)
    now = utcnow()
    event = {"timestamp": now.isoformat().replace("+00:00", "Z"), "type": "event_msg",
             "payload": {"type": "token_count", "rate_limits": {
                 "primary": {"used_percent": 95.0, "window_minutes": 10080, "resets_at": 1790471273},
                 "secondary": None, "plan_type": "prolite"}}}
    (day / "rollout-x.jsonl").write_text('{"type":"other"}\n' + json.dumps(event) + "\n", encoding="utf-8")
    r = codex.read_latest_log(tmp_path, "ChatGPT (Codex)")
    assert r.source == SOURCE_LOG and r.weekly_used_pct == 95 and r.short_used_pct is None


def test_grok_prefers_grokbuild_then_credit_percent():
    r = grok.parse_credits(load("grok_credits"))
    assert r.weekly_used_pct == 100 and r.breakdown == {"Build": 100.0}
    assert r.weekly_resets_at.isoformat().startswith("2026-09-26T09:45")
    r2 = grok.parse_credits({"config": {"creditUsagePercent": 42.5, "currentPeriod": {"type": grok.WEEKLY_PERIOD}}})
    assert r2.weekly_used_pct == 42.5 and r2.breakdown is None
    r3 = grok.parse_credits({"config": {"currentPeriod": {"type": grok.WEEKLY_PERIOD}}})
    assert r3.weekly_used_pct == 0.0  # proto3 omitted default


def test_grok_selects_latest_entry():
    data = {"a": {"key": "old", "expires_at": "2026-01-01T00:00:00Z"},
            "b": {"key": "new", "expires_at": "2026-09-23T18:16:59.856814100Z"}}
    assert grok.select_entry(data)["key"] == "new"


def test_claude_expired_token_is_stale_without_network(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    past = int((utcnow() - timedelta(hours=1)).timestamp() * 1000)
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-FAKE-test-token", "expiresAt": past}}))
    monkeypatch.setattr(claude, "request_json", lambda *a, **k: pytest.fail("must not call network"))
    before = creds.read_bytes()
    with pytest.raises(AuthStale):
        claude.ClaudeProvider({"credentials_path": str(creds)}).fetch()
    assert creds.read_bytes() == before  # never written


@pytest.mark.parametrize("opt_in", [None, True])
def test_claude_reset_lookup_is_opt_in_and_only_it_uses_claude_codes_agent(tmp_path, monkeypatch, opt_in):
    creds = tmp_path / ".credentials.json"
    future = int((utcnow() + timedelta(hours=1)).timestamp() * 1000)
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-FAKE-test-token", "expiresAt": future}}))
    calls = []

    def fake(url, headers, **k):
        calls.append((url, headers["User-Agent"]))
        return load("claude_usage") if url == claude.USAGE_URL else {}
    monkeypatch.setattr(claude, "request_json", fake)
    cfg = {"credentials_path": str(creds)}
    if opt_in:
        cfg["reset_info"] = True
    claude.ClaudeProvider(cfg).fetch()
    assert calls[0] == (claude.USAGE_URL, "quota-tray/1.0")
    assert calls[1:] == ([(claude.RESETS_URL, claude.CLAUDE_CODE_UA)] if opt_in else [])


def test_grok_expired_token_is_stale(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"x::y": {"key": "k", "expires_at": "2020-01-01T00:00:00Z"}}))
    monkeypatch.setattr(grok, "request_json", lambda *a, **k: pytest.fail("must not call network"))
    with pytest.raises(AuthStale):
        grok.GrokProvider({"auth_path": str(auth)}).fetch()


def test_missing_credentials_is_stale(tmp_path):
    with pytest.raises(AuthStale):
        claude.ClaudeProvider({"credentials_path": str(tmp_path / "nope.json")}).fetch()


