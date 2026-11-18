"""Weighted comparable-pool log-ratio quantile forecast for Twitch launch CCU.

Anchors on the observed hour-1 concurrency and models the release-day decay as
log ratios ln(v_h / v_1) derived from a pool of comparable launches. Comparables
are filtered for data quality, weighted by unlock-pattern match and size
similarity to the target, and their weighted empirical log-ratio quantiles form
a non-parametric profile per hour. Profiles are smoothed across neighbouring
hours, adjusted around the median with fixed calibration, then exponentiated
back onto the anchor scale. An empty pool falls back to a Gaussian-shaped house
baseline.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z_HOUSE = (-1.6448536, -0.6744897, 0.0, 0.6744897, 1.6448536)
BAD_MARKERS = ("target-history", "early-access-contaminated", "Tier: weak")
HOURS = tuple(range(2, 25))
MIN_Q, MAX_Q = 1.0, 1e9
MIN_H1 = 50.0
MIN_COVERED = 20


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_finite(value: Any) -> float | None:
    """Return a finite float for a numeric value, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def hour_index(day_start: datetime, point_from: Any) -> int | None:
    """1-based release-day hour index for a point timestamp."""
    ts = parse_time(point_from)
    if ts is None:
        return None
    delta = (ts - day_start).total_seconds() / 3600.0
    return int(math.floor(delta)) + 1


def get_anchor(case: dict[str, Any]) -> float:
    """Hour-1 anchor p from known viewers, at least 1."""
    known = case.get("known")
    if isinstance(known, list):
        for row in known:
            if not isinstance(row, dict):
                continue
            if row.get("hour") == 1:
                v = to_finite(row.get("viewers"))
                if v is not None and v > 0.0:
                    return max(1.0, v)
    return 1.0


def is_bad_comparable(comp: dict[str, Any]) -> bool:
    """True if unlock or notes text contains an exclusion marker."""
    parts: list[str] = []
    for key in ("unlock", "notes"):
        val = comp.get(key)
        if isinstance(val, str):
            parts.append(val)
    text = "\n".join(parts)
    return any(marker in text for marker in BAD_MARKERS)


def build_release_curve(comp: dict[str, Any]) -> dict[int, float] | None:
    """Return hour->viewers map for hours 1..24, or None if unusable."""
    day_start = parse_time(comp.get("day_start"))
    hourly = comp.get("hourly")
    if day_start is None or not isinstance(hourly, list):
        return None
    curve: dict[int, float] = {}
    for point in hourly:
        if not isinstance(point, dict):
            continue
        h = hour_index(day_start, point.get("from"))
        if h is None or h < 1 or h > 24 or h in curve:
            continue
        v = to_finite(point.get("viewers"))
        if v is not None and v > 0.0:
            curve[h] = v
    if len(curve) < MIN_COVERED:
        return None
    h1 = curve.get(1)
    if h1 is None or h1 < MIN_H1:
        return None
    return curve


class PoolMember:
    __slots__ = ("curve", "unlock", "weight", "h1")

    def __init__(self, curve: dict[int, float], unlock: str, weight: float):
        self.curve = curve
        self.unlock = unlock
        self.weight = weight
        self.h1 = curve[1]


def build_pool(case: dict[str, Any], anchor: float) -> list[PoolMember]:
    """Construct weighted comparable pool."""
    comparables = case.get("comparables")
    if not isinstance(comparables, list):
        return []
    members: list[PoolMember] = []
    for comp in comparables:
        if not isinstance(comp, dict):
            continue
        if is_bad_comparable(comp):
            continue
        curve = build_release_curve(comp)
        if curve is None:
            continue
        unlock = comp.get("unlock") if isinstance(comp.get("unlock"), str) else ""
        w = 1.0
        if "midnight local" in unlock and "rolling" in unlock:
            w *= 2.0
        h1 = curve[1]
        # size similarity
        if anchor > 0.0 and h1 > 0.0:
            w *= math.exp(-abs(math.log(anchor) - math.log(h1)) / 0.8)
        else:
            w = 0.0
        members.append(PoolMember(curve, unlock, w))

    if not members:
        return []

    kept = [m for m in members if m.weight > 1e-6]
    if not kept:
        # fall back to equal weights on all pool members
        for m in members:
            m.weight = 1.0
        kept = members
    return kept


def weighted_quantile(values: list[tuple[float, float]], tau: float) -> float:
    """First value where accumulated weight reaches tau's share (ascending)."""
    if not values:
        return 0.0
    values.sort(key=lambda x: x[0])
    total = sum(w for _, w in values)
    if total <= 0.0:
        return values[0][0]
    target = tau * total
    acc = 0.0
    for v, w in values:
        acc += w
        if acc >= target - 1e-15:
            return v
    return values[-1][0]


