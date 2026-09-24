"""Per-token prices for the usage report ("API estimate").

Sources, in order:
1. %APPDATA%\\quota-tray\\pricing.json: your overrides, in USD per million tokens:
       {"gpt-6-astra": {"input": 10, "output": 50, "cache_read": 1, "cache_write": 12.5}}
2. LiteLLM's public price list (the same source T3 Code uses), cached locally in
   litellm_prices.json and re-downloaded at most once a week. Only a read-only GET of a public file.
3. Nothing: the model's tokens are still counted, but it has no cost (shown as "no price").

Grok sessions carry their own cost from the Grok CLI, so they don't need a price at all.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..config import app_dir

log = logging.getLogger(__name__)
LITELLM_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MAX_AGE_SECONDS = 7 * 86400
PREFIXES = ("", "anthropic/", "openai/", "xai/", "gemini/")


@dataclass(frozen=True)
class Price:
    """USD per token. Optional premiums: long-context rates above `long_threshold` tokens per request,
    a separate rate for 1-hour cache writes, and a fast-mode multiplier."""
    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float | None = None
    long_threshold: int | None = None
    long: "Price | None" = None
    fast_multiplier: float | None = None

    def cost(self, uncached: int, cache_read: int, cache_write: int, output: int, *, cw_1h: int = 0,
             long: bool = False, fast: bool = False) -> float:
        p = self.long if (long and self.long is not None) else self
        cw_1h = min(cw_1h, cache_write)
        rate_1h = p.cache_write_1h if p.cache_write_1h is not None else (
            self.cache_write_1h if self.cache_write_1h is not None else p.cache_write)
        c = (uncached * p.input + cache_read * p.cache_read + (cache_write - cw_1h) * p.cache_write
             + cw_1h * rate_1h + output * p.output)
        return c * (self.fast_multiplier or 1.0) if fast else c

    def savings(self, cache_read: int) -> float:
        """What cache reads saved versus paying the full input price."""
        return cache_read * max(0.0, self.input - self.cache_read)


def litellm_path() -> Path:
    return app_dir() / "litellm_prices.json"


def overrides_path() -> Path:
    return app_dir() / "pricing.json"


def refresh_litellm(force: bool = False, timeout: float = 30) -> bool:
    """Download the public price list if the cache is missing or older than a week."""
    path = litellm_path()
    if not force and path.exists() and time.time() - path.stat().st_mtime < MAX_AGE_SECONDS:
        return False
    try:
        raw = urllib.request.urlopen(urllib.request.Request(LITELLM_URL, headers={"User-Agent": "quota-tray/1.0"}),
                                     timeout=timeout).read()
        data = json.loads(raw)
        if not isinstance(data, dict) or len(data) < 100:
            raise ValueError("unexpected price list shape")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        log.info("price list refreshed: %d models", len(data))
        return True
    except Exception as exc:  # offline is fine: keep using the cached copy
        log.info("price list refresh failed (using cache if any): %s", exc)
        return False


def normalize(model: str) -> str:
    """claude-opus-5[1m] -> claude-opus-5"""
    return re.sub(r"\[.*?\]$", "", model.strip())


class PriceBook:
    def __init__(self, overrides: dict | None = None, litellm: dict | None = None):
        self._overrides = overrides if overrides is not None else _read_json(overrides_path())
        self._litellm = litellm if litellm is not None else _read_json(litellm_path())
        self._cache: dict[str, Price | None] = {}

    @property
    def has_list(self) -> bool:
        return bool(self._litellm)

    def get(self, model: str) -> Price | None:
        if model not in self._cache:
            self._cache[model] = self._lookup(model)
        return self._cache[model]

    def _lookup(self, model: str) -> Price | None:
        name = normalize(model)
        o = self._overrides.get(name) or self._overrides.get(model)
        if isinstance(o, dict) and _num(o.get("input")) is not None and _num(o.get("output")) is not None:
            return _override_price(o)
        for candidate in (name, re.sub(r"-\d{8}$", "", name)):
            for prefix in PREFIXES:
                e = self._litellm.get(prefix + candidate)
                if isinstance(e, dict):
                    i, out = _num(e.get("input_cost_per_token")), _num(e.get("output_cost_per_token"))
                    if i is None or out is None:
                        continue
                    return _litellm_price(e, i, out, self._overrides.get("_fast_multiplier"))
        return None


def _litellm_price(e: dict, i: float, out: float, fast) -> Price:
    cr = _num(e.get("cache_read_input_token_cost"))
    cw = _num(e.get("cache_creation_input_token_cost"))
    long, threshold = None, None
    for key in e:
        m = re.fullmatch(r"input_cost_per_token_above_(\d+)k_tokens", key)
        if m:
            threshold = int(m.group(1)) * 1000
            sfx = f"_above_{m.group(1)}k_tokens"
            li = _num(e.get(key))
            lo = _num(e.get("output_cost_per_token" + sfx))
            lcr = _num(e.get("cache_read_input_token_cost" + sfx))
            lcw = _num(e.get("cache_creation_input_token_cost" + sfx))
            long = Price(li, lo if lo is not None else out, lcr if lcr is not None else (cr if cr is not None else li),
                         lcw if lcw is not None else (cw if cw is not None else li))
            break
    return Price(i, out, cr if cr is not None else i, cw if cw is not None else i,
                 cache_write_1h=_num(e.get("cache_creation_input_token_cost_above_1hr")),
                 long_threshold=threshold, long=long, fast_multiplier=_num(fast))


def _override_price(o: dict) -> Price:
    """pricing.json entry, USD per million tokens. Optional: cache_write_1h, long_threshold + long {...},
    fast_multiplier."""
    def per_tok(v):
        return v / 1e6 if v is not None else None
    i, out = per_tok(_num(o["input"])), per_tok(_num(o["output"]))
    cr = per_tok(_num(o.get("cache_read")))
    cw = per_tok(_num(o.get("cache_write")))
    long = None
    lo = o.get("long")
    if isinstance(lo, dict) and _num(lo.get("input")) is not None:
        long = _override_price({"output": o["output"], **lo})
    thr = _num(o.get("long_threshold"))
    return Price(i, out, cr if cr is not None else i, cw if cw is not None else i,
                 cache_write_1h=per_tok(_num(o.get("cache_write_1h"))),
                 long_threshold=int(thr) if thr else None, long=long,
                 fast_multiplier=_num(o.get("fast_multiplier")))


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
