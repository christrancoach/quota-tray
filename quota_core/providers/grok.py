"""Grok (Grok CLI OIDC credentials -> cli-chat-proxy.grok.com/v1/billing?format=credits).

The weekly percentage logic is ported from quse (MIT): prefer the GrokBuild entry in
productUsage, then creditUsagePercent, then onDemandUsed / onDemandCap.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ..http import raw_json, request_json
from ..models import AuthStale, ProviderError, Reading, ShapeError, utcnow
from .base import Provider, clamp_pct, missing_fields, num, parse_time

log = logging.getLogger(__name__)
BASE_URL = "https://cli-chat-proxy.grok.com/v1"
WEEKLY_PERIOD = "USAGE_PERIOD_TYPE_WEEKLY"
PRODUCT_LABELS = {"GrokBuild": "Build", "GrokChat": "Chat", "GrokImagine": "Imagine", "GrokVoice": "Voice", "GrokApi": "API"}


def default_auth_path() -> Path:
    home = os.environ.get("GROK_HOME")
    return (Path(home) if home else Path.home() / ".grok") / "auth.json"


def select_entry(data: dict) -> dict:
    """auth.json maps '<issuer>::<client_id>' -> entry. Use the entry that expires last."""
    entries = [v for v in data.values() if isinstance(v, dict) and isinstance(v.get("key"), str) and v["key"]]
    if not entries:
        raise AuthStale("No Grok login in auth.json")
    far_past = parse_time(0)
    return max(entries, key=lambda e: parse_time(e.get("expires_at")) or far_past)


SHAPE = {"config.currentPeriod.end": "weekly reset time", "config.currentPeriod.type": "period type",
         "config.productUsage": "per-product breakdown"}


def shape_warnings(data: dict) -> list[str]:
    return missing_fields(data, SHAPE)


def parse_credits(data: dict, name: str = "Grok") -> Reading:
    config = data.get("config") if isinstance(data.get("config"), dict) else data
    products: dict[str, float] = {}
    for item in config.get("productUsage") or []:
        if isinstance(item, dict) and isinstance(item.get("product"), str) and num(item.get("usagePercent")) is not None:
            products[item["product"]] = clamp_pct(num(item["usagePercent"]))

    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    is_weekly = period.get("type") == WEEKLY_PERIOD

    used = products.get("GrokBuild")
    if used is None:
        used = clamp_pct(num(config.get("creditUsagePercent")))
    if used is None:
        cap, spent = num(config.get("onDemandCap")), num(config.get("onDemandUsed"))
        if cap and spent is not None:
            used = clamp_pct(spent / cap * 100)
    if used is None and is_weekly:
        used = 0.0  # proto3 JSON omits a 0.0 percent
    if used is None:
        raise ShapeError("No weekly usage in Grok credits response", raw_json(data))

    return Reading(
        name=name,
        weekly_used_pct=used,
        weekly_resets_at=parse_time(period.get("end") or config.get("billingPeriodEnd")),
        breakdown={PRODUCT_LABELS.get(k, k): v for k, v in products.items()} or None,
        last_updated=utcnow(),
    )


class GrokProvider(Provider):
    key = "grok"
    default_name = "Grok"
    cli_name = "Grok CLI"

    def fetch(self) -> Reading:
        path = Path(self.cfg["auth_path"]) if self.cfg.get("auth_path") else default_auth_path()
        entry = select_entry(self.read_json_file(path))
        expires = parse_time(entry.get("expires_at"))
        if expires and expires <= utcnow():
            raise AuthStale(f"Grok CLI token expired. {self.stale_message}")
        headers = {"Authorization": f"Bearer {entry['key']}", "Accept": "application/json",
                   "x-grok-client-mode": "cli", "User-Agent": "quota-tray/1.0"}
        stale = f"Grok rejected the token. {self.stale_message}"
        credits = request_json(f"{BASE_URL}/billing?format=credits", headers, stale_message=stale)
        reading = parse_credits(credits, self.name)
        reading.warnings = shape_warnings(credits) or None
        try:
            user = request_json(f"{BASE_URL}/user?include=subscription", headers, stale_message=stale)
            tier = user.get("subscriptionTier")
            reading.plan = {"XPremiumPlus": "Premium+", "SuperGrok": "SuperGrok"}.get(tier, tier) if isinstance(tier, str) else None
        except ProviderError as exc:
            log.info("grok plan lookup failed (non-blocking): %s", exc)
        del headers
        return reading
