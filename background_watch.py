"""One read-only background worker. This module has no order/engine API."""
from collections import deque
import datetime as dt
import threading
import time
import uuid

import entry_guard
import guard_service


class BackgroundWatch:
    def __init__(self, context, emit, interval=15):
        self.context=context;self.emit=emit;self.interval=interval
        self.lock=threading.RLock();self.stop_event=threading.Event();self.thread=None
        self.enabled=False;self.generation=0;self.last_checked=0;self.started_at=0
        self.signal=entry_guard.blocked('STOPPED','Background watch is stopped.')
        self.alerts=deque(maxlen=100);self.seen=deque(maxlen=200);self.last_alert_at={}

    def start(self):
        with self.lock:
            if self.enabled:return False
            if self.thread and self.thread.is_alive():raise RuntimeError('Previous scan is finishing; try Start again shortly.')
            self.enabled=True;self.generation+=1;generation=self.generation
            self.stop_event=threading.Event();self.started_at=time.time();self.last_checked=0
            self.signal=entry_guard.blocked('STARTING','Background watch is starting.')
            self.thread=threading.Thread(target=self._loop,args=(generation,self.stop_event),daemon=True,name='setup-watch')
            self.thread.start()
        return True

    def stop(self):
        with self.lock:
            self.enabled=False;self.generation+=1;self.stop_event.set()
            self.signal=entry_guard.blocked('STOPPED','Watch stopped. Trading engine and existing positions are unchanged.')
            for alert in self.alerts:alert['active']=False

    def _loop(self,generation,stop_event):
        while not stop_event.is_set():
            try:self.scan_once(generation)
            except Exception:
                # An alert/logging failure must not kill the worker or invoke trading.
                with self.lock:
                    if generation==self.generation:
                        self.signal=entry_guard.blocked('WATCH_ERROR','Watch could not complete a scan. No setup is authorized.')
                        for alert in self.alerts:alert['active']=False
            stop_event.wait(self.interval)

    def scan_once(self,generation):
        client,settings,running=self.context()
        if client is None:signal=entry_guard.blocked('LOGIN_REQUIRED','Login to Kite, then watch will resume.')
        else:signal={**guard_service.assess(client,settings), 'alerts_paused':bool(running)}
        with self.lock:
            if not self.enabled or generation!=self.generation:return
            self.last_checked=time.time()
            self._accept(signal,settings or {},suppress_alerts=running)

    def _accept(self,signal,settings,suppress_alerts=False):
        """Called under lock. Public in tests to verify deduplication deterministically."""
        old=self.signal;self.signal=signal
        index=signal.get('index',settings.get('index'))
        status=signal['status'];candle=signal.get('signal_candle')
        identity=(index,status,candle)
        for alert in self.alerts:
            if suppress_alerts or (alert['index'],alert['status'],alert['signal_candle'])!=identity:alert['active']=False
        if (old.get('index'),old.get('status'),old.get('signal_candle'))!=identity:
            self.emit({'type':'watch_state','signal':signal},settings)
        if suppress_alerts:return
        category='SETUP_READY' if status in ('READY_CALL','READY_PUT') else 'SETUP_FORMING' if status=='WAIT_CONFIRMATION' else None
        if category is None or not candle or identity in self.seen:return
        now=time.time();cooldown_key=(index,category)
        if now-self.last_alert_at.get(cooldown_key,0)<60:return
        direction=signal.get('allowed_direction') or ('CALL' if signal.get('trend')==1 else 'PUT' if signal.get('trend')==-1 else None)
        if direction is None:return
        alert={'id':uuid.uuid4().hex,'type':'watch_alert','category':category,'index':index,
               'status':status,'direction':direction,'signal_candle':candle,'active':True,
               'created_at':entry_guard.clock().isoformat(),'expires_at':now+90,
               'message':f'{index}: {direction} setup '+('detected. Click Execute for a fresh full assessment; it may still return NO TRADE.' if category=='SETUP_READY' else 'forming. Confirmation is still missing; do not enter yet.'),
               'reason':signal['reason'],'rule_version':signal.get('rule_version')}
        # Persist/broadcast before queueing so failures cannot look like audited alerts.
        self.emit(alert,settings)
        self.alerts.append(alert);self.seen.append(identity);self.last_alert_at[cooldown_key]=now

    def snapshot(self):
        with self.lock:
            stale=self.enabled and time.time()-(self.last_checked or self.started_at)>45
            signal=entry_guard.blocked('WATCH_STALE','Background scan is delayed; disregard previous setup alerts.') if stale else dict(self.signal)
            alerts=[{**a,'active':a['active'] and not stale and self.enabled and a['expires_at']>time.time()} for a in self.alerts]
            return {'enabled':self.enabled,'worker_alive':bool(self.thread and self.thread.is_alive()),
                    'last_checked':self.last_checked,'signal':signal,'alerts':alerts,'stale':stale}

    def alert_context(self,alert_id):
        # Context is attribution only; it NEVER authorizes an entry.
        for alert in self.snapshot()['alerts']:
            if alert['id']==alert_id:
                return {k:alert[k] for k in ('id','index','category','direction','created_at','signal_candle','active')}
        return None
