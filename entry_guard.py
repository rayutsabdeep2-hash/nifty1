"""Deterministic, fail-closed entry guard. No predictive accuracy claim.

Closed 3-minute index candles; EMA8/20 + ATR14, with conservative confirmation.
The same gate protects automatic and manual entries. BOTH is intentionally blocked.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
VERSION = 'strict-entry-v1'
RULES = {
    'version':VERSION,'interval':'3minute','ema_fast':8,'ema_slow':20,'atr_period':14,
    'minimum_closed_bars':30,'minimum_today_bars':5,'max_stale_seconds':180,
    'minimum_ema_separation_atr':0.10,'maximum_extension_atr':1.0,
    'maximum_price_drift_atr':0.50,'entry_start_ist':'09:30','entry_end_ist':'15:15',
    'pullback':'Aligned EMA trend but latest close moves against it, or price is beyond EMA8 against the trend.',
    'reversal':'Previous EMA trend loses EMA20 on a closed bar or EMA8/20 cross.',
    'confirmation':'Aligned trend and slow EMA slope; closed bar breaks previous bar high/low; live quote still beyond that level.',
    'blocked':'Missing/stale/invalid data, outside session, sideways, pullback, reversal risk, extended, no confirmation, conflicting direction or BOTH.',
    'open_positions':'Warning only; does not automatically reverse or add a new exit rule.',
    'meaning':'Observed price-action filter, not a forecast or calibrated probability.'
}

def clock():return dt.datetime.now(IST)

def _time(value):
    if isinstance(value,str):value=dt.datetime.fromisoformat(value)
    if not isinstance(value,dt.datetime):raise ValueError('invalid candle timestamp')
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)

def _ema(values,period):
    alpha=2/(period+1);out=[values[0]]
    for v in values[1:]:out.append(alpha*v+(1-alpha)*out[-1])
    return out

def blocked(status,reason,**data):
    return {'status':status,'allowed_direction':None,'reason':reason,'rule_version':VERSION,**data}

def evaluate(candles, spot, now=None, cutoff='15:15'):
    now=_time(now or clock());observed={'evaluated_at':now.isoformat(),'entry_cutoff_ist':cutoff,'rules':RULES}
    try:
        stop=dt.time.fromisoformat(cutoff)
        if not dt.time(9,30)<=now.time().replace(tzinfo=None)<min(stop,dt.time(15,15)):
            return blocked('OUTSIDE_ENTRY_WINDOW','Entries only after 09:30 IST and before the configured exit cutoff (latest 15:15).',**observed)
        spot=float(spot)
        if not math.isfinite(spot) or spot<=0:raise ValueError('invalid spot')
        by_time={}
        for c in candles:
            t=_time(c['date'])
            if t+dt.timedelta(minutes=3)>now:continue  # never use unfinished candles
            if not dt.time(9,15)<=t.time().replace(tzinfo=None)<dt.time(15,30):continue
            o,h,l,close=[float(c[k]) for k in ('open','high','low','close')]
            if not all(math.isfinite(v) and v>0 for v in (o,h,l,close)) or not l<=min(o,close)<=max(o,close)<=h:
                raise ValueError('invalid OHLC')
            if t in by_time:raise ValueError('duplicate candle timestamp')
            by_time[t]={'date':t.isoformat(),'open':o,'high':h,'low':l,'close':close}
        bars=[by_time[t] for t in sorted(by_time)][-120:]
        if len(bars)<30:return blocked('INSUFFICIENT_DATA','Need 30 completed 3-minute candles for the indicator warm-up.',**observed)
        today=[b for b in bars if _time(b['date']).date()==now.date()]
        if len(today)<5:return blocked('INSUFFICIENT_DATA','Need at least the first 15 minutes of completed candles today.',**observed)
        latest=_time(bars[-1]['date'])
        if (now-latest-dt.timedelta(minutes=3)).total_seconds()>180:
            return blocked('STALE_DATA','Latest completed candle is too old.',last_candle=latest.isoformat(),**observed)
        # Reject holes in the latest 5 bars of this session.
        if any((_time(b['date'])-_time(a['date'])).total_seconds()!=180 for a,b in zip(today[-5:],today[-4:])):
            return blocked('MISSING_BARS','Recent 3-minute candles have gaps.',**observed)
        closes=[b['close'] for b in bars];fast=_ema(closes,8);slow=_ema(closes,20)
        ranges=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(bars,bars[1:])]
        atr=sum(ranges[-14:])/14
        if atr<=0:return blocked('NO_VOLATILITY','ATR is zero; no reliable entry.',**observed)
        a,b=bars[-2:];previous=1 if fast[-2]>slow[-2] else -1 if fast[-2]<slow[-2] else 0
        trend=1 if fast[-1]>slow[-1] else -1 if fast[-1]<slow[-1] else 0
        details={**observed,'spot':spot,'signal_candle':b['date'],'candles':bars,
                 'ema8':fast[-1],'ema20':slow[-1],'ema20_previous':slow[-2],'atr14':atr,
                 'prior_trend':previous,'trend':trend,'previous_high':a['high'],'previous_low':a['low']}
        if previous and (trend!=previous or (previous==1 and b['close']<slow[-1]) or (previous==-1 and b['close']>slow[-1])):
            return blocked('REVERSAL_RISK','Previous trend lost EMA20 or EMA8 crossed EMA20. Wait for a new confirmed structure.',**details)
        if not trend or abs(fast[-1]-slow[-1])<.10*atr or trend*(slow[-1]-slow[-2])<=0:
            return blocked('SIDEWAYS','EMA alignment/separation/slope is too weak.',**details)
        if trend*(b['close']-a['close'])<0 or trend*(b['close']-fast[-1])<0 or trend*(spot-fast[-1])<0:
            return blocked('PULLBACK','Price is moving against the prevailing EMA trend. Wait; do not enter during this pullback.',**details)
        if abs(spot-fast[-1])>atr:
            return blocked('EXTENDED','Price is more than 1 ATR from EMA8; do not chase.',**details)
        level=a['high'] if trend==1 else a['low']
        if trend*(b['close']-level)<=0 or trend*(spot-level)<=0:
            return blocked('WAIT_CONFIRMATION','Need a completed candle beyond the previous high/low, with the current quote still beyond that level.',**details)
        if abs(spot-b['close'])>.5*atr:
            return blocked('PRICE_DRIFT','Current quote moved more than 0.5 ATR from the confirming close.',**details)
        return {'status':'READY_CALL' if trend==1 else 'READY_PUT','allowed_direction':'CALL' if trend==1 else 'PUT',
                'reason':'Closed candle confirms an aligned trend; no pullback/reversal/extension veto is active.',
                'rule_version':VERSION,**details}
    except (KeyError,TypeError,ValueError,OverflowError):
        return blocked('INVALID_DATA','Missing or invalid candle, quote or cutoff data. Entry blocked.',**observed)

def permits(signal,direction):
    return direction in ('CALL','PUT') and signal.get('allowed_direction')==direction
