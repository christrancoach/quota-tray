"""The provider plugin contract and window classification."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

STATUS_OK = "ok"
STATUS_STALE = "stale"   # credentials expired or rejected; user must open the CLI
STATUS_ERROR = "error"   # network / server / parse failure

SOURCE_CLI = "cli"
SOURCE_COOKIE = "cookie"
SOURCE_LOG = "log_fallback"

SHORT_MAX_SECONDS = 24 * 3600
WEEKLY_MIN_SECONDS = 6 * 24 * 3600
WEEKLY_MAX_SECONDS = 8 * 24 * 3600


def classify_window(seconds: float | None) -> str | None:
    """Classify a quota window strictly by its length: 'short', 'weekly', or None."""
    if not seconds or seconds <= 0:
        return None
    if seconds <= SHORT_MAX_SECONDS:
        return "short"
    if WEEKLY_MIN_SECONDS <= seconds <= WEEKLY_MAX_SECONDS:
        return "weekly"
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Reading:
    """What every provider returns. Percentages are *used* percent, 0-100."""

    name: str
    weekly_used_pct: float | None
    weekly_resets_at: datetime | None
    short_used_pct: float | None = None
    short_resets_at: datetime | None = None
    # Secondary bars shown on the card: label -> used %.
    breakdown: dict[str, float] | None = None
    status: str = STATUS_OK
    source: str = SOURCE_CLI
    last_updated: datetime = field(default_factory=utcnow)
    error: str | None = None
    # Extra detail shown on hover (e.g. Claude product split): label -> percent.
    detail: dict[str, float] | None = None
    plan: str | None = None
    # Banked one-time limit resets: [{"label", "left", "expires_at" (ISO), "usable_now", "clears"}]
    resets: list[dict] | None = None
    # Early warnings that the provider's response shape changed (optional fields went missing).
    warnings: list[str] | None = None

    @property
    def resets_left(self) -> int:
        return sum(int(r.get("left") or 0) for r in self.resets or [])

    @property
    def reset_usable_now(self) -> bool:
        return any(r.get("usable_now") and (r.get("left") or 0) > 0 for r in self.resets or [])

    @property
    def weekly_left_pct(self) -> float | None:
        if self.weekly_used_pct is None:
            return None
        return max(0.0, min(100.0, 100.0 - self.weekly_used_pct))

    @property
    def short_left_pct(self) -> float | None:
        if self.short_used_pct is None:
            return None
        return max(0.0, min(100.0, 100.0 - self.short_used_pct))

    def reset_passed(self, now: datetime | None = None) -> bool:
        """True when the stored weekly reset time is already in the past."""
        if self.weekly_resets_at is None:
            return False
        return self.weekly_resets_at <= (now or utcnow())

    def degraded(self, status: str, error: str) -> "Reading":
        """Copy of this (last good) reading marked stale/error. last_updated keeps the success time."""
        return replace(self, status=status, error=error)

    def to_json(self) -> dict:
        def iso(d: datetime | None) -> str | None:
            return d.isoformat() if d else None

        return {
            "name": self.name,
            "weekly_used_pct": self.weekly_used_pct,
            "weekly_resets_at": iso(self.weekly_resets_at),
            "short_used_pct": self.short_used_pct,
            "short_resets_at": iso(self.short_resets_at),
            "breakdown": self.breakdown,
            "status": self.status,
            "source": self.source,
            "last_updated": iso(self.last_updated),
            "error": self.error,
            "detail": self.detail,
            "plan": self.plan,
            "resets": self.resets,
            "warnings": self.warnings,
        }

    @classmethod
    def from_json(cls, d: dict) -> "Reading":
        def dt(v):
            return datetime.fromisoformat(v) if v else None

        return cls(
            name=d["name"],
            weekly_used_pct=d.get("weekly_used_pct"),
            weekly_resets_at=dt(d.get("weekly_resets_at")),
            short_used_pct=d.get("short_used_pct"),
            short_resets_at=dt(d.get("short_resets_at")),
            breakdown=d.get("breakdown"),
            status=d.get("status", STATUS_OK),
            source=d.get("source", SOURCE_CLI),
            last_updated=dt(d.get("last_updated")) or utcnow(),
            error=d.get("error"),
            detail=d.get("detail"),
            plan=d.get("plan"),
            resets=d.get("resets") if isinstance(d.get("resets"), list) else None,
            warnings=d.get("warnings") if isinstance(d.get("warnings"), list) else None,
        )


class ProviderError(Exception):
    """A fetch failed (network, HTTP 5xx, etc.). Last good data stays on screen."""


class AuthStale(ProviderError):
    """The CLI's stored credentials are expired, missing, or rejected."""


class ClientRefused(ProviderError):
    """The service refused this app as a client (e.g. Google's 403 SUBSCRIPTION_REQUIRED)."""


class ShapeError(ProviderError):
    """The response did not have the shape we expect. Raw body goes to debug.log."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw
