# gta6-twitch-ccu-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/lindaayu1200/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/lindaayu1200/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts Grand Theft Auto VI's release-day Twitch concurrency for hours 2 to 24 from the hour-1 print, using a weighted comparable-pool log-ratio quantile model: launch-matching and size-matched comparables set the shape, neighbouring hours are smoothed, and the spread is calibrated for pinball loss on the log scale.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

## Method

- Anchor on hour 1's viewer count taken from `known`.
- Build release-day curves for the comparables (hour index from `day_start`, hours 1 to 24, positive viewers), and drop launches marked `target-history`, `early-access-contaminated` or `Tier: weak`, or with too thin a curve.
- Weight each comparable: base 1, doubled when its unlock is a rolling midnight-local console unlock like this launch, and scaled by exp(-|log-anchor gap| / 0.8) for size similarity.
- Per hour, take the weighted empirical quantiles of log(viewers_h / viewers_1) across the pool, smooth them with the neighbouring hours (weights 1, 0.5, 0.5), then re-centre and widen around the median with fixed calibration constants before exponentiating onto the hour-1 scale.
- With no usable comparable, fall back to the house baseline shape p·exp(z·0.15·sqrt(h-1)).
- Every quantile is clamped to [1, 1e9] and kept non-decreasing per hour; output is one JSON file per case with 23 rows of five quantiles.

Standard library only, deterministic, no network.

---

<sub>Entered on [make.fun](https://make.fun/?ref=eh9bmgam&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