def house_log_baseline(hour: int) -> list[float]:
    """House baseline log-shape quantiles for one hour."""
    s = 0.15 * math.sqrt(hour - 1)
    return [z * s for z in Z_HOUSE]


def profile_quantiles(pool: list[PoolMember]) -> dict[int, list[float]]:
    """Raw (unsmoothed) log-ratio quantiles per hour 2..24."""
    raw: dict[int, list[float]] = {}
    for h in HOURS:
        samples: list[tuple[float, float]] = []
        for m in pool:
            vh = m.curve.get(h)
            if vh is None or vh <= 0.0 or m.h1 <= 0.0:
                continue
            lr = math.log(vh / m.h1)
            if math.isfinite(lr):
                samples.append((lr, m.weight))
        if samples:
            raw[h] = [weighted_quantile(list(samples), tau) for tau in LEVELS]
        else:
            raw[h] = house_log_baseline(h)
    return raw


def smooth_profile(raw: dict[int, list[float]]) -> dict[int, list[float]]:
    """Smooth 5 quantile series across neighbouring hours (weights 1,0.5,0.5)."""
    smoothed: dict[int, list[float]] = {}
    for h in HOURS:
        out = [0.0] * 5
        total_w = 0.0
        for nh in (h - 1, h, h + 1):
            if nh not in raw:
                continue
            dist = abs(nh - h)
            w = 1.0 / (1.0 + dist)
            vals = raw[nh]
            for i in range(5):
                out[i] += w * vals[i]
            total_w += w
        if total_w <= 0.0:
            smoothed[h] = list(raw.get(h, house_log_baseline(h)))
        else:
            smoothed[h] = [x / total_w for x in out]
    return smoothed


def calibrate_and_scale(smoothed_q: list[float], anchor: float) -> list[float]:
    """Apply calibration around median and exponentiate onto anchor scale."""
    q05, q25, q50, q75, q95 = smoothed_q
    m = q50
    z05 = m + 0.9 * (q05 - m)
    z25 = m + 0.8 * (q25 - m)
    z50 = m
    z75 = m + 1.2 * (q75 - m)
    z95 = m + 0.9 * (q95 - m)
    zs = [z05, z25, z50, z75, z95]
    out: list[float] = []
    for z in zs:
        try:
            val = anchor * math.exp(z)
        except OverflowError:
            val = math.inf
        if not math.isfinite(val):
            val = MAX_Q
        val = max(MIN_Q, min(MAX_Q, val))
        out.append(val)
    # enforce non-decreasing
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return out


def house_baseline_forecast(anchor: float) -> dict[str, Any]:
    """Competition house baseline forecast for empty pool."""
    rows = []
    for h in HOURS:
        s = 0.15 * math.sqrt(h - 1)
        zs = [z * s for z in Z_HOUSE]
        qs = calibrate_and_scale_from_z(zs, anchor)
        rows.append({"hour": h, "q": qs})
    return {"hours": rows}


def forecast_case(case: Any) -> dict[str, Any]:
    """Produce forecast output for one case."""
    if not isinstance(case, dict):
        return house_baseline_forecast(1.0)
    anchor = get_anchor(case)
    if not (math.isfinite(anchor) and anchor >= 1.0):
        anchor = 1.0
    pool = build_pool(case, anchor)

    if not pool:
        return house_baseline_forecast(anchor)

    raw = profile_quantiles(pool)
    smoothed = smooth_profile(raw)
    rows = []
    for h in HOURS:
        qs = calibrate_and_scale(smoothed[h], anchor)
        rows.append({"hour": h, "q": qs})
    return {"hours": rows}


def calibrate_and_scale_from_z(zs: list[float], anchor: float) -> list[float]:
    """Exponentiate given log quantiles and clamp/enforce monotonicity (fallback)."""
    out: list[float] = []
    for z in zs:
        try:
            val = anchor * math.exp(z)
        except OverflowError:
            val = math.inf
        if not math.isfinite(val):
            val = MAX_Q
        val = max(MIN_Q, min(MAX_Q, val))
        out.append(val)
    for i in range(1, len(out)):
        if out[i] < out[i - 1]:
            out[i] = out[i - 1]
    return out


def main() -> None:
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        names = sorted(os.listdir(input_dir))
    except OSError:
        names = []

    for name in names:
        if not name.endswith(".in"):
            continue
        in_path = os.path.join(input_dir, name)
        case: Any = {}
        try:
            with open(in_path, "r", encoding="utf-8") as f:
                case = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            case = {}
        if not isinstance(case, dict):
            case_id = name[:-3]
            case = {}
        else:
            case_id = case.get("id")
            if not isinstance(case_id, str) or not case_id:
                case_id = name[:-3]
        result = forecast_case(case)
        out_path = os.path.join(output_dir, case_id + ".out")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, separators=(",", ":"))
        except OSError:
            pass


if __name__ == "__main__":
    main()
