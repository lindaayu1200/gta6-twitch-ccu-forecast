"""Empirical-quantile CCU forecast for game-launch comparables.

We treat the observed hour-1 concurrency as a scale anchor and estimate the
*shape* of the launch-day decay from a pool of comparable launches.  For every
pool member we compute the log ratio log(viewers_h / viewers_1) on its own
release day; the empirical distribution of those log ratios at each hour gives
a non-parametric quantile curve (no normality assumption).  Neighbouring hours
are blended to damp sampling noise, and the log quantiles are exponentiated
back onto the anchor scale.  An empty pool falls back to a Gaussian-shaped
house baseline with a fixed 0.15-per-sqrt-hour log dispersion.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
Z_HOUSE = (-1.6448536, -0.6744897, 0.0, 0.6744897, 1.6448536)
BAD_NOTES = ("early-access-contaminated", "Tier: weak", "target-history")
MIN_Q, MAX_Q = 1.0, 1e9
MIN_ANCHOR_VIEWERS = 50.0
MIN_COVERED_HOURS = 20
HOURS = tuple(range(2, 25))


def parse_time(value: Any) -> datetime | None:
    """Return an aware UTC datetime for an ISO-8601 string, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def hour_of(day_start: datetime, value: Any) -> int | None:
    """Map a timestamp to a 1-based release-day hour (whole hours since start)."""
    stamp = parse_time(value)
    if stamp is None:
        return None
    return int(math.floor((stamp - day_start).total_seconds() / 3600.0)) + 1


def finite_number(value: Any) -> float | None:
    """Coerce a JSON number to a finite float, rejecting bools and nulls."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def empirical_quantile(sorted_values: list[float], tau: float) -> float:
    """Linearly interpolated empirical quantile (type-7) of sorted values."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * min(max(tau, 0.0), 1.0)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def anchor_viewers(case: dict[str, Any]) -> float:
    """Resolve p1 from known hour 1, history hour 1, then the last known point."""
    known = case.get("known")
    known = known if isinstance(known, list) else []
    for row in known:
        if isinstance(row, dict) and row.get("hour") == 1:
            value = finite_number(row.get("viewers"))
            if value is not None and value >= 1.0:
                return value
    history = case.get("history_hourly")
    history = history if isinstance(history, list) else []
    day_start = parse_time(case.get("day_start"))
    for row in history:
        if not isinstance(row, dict):
            continue
        is_hour_one = row.get("hour") == 1 or (
            day_start is not None and hour_of(day_start, row.get("from")) == 1
        )
        if is_hour_one:
            value = finite_number(row.get("viewers"))
            if value is not None and value >= 1.0:
                return value
    for row in reversed(known):
        if isinstance(row, dict):
            value = finite_number(row.get("viewers"))
            if value is not None and value >= 1.0:
                return value
    return 1.0


# Hour 1 is the denominator of every log ratio: a launch anchored by only a
# handful of viewers (or by noise around zero) produces explosive ratios, so
# require a real hour-1 print.  Requiring 20 of 24 usable hours stops a largely
# unobserved day from being padded into a complete-looking curve by the
# nearest-hour borrow below; each launch still enters the pool at most once
# per hour, even if its input repeats a timestamp.
def build_pool(case: dict[str, Any]) -> list[list[float]]:
    """Collect 24-point release-day viewer curves from eligible comparables."""
    pool: list[list[float]] = []
    comparables = case.get("comparables")
    if not isinstance(comparables, list):
        return pool
    for comp in comparables:
        if not isinstance(comp, dict):
            continue
        text = " ".join(str(v) for v in comp.values() if isinstance(v, str))
        if any(marker in text for marker in BAD_NOTES):
            continue
        day_start = parse_time(comp.get("day_start"))
        hourly = comp.get("hourly")
        if day_start is None or not isinstance(hourly, list):
            continue
        values: dict[int, float] = {}
        for point in hourly:
            if not isinstance(point, dict):
                continue
            hour = hour_of(day_start, point.get("from"))
            if hour is None or not 1 <= hour <= 24 or hour in values:
                continue
            value = finite_number(point.get("viewers"))
            if value is not None and value > 0.0:
                values[hour] = value
        if len(values) < MIN_COVERED_HOURS:
            continue
        originals = sorted(values)
        curve = [
            values[hour] if hour in values
            else values[min(originals, key=lambda h: (abs(h - hour), h))]
            for hour in range(1, 25)
        ]
        if curve[0] < MIN_ANCHOR_VIEWERS:
            continue
        pool.append(curve)
    return pool


