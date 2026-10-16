# Journal + NIFTY / BANKNIFTY / SENSEX update

This package is a dry-run research build based on repository commit 57a97c5.
It has not been tested with a logged-in Kite account or real orders. It does
not establish that the strategy is profitable or live-safe.

## Start on Windows

1. Extract the ZIP into a new folder. Read this guide before running it.
2. Make sure no older engine is managing a live position. The original
   launch scripts force-stop running app.py processes.
3. Double-click run.bat. Python must be installed.
4. Enter your newly rotated Kite and AI credentials in Settings. The package
   deliberately contains no credentials, token cache or previous analysis.
5. Keep DRY RUN selected. Choose NIFTY, BANKNIFTY or SENSEX and Save Settings.
6. Login, then Analyze (no entry) or Execute (simulated entry and monitoring).
7. Open Trade journal & review notes at the top of the dashboard.
8. Download all notes and events (ZIP) to share the run history for review.

One index/run is active at a time. Changing index during a run is rejected.
Expiry, strike interval and lot sizes come from Kite's current instrument
list. The nearest available expiry is selected; there is no assumption that
every index has weekly contracts. SENSEX quotes use BSE and options/futures
use BFO; NIFTY and BANKNIFTY derivatives use NFO. Account segment access and
broker order restrictions still apply.

The release's example settings use dry_run=true, one lot, a 500 rupee
simulated stop and 1000 rupee simulated target. These are test defaults,
not suitable-risk recommendations. Original account settings were not altered.

## What is saved

Each run gets a unique ID and two files in journals/YYYYMMDD/:

- A readable Markdown note: settings and source fingerprints, decision and
  reason, instrument/strike/expiry/exchange/quantity, entry/exit prices,
  underlying price when available, timestamps, exit trigger, gross P&L,
  observed best/worst P&L, errors, settings changes and reviewer notes.
- JSONL events for reproducible analysis: also includes technical logs and
  P&L snapshots approximately every five seconds. Best/worst leg P&L is
  observed at the monitoring interval, not every exchange tick.

Times in the journal are IST. Existing strategy time checks still use the
machine's local clock: run on a machine set to India time.

Entries/exits in dry run use latest available traded prices. They do not
simulate order-book fills, partial fills, latency, costs or liquidity.
Missing underlying prices/timestamps are explicitly unavailable, not invented.
Live order IDs are recorded as unverified submissions. The journal does not
claim live LTP estimates are executed fill prices. Original live execution
limitations remain, including lack of fill confirmation and broker-held stops.
An unfinished/failed run never proves that the brokerage account is flat.

No credentials are saved in journal settings. Known configured secrets are
redacted from log text. Review notes are free text: do not paste secrets there.
Keep this unauthenticated local dashboard on localhost.

## How decisions are explained

The unchanged decision policy uses an OI-wall base score. Seven inputs add
or subtract 0.05: PCR, VWAP, ORB, opening gap, ATM OI change, AI sentiment and
momentum. The journal records each contribution and the final score (clipped
between 0.05 and 0.95). These are heuristic scores, not calibrated probabilities.

Rule priority:
1. Expected move below the minimum: NO_TRADE.
2. Large gap: BOTH, even when the premium gate would fail. This legacy
   override is explicitly identified in the note.
3. Score >= CALL threshold: CALL if its movement gate passes; opposing AI
   changes the candidate to BOTH, subject to BOTH's gate.
4. Score <= PUT threshold: PUT, with the mirrored conflict rule.
5. Between thresholds: BOTH if its gate passes, otherwise NO_TRADE.

The movement gate compares expected movement to option premium(s) times a
margin. It remains an experimental approximation, not an intraday option
valuation model. India VIX is NIFTY-based and is labelled a proxy for other
indices. Index-specific calibration has not been established. The original
spot-versus-futures-VWAP comparison is retained so baseline runs are comparable;
this is a known strategy issue to evaluate, not a claim that the basis is zero.

The separate 2-of-3 technical bias is informational. Manual trades record the
trader's choice and explicitly say the automated analysis was bypassed.
Analyze-only, no-trade, canceled and failed runs are saved too.

## Review and correction workflow

1. Gather runs over multiple sessions; keep manual and automatic decisions,
   each index, and different source/settings fingerprints separate.
2. Review entries, exits, missing/stale data, max adverse/favorable observed
   P&L and rule contributions. Add an observation in the review note form.
3. Diagnose execution/data bugs before interpreting trading performance.
4. Propose one rule change, document why, and test it on subsequent unseen
   data. Do not tune a rule to explain a handful of losing trades.
5. Compare results after realistic costs, spread and slippage before drawing
   conclusions. This software never automatically changes the strategy.

## Verification and limits

The original mathematical/decision tests and added offline integration tests
cover multi-index routing, quote fallbacks, a complete automatic dry-run cycle,
notes/exports, no-trade and analysis-only paths, pre-entry Stop, and cleanup
when the second straddle leg throws an error. There is no claim of broker
integration validation, live-fill safety or a profitable backtest.

Pre-entry Stop now prevents entry at explicit checks. Successfully returned
first-leg entries remain tracked if a second leg raises. An ambiguous broker
response or submission that later fails still requires proper order-status
reconciliation before live use.

Run tests with: python -m pytest -q
