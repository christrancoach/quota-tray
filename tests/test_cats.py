"""Cat art: state mapping and SVG validity for every cat in every state."""
import os
from datetime import timedelta

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from quota_core.engine import ProviderState  # noqa: E402
from quota_core.models import STATUS_ERROR, STATUS_OK, STATUS_STALE, Reading, utcnow  # noqa: E402
from quota_ui import cats  # noqa: E402

ALL_STATES = [*cats.ENERGY_STATES, cats.DEEP, cats.STALE, cats.ERROR, cats.RESET, cats.DEAD]


@pytest.mark.parametrize("left,state", [
    (100, "awake"), (75, "awake"), (74.9, "alert"), (50, "alert"), (49.9, "drowsy"), (25, "drowsy"),
    (24.9, "nodding"), (10, "nodding"), (9.9, "asleep"), (0.1, "asleep"), (0, "deep"),
])
def test_energy_thresholds(left, state):
    assert cats.energy_state(left) == state


def st(used=40.0, status=STATUS_OK, error=None, reset_in=timedelta(days=2), reading=True):
    r = (Reading(name="Claude", weekly_used_pct=used, weekly_resets_at=utcnow() + reset_in, status=status, error=error)
         if reading else None)
    return ProviderState("claude", "Claude", "Claude Code", reading=r, last_good=r)


def test_special_states_override_energy():
    assert cats.state_for(st(status=STATUS_STALE, error="Open Claude Code to refresh")) == cats.STALE
    assert cats.state_for(st(status=STATUS_ERROR, error="Network error")) == cats.ERROR
    assert cats.state_for(st(status=STATUS_STALE, reset_in=timedelta(hours=-1))) == cats.RESET
    assert cats.state_for(st(status=STATUS_STALE, error="Claude Code login ended. Open Claude Code to log in")) == cats.DEAD
    assert cats.state_for(st(reading=False)) == cats.STALE          # waiting for first refresh
    assert cats.state_for(st(used=40)) == cats.ALERT               # fresh: energy from % left


FRAMES = [cats.anim_at(t / 7, seed=s) for t in range(0, 70, 3) for s in (0, 2)]


@pytest.mark.parametrize("key", list(cats.CATS))
@pytest.mark.parametrize("state", ALL_STATES)
def test_every_cat_in_every_state_is_valid_svg_through_the_animation(key, state):
    for anim in [cats.Anim(), *FRAMES]:
        svg = cats.cat_svg(key, state, anim)
        assert QSvgRenderer(QByteArray(svg.encode())).isValid(), (key, state, anim)


@pytest.mark.parametrize("key", list(cats.CATS))
@pytest.mark.parametrize("state", [*cats.ENERGY_STATES, cats.DEEP, cats.STALE, cats.ERROR, cats.RESET])
def test_every_living_state_actually_moves(key, state):
    frames = {cats.cat_svg(key, state, cats.anim_at(t / 5)) for t in range(0, 50)}
    assert len(frames) > 10, f"{key}/{state} barely changes over 10 s"


@pytest.mark.parametrize("key", list(cats.CATS))
def test_still_frame_is_stable_for_reduce_motion(key):
    for state in ALL_STATES:
        assert cats.cat_svg(key, state, cats.Anim()) == cats.cat_svg(key, state, cats.Anim(t=123.4, seed=3))
