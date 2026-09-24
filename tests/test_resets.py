"""Banked limit resets (Claude cedar_ember / juniper_tide, Codex reset credits) and their display."""
import os
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from quota_core.display import reset_badge, reset_lines  # noqa: E402
from quota_core.engine import ProviderState  # noqa: E402
from quota_core.models import Reading, utcnow  # noqa: E402
from quota_core.providers.claude import parse_resets  # noqa: E402
from quota_core.providers.codex import parse_reset_credits  # noqa: E402
from quota_ui import cats  # noqa: E402

CLAUDE_RESETS = {  # shape of the real ?at_wall=1 response (Claude Code client)
    "juniper_tide": {"eligible": False, "ineligible_reason": "not_at_wall", "available": False,
                     "next_available_at": None, "resets_per_week": 1},
    "cedar_ember": {"eligible": True, "grants": [
        {"id": "g1", "label": "Launch: one usage-limit reset", "resets_total": 1, "resets_left": 1,
         "ends_at": "2026-10-22T16:00:00+00:00", "clears": ["five_hour", "seven_day"], "paused": False,
         "usable_now": True},
        {"id": "g0", "label": "Used up", "resets_left": 0, "usable_now": True}]}}

CODEX_CREDITS = {"credits": [
    {"status": "available", "title": "Full reset", "expires_at": "2026-10-22T20:44:49Z"},
    {"status": "redeemed", "title": "Full reset", "expires_at": "2026-10-01T00:00:00Z"}], "available_count": 1}


def test_claude_resets_from_grants_and_session_reset():
    out = parse_resets(CLAUDE_RESETS)
    assert out == [{"label": "Launch: one usage-limit reset", "left": 1, "expires_at": "2026-10-22T16:00:00+00:00",
                    "usable_now": True, "clears": ["5-hour", "weekly"], "kind": "banked"}]
    at_wall = dict(CLAUDE_RESETS, juniper_tide={"eligible": True, "available": True,
                                                 "weekly_resets_at": "2026-09-28T15:00:00+00:00"})
    kinds = [r["kind"] for r in parse_resets(at_wall)]
    assert kinds == ["banked", "session"]
    assert parse_resets({}) == []                          # "ineligible: surface" responses carry nothing


def test_codex_reset_credits_and_applicability():
    usage = {"rate_limit_reset_credits": {"available_count": 1, "applicable_available_count": 0}}
    out = parse_reset_credits(CODEX_CREDITS, usage)
    assert out == [{"label": "Full reset", "left": 1, "expires_at": "2026-10-22T20:44:49Z", "usable_now": False,
                    "clears": ["weekly"], "kind": "banked"}]
    usage["rate_limit_reset_credits"]["applicable_available_count"] = 1
    assert parse_reset_credits(CODEX_CREDITS, usage)[0]["usable_now"] is True


def reading(resets):
    return Reading(name="Claude", weekly_used_pct=95, weekly_resets_at=utcnow() + timedelta(days=2), resets=resets)


def test_reset_display_text_and_badge():
    r = reading(parse_resets(CLAUDE_RESETS))
    assert r.resets_left == 1 and r.reset_usable_now
    assert reset_badge(r) == "↻1"
    line = reset_lines(r)[0]
    assert "1 reset available" in line and "usable now" in line and "clears 5-hour, weekly" in line and "use by" in line
    assert reset_badge(reading(None)) is None and reset_lines(reading([])) == []


def test_resets_survive_state_round_trip():
    r = reading(parse_resets(CLAUDE_RESETS))
    back = Reading.from_json(r.to_json())
    assert back.resets == r.resets and back.resets_left == 1


def test_cat_shows_a_glowing_token_only_when_resets_exist():
    st = ProviderState("claude", "Claude", "Claude Code", reading=reading(parse_resets(CLAUDE_RESETS)))
    assert cats.reset_for(st) == "ready"
    st_banked = ProviderState("codex", "Codex", "Codex CLI",
                              reading=reading(parse_reset_credits(CODEX_CREDITS, {})))
    assert cats.reset_for(st_banked) == "banked"
    assert cats.reset_for(ProviderState("grok", "Grok", "Grok CLI", reading=reading(None))) is None
    plain = cats.cat_svg("claude", cats.ALERT)
    ready = cats.cat_svg("claude", cats.ALERT, cats.anim_at(1.0), reset="ready")
    banked = cats.cat_svg("claude", cats.ALERT, reset="banked")
    assert "#E8B84A" in ready and "#F6D774" in ready          # gold coin with glow
    assert "#D9CFB8" in banked and "#F6D774" not in banked    # dimmed, no glow
    assert "#E8B84A" not in plain
    assert cats.cat_svg("claude", cats.DEAD, reset="ready") == cats.cat_svg("claude", cats.DEAD)  # no token on an empty cushion
    for svg in (ready, banked):
        assert QSvgRenderer(QByteArray(svg.encode())).isValid()


def test_card_tooltip_and_cat_tooltip_list_resets():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from quota_ui.cards import build_card, full_details
    r = reading(parse_resets(CLAUDE_RESETS))
    st = ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r)
    assert "1 reset available" in build_card(st).toolTip()
    assert "1 reset available" in full_details(st)
