# V2: strict entry gate and day-end review

Use this build in DRY RUN. It adds observed price-action vetoes, not guaranteed
reversal prediction or a proven profitable strategy. It has not been verified
with real broker orders. Prior live execution limitations remain.

## Decision order

1. The existing OI/PCR/VWAP/ORB/gap/AI/momentum scoring selects a candidate.
2. Fresh completed 3-minute index candles and a fresh quote are requested.
3. The strict gate can reject ANY candidate, including manual trades and BOTH.
4. Only a matching READY_CALL or READY_PUT can proceed to order preparation.
5. Stop and journal-health checks still apply before BUY.
6. The existing configured stop/target/time rules manage open positions.

The new gate is mandatory and has no bypass switch in the dashboard. BOTH is
blocked: a directional confirmation cannot justify a two-leg straddle. The old
large-gap rule may still produce a BOTH *candidate*, but its final decision is
NO_TRADE. A READY label alone does not authorize an order or guarantee profit.

## Exact first-version rules

- Time: after 09:30 IST, before the earlier enabled exit cutoff or 15:15 IST.
- Warm-up: at least 30 completed 3-minute candles, at least five today. Up to
  120 recent completed candles are retained and used, including prior sessions.
- Data: positive finite OHLC/spot values; no duplicate timestamps or missing
  bars in the latest five same-day candles. A candle must be completed; the
  latest completed candle's close cannot be more than 180 seconds old.
- Trend: EMA8 vs EMA20, aligned EMA20 slope, separation at least 0.10 ATR14.
  ATR14 is the arithmetic mean of the last 14 true ranges.
- REVERSAL_RISK: the prior EMA trend loses EMA20 on a close or EMAs cross.
- PULLBACK: latest close moves against the trend, or close/live quote is on
  the adverse side of EMA8. Wait for subsequent confirmation; do not enter.
- EXTENDED: current price is more than 1 ATR14 from EMA8. Do not chase.
- Confirmation: completed candle closes beyond the previous candle's high
  for CALL or low for PUT; the live quote must remain beyond the same level.
- PRICE_DRIFT: reject if quote differs from confirmation close by >0.5 ATR14.
- Missing/stale/error/sideways/unconfirmed/conflicting signals mean NO_TRADE.

These conservative defaults are experimental. They can reject valid trades
and still admit losing ones. Their purpose is to define a consistent baseline
that can be audited. They are not a complete pullback-entry trading style.

## Visible status and recorded evidence

While the dashboard is open, a read-only status panel refreshes every 15 seconds.
It shows READY_CALL/PUT, PULLBACK, REVERSAL_RISK, SIDEWAYS, EXTENDED,
WAIT_CONFIRMATION, PRICE_DRIFT or data/session blocks, with a reason and timestamp.
If the status request fails or times out, the display becomes NO TRADE rather
than retaining an old READY label. Historical candles can be delayed by the
provider; no claim of tick-by-tick reversal detection is made.

Market-state changes are saved in a separate market_watch journal even if no
Execute was clicked. Monitoring this panel requires the app and dashboard to
stay open. Entry-time checks run on the server independently of UI polling and
always request fresh data. For open trades, the panel is a warning only; this
release does not auto-exit or reverse a position based on that panel.

Every run now saves a rulebook, source/settings fingerprints, raw OI and volume
snapshots, each score contribution, candidate direction, final strict-gate
verdict and candle/EMA/ATR evidence. Existing entry/exit, P&L, maximum observed
favorable/adverse leg P&L, errors, review notes and five-second P&L samples remain.
It is an audit of decisions and sampled data, not a complete exchange tick feed.

## Day-end process

1. Open Trade journal & review notes.
2. Select the session date (IST).
3. Download review bundle. Blank date exports all saved days.
4. Upload the ZIP in this chat and ask for a day-end review.

The bundle contains DAY_END_REVIEW.md, day_end_summary.json, trades.csv,
original notes and raw JSONL events. Performance is grouped by index, automatic
versus manual style, code version and settings fingerprint. Only finished,
matched dry-run entry/exit pairs contribute to gross simulated performance.
Failed, damaged, unfinished, recovery and unverified live runs are excluded.
Gross results omit commissions, taxes, bid/ask spread and slippage. Net P&L is
unknown, not silently treated as equal to gross. Review missed opportunities
from recorded prices cautiously; there is no counterfactual-fill simulator.

ChatGPT cannot automatically fetch logs from this computer. No upload,
scheduled analysis, auto-learning, code deployment or automatic tuning is set
up. Upload each bundle. Proposed code changes should be versioned, tested on
new sessions and compared with the prior baseline after costs. Do not optimize
rules to explain one day's losses or assume that higher win rate means higher
profitability.

## Future development, not implemented here

1. Separate named styles: ORB breakout/retest; EMA/VWAP continuation;
   confirmed pullback resumption; carefully defined reversal setups.
2. Each style needs its own entry/exit rules, style identifier, risk limits,
   journal grouping and out-of-sample evaluation. Do not combine their results.
3. MCX CRUDEOILM and NATGAS mini-contract support belongs in a separate commodity
   futures adapter. Verify current broker symbols/products at implementation.
   These are commodities, not equity indices. Contract units, expiry, tick
   value, margin, quantity, rupee P&L and exchange session/holiday schedules
   must be obtained and validated independently. Do not reuse the index-option
   CALL/PUT, ATM-selection, OI-wall, VIX or 15:15 exit logic unchanged.

## Installation and compatibility

Extract V2 into a new folder and start with blank/rotated credentials and
DRY RUN. Keep the old folder and logs. If moving the old journals directory,
copy it into this folder before starting; unique filenames preserve prior runs.
The day-end reader can read V1 notes but V1 has no strict-gate evidence.

Windows needs the tzdata package for IST timestamps; it is now included in
requirements.txt. Example settings remain one lot, simulated 500 rupee stop
and 1000 rupee target. These are test defaults, not a risk recommendation.

See JOURNAL_GUIDE.md for baseline behaviour, installation, and original live
execution limitations; this V2 document takes precedence for strict entries,
BOTH and export behaviour.