def house_log_shape(hour: int, count: int) -> list[float]:
    """The house baseline's log shape at one hour, for the first `count` levels."""
    return [z * 0.15 * math.sqrt(hour - 1) for z in Z_HOUSE[:count]]


def log_quantiles(pool: list[list[float]], levels: list[float]) -> dict[int, list[float]]:
    """Empirical log-ratio quantile arrays for each forecast hour."""
    result: dict[int, list[float]] = {}
    for hour in HOURS:
        ratios: list[float] = []
        for curve in pool:
            base, current = curve[0], curve[hour - 1]
            if base > 0.0 and current > 0.0:
                ratio = math.log(current / base)
                if math.isfinite(ratio):
                    ratios.append(ratio)
        ratios.sort()
        result[hour] = (
            [empirical_quantile(ratios, tau) for tau in levels] if ratios
            else house_log_shape(hour, len(levels))
        )
    return result


def smooth(hour: int, raw: dict[int, list[float]], width: int) -> list[float]:
    """Blend a quantile array with its (clamped) neighbours, weights .5/1/.5."""
    total = [0.0] * width
    weight_sum = 0.0
    for neighbour, weight in ((hour - 1, 0.5), (hour, 1.0), (hour + 1, 0.5)):
        for index, value in enumerate(raw[min(24, max(2, neighbour))]):
            total[index] += weight * value
        weight_sum += weight
    return [value / weight_sum for value in total]


def scaled_quantile(p1: float, log_value: float) -> float:
    """Exponentiate a log shape onto the anchor scale; overflow is non-finite."""
    try:
        return p1 * math.exp(log_value)
    except OverflowError:
        return math.inf


def make_quantiles(value: float) -> float:
    """Clamp a forecast quantile into the valid, finite output range."""
    if not math.isfinite(value):
        return MAX_Q
    return min(MAX_Q, max(MIN_Q, value))


def widen_outer_quantiles(quantiles: list[float]) -> list[float]:
    """Stretch the outer levels away from the median on the log scale."""
    # Widen q05/q95 to 1.2x their log distance from the median: this corrects the
    # mild undercoverage of the empirical tails seen on 20+ launch comparisons.
    if len(quantiles) < 5:
        return list(quantiles)
    q05, q50, q95 = quantiles[0], quantiles[2], quantiles[4]
    if not all(math.isfinite(q) and q > 0.0 for q in (q05, q50, q95)):
        return list(quantiles)
    centre = math.log(q50)
    widened = list(quantiles)
    widened[0] = scaled_quantile(1.0, centre + 1.2 * (math.log(q05) - centre))
    widened[4] = scaled_quantile(1.0, centre + 1.2 * (math.log(q95) - centre))
    return widened


def forecast(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Produce the 23-hour quantile forecast object for one case."""
    raw_levels = case.get("levels")
    levels = list(raw_levels) if isinstance(raw_levels, list) and len(raw_levels) == 5 else list(LEVELS)
    levels = [float(t) if finite_number(t) is not None else LEVELS[i] for i, t in enumerate(levels)]
    p1 = max(MIN_Q, anchor_viewers(case))
    pool = build_pool(case)
    raw = log_quantiles(pool, levels) if pool else {}
    rows: list[dict[str, Any]] = []
    for hour in HOURS:
        if pool:
            quantiles = [scaled_quantile(p1, x) for x in smooth(hour, raw, len(levels))]
            if not all(math.isfinite(q) for q in quantiles):
                quantiles = [scaled_quantile(p1, x) for x in house_log_shape(hour, len(levels))]
        else:
            quantiles = [scaled_quantile(p1, x) for x in house_log_shape(hour, len(levels))]
        quantiles = widen_outer_quantiles(quantiles)
        quantiles = [make_quantiles(q) for q in quantiles]
        for index in range(1, len(quantiles)):
            quantiles[index] = max(quantiles[index], quantiles[index - 1])
        rows.append({"hour": hour, "q": quantiles})
    return {"hours": rows}


def main() -> None:
    """Read every input case and write one JSON forecast per case."""
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    for filename in sorted(os.listdir(input_dir)):
        if not filename.endswith(".in"):
            continue
        try:
            with open(os.path.join(input_dir, filename), encoding="utf-8") as handle:
                case = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(case, dict):
            case = {}
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            case_id = filename[:-3]
        with open(os.path.join(output_dir, case_id + ".out"), "w", encoding="utf-8") as handle:
            json.dump(forecast(case), handle, allow_nan=False)


if __name__ == "__main__":
    main()
