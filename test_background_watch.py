import datetime as dt
import json
import threading
import time
from types import SimpleNamespace

import pytest
import background_watch as module
from background_watch import BackgroundWatch
import guard_service
import engine
import trade_journal
import entry_guard
from test_journal_routing import FakeClient,settings_file


def signal(status='READY_CALL',candle='2026-10-16T10:00:00+05:30'):
    return {'index':'NIFTY','status':status,'signal_candle':candle,'trend':1,
            'allowed_direction':'CALL' if status=='READY_CALL' else None,
            'reason':'synthetic test','rule_version':'test'}


def watcher():
    events=[];w=BackgroundWatch(lambda:(None,{'index':'NIFTY'},False),lambda e,s:events.append(e))
    w.enabled=True;w.started_at=time.time();w.last_checked=time.time()
    return w,events


def test_alerts_are_read_only_and_deduplicated():
    w,events=watcher()
    for _ in range(5):w._accept(signal(),{'index':'NIFTY'})
    assert len(w.alerts)==1
    assert [e['type'] for e in events]==['watch_state','watch_alert']
    assert w.alerts[0]['category']=='SETUP_READY'
    assert 'Execute' in w.alerts[0]['message']
    # No reference to an execution/order callback is available to this service.
    assert not hasattr(w,'engine') and not hasattr(w,'place_order')


def test_forming_can_upgrade_to_ready_immediately():
    w,_=watcher();w._accept(signal('WAIT_CONFIRMATION'),{'index':'NIFTY'})
    w._accept(signal(),{'index':'NIFTY'})
    assert [a['category'] for a in w.alerts]==['SETUP_FORMING','SETUP_READY']
    assert not w.alerts[0]['active'] and w.alerts[1]['active']


def test_invalidation_expiry_and_stop():
    w,_=watcher();w._accept(signal(),{'index':'NIFTY'})
    aid=w.alerts[0]['id'];w.alerts[0]['expires_at']=time.time()-1
    assert not w.alert_context(aid)['active']
    w._accept(signal('PULLBACK'),{'index':'NIFTY'})
    assert not w.snapshot()['alerts'][0]['active']
    w.stop();assert not w.snapshot()['enabled']


def test_stale_scan_invalidates_ready_alerts():
    w,_=watcher();w._accept(signal(),{'index':'NIFTY'});w.last_checked=time.time()-60
    assert w.snapshot()['signal']['status']=='WATCH_STALE'
    assert not w.snapshot()['alerts'][0]['active']


def test_alerts_suppressed_during_run_but_signal_updates():
    w,_=watcher();w._accept(signal(),{'index':'NIFTY'},suppress_alerts=True)
    assert not w.alerts and w.signal['status']=='READY_CALL'


def test_worker_runs_without_browser_and_start_is_idempotent(monkeypatch):
    scanned=threading.Event();events=[]
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw: signal())
    def emitted(event, settings):
        events.append(event)
        if event['type']=='watch_alert':scanned.set()
    w=BackgroundWatch(lambda:(object(),{'index':'NIFTY'},False),emitted,interval=.02)
    try:
        assert w.start();assert not w.start();assert scanned.wait(1)
        # Join only after stop: no UI requests are involved in producing events.
    finally:w.stop();w.thread.join(timeout=1)
    assert not w.thread.is_alive()
    assert any(e['type']=='watch_alert' for e in events)


def test_stop_discards_an_inflight_scan(monkeypatch):
    entered=threading.Event();release=threading.Event();events=[]
    def assess(*a,**kw):entered.set();release.wait(1);return signal()
    monkeypatch.setattr(guard_service,'assess',assess)
    w=BackgroundWatch(lambda:(object(),{'index':'NIFTY'},False),lambda e,s:events.append(e))
    w.start();assert entered.wait(1);w.stop();release.set();w.thread.join(timeout=1)
    assert not events and not w.alerts


def test_data_error_clears_existing_alerts():
    w,_=watcher();w._accept(signal(),{'index':'NIFTY'})
    w._accept(entry_guard.blocked('DATA_ERROR','offline'),{'index':'NIFTY'})
    assert not w.alerts[0]['active']


def test_execute_rechecks_and_can_reject_prior_alert(tmp_path,monkeypatch):
    path,settings=settings_file(tmp_path);client=FakeClient(settings)
    monkeypatch.setattr(engine,'_run_analysis',lambda *a:'CALL')
    calls=[]
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw: calls.append(kw.get('force')) or entry_guard.blocked('PULLBACK','Changed after the alert'))
    result=engine.run(settings_path=str(path),kite_client=client,log=lambda *a:None,
                      alert_context={'id':'old-alert','direction':'CALL','active':False})
    assert result['direction']=='NO_TRADE' and not client.orders and calls==[True]
    rows=[json.loads(l) for p in tmp_path.glob('journals/*/*.jsonl') for l in p.read_text().splitlines()]
    assert any(e['type']=='user_execute_request' and e['alert_context']['id']=='old-alert' for e in rows)
    assert any(e['type']=='final_decision' and e['direction']=='NO_TRADE' for e in rows)


def test_watch_api_cannot_launch_engine(tmp_path,monkeypatch):
    import app
    path,settings=settings_file(tmp_path)
    monkeypatch.setattr(app,'SETTINGS_PATH',str(path));monkeypatch.setattr(app,'_WATCH_JOURNALS',{})
    w=BackgroundWatch(app._watch_context,app._save_watch_event,interval=.02)
    monkeypatch.setattr(app,'WATCHER',w)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:signal())
    monkeypatch.setattr(engine,'run',lambda *a,**kw:pytest.fail('watch must never run engine'))
    monkeypatch.setitem(app.STATE,'logged_in',False)
    web=app.app.test_client();assert web.post('/api/watch/start').status_code==400
    monkeypatch.setitem(app.STATE,'logged_in',True);monkeypatch.setitem(app.STATE,'running',False)
    client=FakeClient(settings);monkeypatch.setitem(app.STATE,'kite_client',client)
    try:
        assert web.post('/api/watch/start').json['enabled']
        assert web.get('/api/watch/status').json['enabled']
        assert web.post('/api/watch/stop').json['enabled'] is False
    finally:w.stop();w.thread.join(timeout=1)
    assert not client.orders


def test_watch_alerts_saved_in_day_end_bundle(tmp_path,monkeypatch):
    import app,day_review
    path,settings=settings_file(tmp_path)
    monkeypatch.setattr(app,'SETTINGS_PATH',str(path));monkeypatch.setattr(app,'_WATCH_JOURNALS',{})
    app._save_watch_event({'type':'watch_state','signal':signal()},settings)
    app._save_watch_event({'type':'watch_alert','id':'test-alert','category':'SETUP_READY'},settings)
    report=day_review.summarize(tmp_path/'journals')
    assert report['setup_alerts']==1 and report['guard_watch_updates']==1
    assert not report['simulation_groups']
