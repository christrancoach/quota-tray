"""Turn the usage index into the numbers the report shows. Costs are an API-price estimate."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .index import connect
from .pricing import PriceBook

PROVIDERS = {"claude": "Claude Code", "codex": "Codex", "grok": "Grok Build", "gemini": "Gemini CLI"}
ORDER = ("claude", "codex", "grok", "gemini")   # fixed order = fixed color slots in the chart


@dataclass
class Tokens:
    uncached: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0

    @property
    def total(self) -> int:
        return self.uncached + self.cache_read + self.cache_write + self.output

    def add(self, o: "Tokens"):
        self.uncached += o.uncached
        self.cache_read += o.cache_read
        self.cache_write += o.cache_write
        self.output += o.output


@dataclass
class Line:
    key: str                 # provider key, model name or day
    provider: str = ""
    cost: float = 0.0
    tokens: Tokens = field(default_factory=Tokens)
    sessions: int = 0
    priced: bool = True      # False when some tokens had no price


@dataclass
class Report:
    start: date
    end: date
    cost: float = 0.0
    sessions: int = 0
    tokens: Tokens = field(default_factory=Tokens)
    cache_savings: float = 0.0
    providers: list[Line] = field(default_factory=list)
    models: list[Line] = field(default_factory=list)
    days: list[Line] = field(default_factory=list)                 # every day in range, oldest first
    daily: dict[str, list[float]] = field(default_factory=dict)    # provider -> cost per day (aligned to days)
    unpriced_models: list[str] = field(default_factory=list)

    def share(self, cost: float) -> float:
        return cost / self.cost if self.cost else 0.0


def build(days: int | None = 30, today: date | None = None, prices: PriceBook | None = None,
          db: Path | None = None) -> Report:
    """days=None means all time."""
    today = today or date.today()
    prices = prices or PriceBook()
    con = connect(db)
    if days is None:
        first = con.execute("SELECT min(day) FROM events").fetchone()[0]
        start = date.fromisoformat(first) if first else today
    else:
        start = today - timedelta(days=days - 1)
    # Group requests into context-size bands so long-context rates apply only above each model's threshold.
    rows = con.execute(
        """SELECT provider, model, day, sum(uncached), sum(cache_read), sum(cache_write), sum(output),
                  sum(native_cost), count(native_cost), sum(cw_1h), speed,
                  CASE WHEN ctx > 272000 THEN 272001 WHEN ctx > 200000 THEN 200001
                       WHEN ctx > 128000 THEN 128001 ELSE 0 END AS band
           FROM events WHERE day BETWEEN ? AND ? GROUP BY provider, model, day, speed, band""",
        (start.isoformat(), today.isoformat())).fetchall()
    sess_prov = dict(con.execute(
        "SELECT provider, count(DISTINCT session) FROM events WHERE day BETWEEN ? AND ? GROUP BY provider",
        (start.isoformat(), today.isoformat())).fetchall())
    sess_model = {(p, m): n for p, m, n in con.execute(
        "SELECT provider, model, count(DISTINCT session) FROM events WHERE day BETWEEN ? AND ? GROUP BY provider, model",
        (start.isoformat(), today.isoformat()))}
    sess_day = dict(con.execute(
        "SELECT day, count(DISTINCT provider || session) FROM events WHERE day BETWEEN ? AND ? GROUP BY day",
        (start.isoformat(), today.isoformat())).fetchall())
    con.close()

    rep = Report(start=start, end=today)
    day_list = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]
    day_idx = {d: i for i, d in enumerate(day_list)}
    rep.daily = {p: [0.0] * len(day_list) for p in ORDER}
    by_prov: dict[str, Line] = {p: Line(p, p) for p in ORDER}
    by_model: dict[tuple, Line] = {}
    by_day: dict[str, Line] = {d: Line(d) for d in day_list}
    unpriced = set()

    for provider, model, day, unc, cr, cw, out, native, n_native, cw1h, speed, band in rows:
        tok = Tokens(unc or 0, cr or 0, cw or 0, out or 0)
        price = prices.get(model)
        if n_native:                       # the CLI reported its own cost (Grok)
            cost = native or 0.0
        elif price:
            long = bool(price.long_threshold and band and band > price.long_threshold)
            cost = price.cost(tok.uncached, tok.cache_read, tok.cache_write, tok.output, cw_1h=cw1h or 0,
                              long=long, fast=(speed == "fast"))
        else:
            cost = 0.0
            unpriced.add(model)
        if price:
            rep.cache_savings += price.savings(tok.cache_read)
        pl = by_prov.setdefault(provider, Line(provider, provider))
        ml = by_model.setdefault((provider, model), Line(model, provider))
        dl = by_day[day]
        for line in (pl, ml, dl):
            line.cost += cost
            line.tokens.add(tok)
        if not n_native and not price:
            ml.priced = False
        rep.tokens.add(tok)
        rep.cost += cost
        if provider in rep.daily and day in day_idx:
            rep.daily[provider][day_idx[day]] += cost

    for p, line in by_prov.items():
        line.sessions = sess_prov.get(p, 0)
    for (p, m), line in by_model.items():
        line.sessions = sess_model.get((p, m), 0)
    for d, line in by_day.items():
        line.sessions = sess_day.get(d, 0)
    rep.sessions = sum(sess_prov.values())
    rep.providers = [by_prov[p] for p in ORDER if by_prov[p].tokens.total] + \
                    [v for k, v in by_prov.items() if k not in ORDER and v.tokens.total]
    rep.models = sorted(by_model.values(), key=lambda l: (-l.cost, -l.tokens.total))
    rep.days = [by_day[d] for d in day_list]
    rep.unpriced_models = sorted(unpriced)
    return rep


def fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def fmt_tokens(n: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            s = f"{n / div:.1f}".rstrip("0").rstrip(".")
            return f"{s}{unit}"
    return f"{int(n)}"
