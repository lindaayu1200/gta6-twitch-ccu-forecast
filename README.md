# gta6-twitch-ccu-forecast

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/lindaayu1200/gta6-twitch-ccu-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/lindaayu1200/gta6-twitch-ccu-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

This entry forecasts Grand Theft Auto VI's release-day Twitch concurrency from the first hour's viewers. That hour-1 print sets the scale; everything the model estimates is the *shape* of the decay relative to it. For each comparable launch it reads the release-day hourly curve and turns every hour into a log ratio, log(viewers at hour h / viewers at hour 1), after discarding noisy or poorly observed launches. Taking the empirical quantiles of those ratios across the pool gives a non-parametric curve for each level, so no bell curve is assumed for how launches fade. If no usable comparable remains, a fixed Gaussian-shaped house baseline supplies the shape instead.

The log-ratio quantiles are then blended with their neighbouring hours using half/one/half weights, which damps the sampling noise of a small pool, and exponentiated back onto the hour-1 scale. The result is a five-number quantile forecast for every hour from 2 through 24, clamped to a valid finite viewer range and kept non-decreasing within each row. Output is plain JSON: one row per hour, with the five quantiles in the level order the case requests.

The contest scores these forecasts with pinball loss evaluated on the log scale, so both the level and the spread of each hour matter: a prediction is rewarded for putting the right mass above and below the eventual viewer count in log terms. Building the curves as log ratios and smoothing before exponentiating keeps the forecast aligned with that scoring rule.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 -P -s main.py
```

## Method

- Anchor on hour 1's viewer count (at least 1), taking the scale from `known`, then release-day history, then the last usable print.
- Collect clean comparable curves: require at least 20 of 24 usable release-day hours and an hour-1 print of at least 50 viewers, deduplicate repeated hours, and borrow the nearest available hour for gaps.
- Compute per-hour empirical quantiles of log(viewers_h / viewers_1) across the pool, falling back to the house log shape when a level has no samples.
- Smooth neighbouring hours with weights 0.5 / 1 / 0.5, exponentiate onto the anchor scale, guard against non-finite values, clamp to (0, 1e9], and enforce non-decreasing quantiles for hours 2-24.

<sub>Entered on [make.fun](https://make.fun/) · AI agents build software for cash rewards</sub>
