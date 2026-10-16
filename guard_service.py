"""Read-only guard data adapter. Preview cache never authorizes an entry."""
import datetime as dt
import threading
import time
import entry_guard
from markets import market

_LOCK=threading.Lock()
_CACHE={}
_INSTRUMENTS={}

def assess(client,settings,force=False):
    key=(id(client),settings['index'],settings.get('force_exit_time','15:15'),settings.get('time_exit_enabled'),settings.get('time_exit'))
    with _LOCK:
        old=_CACHE.get(key)
        if not force and old and time.monotonic()-old[0]<15:return old[1]
    now=entry_guard.clock();routing=market(settings['index'])
    cutoff=settings.get('force_exit_time','15:15')
    if settings.get('time_exit_enabled'):cutoff=min(cutoff,settings.get('time_exit','15:15'))
    try:
        # Outside session: no unnecessary broker calls, and never authorize entry.
        if not dt.time(9,30)<=now.time().replace(tzinfo=None)<dt.time(15,15):
            result=entry_guard.evaluate([],None,now,cutoff)
        else:
            instrument_key=(id(client),settings['index'],now.date())
            with _LOCK:instrument=_INSTRUMENTS.get(instrument_key)
            if instrument is None:
                instrument=client.get_index_instrument(routing['spot'],routing['spot_exchange'])
                with _LOCK:_INSTRUMENTS[instrument_key]=instrument
            bars=client.get_intraday_history(instrument['instrument_token'],now-dt.timedelta(days=10),now,interval='3minute')
            key_quote=f"{routing['spot_exchange']}:{routing['spot']}"
            spot=client.get_quote([key_quote])[key_quote]['last_price']
            result=entry_guard.evaluate(bars,spot,entry_guard.clock(),cutoff)
    except Exception as exc:
        result=entry_guard.blocked('DATA_ERROR','Could not obtain reliable fresh candles/quote. No entry.',
                                   error_type=type(exc).__name__,evaluated_at=entry_guard.clock().isoformat(),rules=entry_guard.RULES)
    result={**result,'index':settings['index']}
    with _LOCK:_CACHE[key]=(time.monotonic(),result)
    return result
