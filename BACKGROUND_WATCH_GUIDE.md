# V3: background setup alerts, user-controlled execution

This is a DRY RUN research build. 169 offline tests passed; JavaScript syntax
was checked. Real Kite integration, broker fills and Windows desktop-notification
delivery have not been verified in this environment.

## Workflow

1. Extract into a new folder. Copy your existing journals folder if you want
   the older history included; keep a backup of that folder.
2. Start run.bat, enter rotated credentials, keep DRY RUN, select an index,
   save, and log into Kite.
3. Click Start Watch. The server checks the selected index about every
   15 seconds. Only one watch worker runs, even if Start is clicked twice.
4. Click Enable sound & desktop alerts and allow browser notifications.
   A short tone tests sound initialization. Sound requires a browser audio
   context permitted by the browser; OS/browser notification settings can
   still suppress desktop alerts. The dashboard always shows alert text.
5. You may minimize the browser and work elsewhere. Keep Python running,
   the dashboard tab open, the computer awake and internet connected.
6. When a setup alert arrives, inspect it and click the normal Execute button
   if you want a full assessment. That starts fresh score/AI analysis plus
   the mandatory fresh strict entry gate. It may still return NO_TRADE.
7. Watch continues between runs. New setup alerts are suppressed during an
   active engine run, while the observed market-state panel continues to update.
8. Download the day-end bundle from Trade journal & review notes.

Start Watch never calls the trading engine and never submits an order. Stop
Watch stops only scanning/alerts; it does NOT close a trade. The existing Stop
button remains responsible for stopping the engine and requesting square-off.
Starting/stopping the watcher is not the same as switching dry/live mode.

## Alerts

- SETUP FORMING: the strict filter sees an aligned, non-extended trend but
  the previous-candle breakout confirmation is missing. This is still NO_TRADE.
- SETUP DETECTED: READY_CALL or READY_PUT from completed-candle rules. This
  is not a full score/AI assessment, an order recommendation or a prediction
  that a profitable move is certain to occur.
- No setup alerts for pullback, reversal risk, sideways, extended, invalid,
  stale or out-of-session states. The panel displays the blocking condition.

Repeated identical index/state/candle alerts are suppressed. A 60-second
cooldown applies separately to forming and ready alerts, allowing an immediate
forming-to-ready upgrade. Alerts expire after 90 seconds and are invalidated
on a changed signal, index, run suppression or Stop Watch. If a scan is delayed
by over 45 seconds, old ready signals are marked stale. Execute never relies
on an alert's age or authority: it always rechecks the current market.

The server stores alerts and state changes in the market-watch journal. When
Execute is clicked with an active alert displayed, its ID is included in the
trade journal for attribution. The day-end summary includes alert counts and
trigger IDs. An alert with no resulting trade is not a winning/losing trade.

## Background operation limits

The watcher is a daemon thread in the Python application, not an installed
Windows service. It continues scanning/logging without the browser open, but
browser sound/desktop notifications require an open tab; reopening shows recent
active alerts. There are no phone, email or Telegram messages. A computer that
is asleep/offline or an expired Kite login cannot provide reliable updates.
After restarting Python, log in/restore your session and click Start Watch again.

Watch uses the existing selected index (NIFTY, BANKNIFTY or SENSEX), not a
simultaneous scanner across all indices. Before 09:30 IST and after the entry
cutoff it reports NO_TRADE. It uses historical completed 3-minute candles; this
is not tick-by-tick prediction. Market-state polling during open trades remains
warning-only and does not introduce a new automatic exit or reverse position.

The original live-fill confirmation and other execution limitations remain.
Use DRY RUN only for this testing phase. See STRICT_GATE_AND_DAY_END.md for
exact filter rules and JOURNAL_GUIDE.md for the journal and original limitations.
This V3 guide takes precedence over earlier browser-only monitoring descriptions.
