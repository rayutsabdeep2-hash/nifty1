import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import entry_guard as guard
import guard_service
import day_review
import trade_journal
import engine
from test_journal_routing import FakeClient, settings_file

NOW=dt.datetime(2026,10,16,12,16,tzinfo=guard.IST)

def candles(direction=1):
    start=NOW.replace(hour=9,minute=15,second=0)
    bars=[]
    for i in range(60):
        close=100+direction*.1*i
        bars.append({'date':start+dt.timedelta(minutes=3*i),
                     'open':close-direction*.02,'close':close,
                     'high':close+(.01 if direction==1 else 1.99),
                     'low':close-(1.99 if direction==1 else .01)})
    return bars

@pytest.mark.parametrize('direction,expected',[(1,'CALL'),(-1,'PUT')])
def test_confirmed_trend_allows_only_matching_side(direction,expected):
    bars=candles(direction);result=guard.evaluate(bars,bars[-1]['close'],NOW)
    assert result['allowed_direction']==expected
    assert guard.permits(result,expected)
    assert not guard.permits(result,'BOTH')
    assert not guard.permits(result,'PUT' if expected=='CALL' else 'CALL')

@pytest.mark.parametrize('state',['PULLBACK','REVERSAL_RISK','SIDEWAYS','EXTENDED','WAIT_CONFIRMATION','PRICE_DRIFT','STALE_DATA','MISSING_BARS','INVALID_DATA'])
def test_strict_veto_states(state):
    bars=candles();spot=bars[-1]['close'];now=NOW
    if state=='PULLBACK':bars[-1]['close']=bars[-2]['close']-.05;spot=bars[-1]['close']
    if state=='REVERSAL_RISK':bars[-1].update(close=104,low=103);spot=104
    if state=='SIDEWAYS':
        for b in bars:b.update(open=100,high=101,low=99,close=100)
        spot=100
    if state=='EXTENDED':spot+=3
    if state=='WAIT_CONFIRMATION':bars[-2]['high']+=1
    if state=='PRICE_DRIFT':spot+=1.1
    if state=='STALE_DATA':now+=dt.timedelta(minutes=5)
    if state=='MISSING_BARS':bars.pop(-2)
    if state=='INVALID_DATA':bars[-1]['high']=float('nan')
    result=guard.evaluate(bars,spot,now)
    assert result['status']==state
    assert result['allowed_direction'] is None


def test_forming_candle_is_ignored_and_input_unchanged():
    bars=candles();before=list(bars)
    bars.append({'date':NOW.replace(minute=15),'open':1,'high':999,'low':1,'close':999})
    result=guard.evaluate(bars,before[-1]['close'],NOW)
    assert result['status']=='READY_CALL'
    assert len(result['candles'])==60 and len(bars)==61

@pytest.mark.parametrize('hour,minute',[(9,20),(15,15),(18,0)])
def test_outside_window_fails_closed(hour,minute):
    assert guard.evaluate([],None,NOW.replace(hour=hour,minute=minute))['status']=='OUTSIDE_ENTRY_WINDOW'


def test_insufficient_history_and_missing_quote_fail_closed():
    assert guard.evaluate(candles()[-10:],105.9,NOW)['status']=='INSUFFICIENT_DATA'
    assert guard.evaluate(candles(),None,NOW)['status']=='INVALID_DATA'
    assert guard.evaluate(candles(),105.9,NOW,cutoff='12:00')['status']=='OUTSIDE_ENTRY_WINDOW'


def test_broker_failure_never_reuses_ready_for_fresh_entry(monkeypatch):
    monkeypatch.setattr(guard,'clock',lambda:NOW)
    class Client:
        broken=False
        def get_index_instrument(self,*a):return {'instrument_token':1}
        def get_intraday_history(self,*a,**kw):
            if self.broken:raise RuntimeError('offline')
            return candles()
        def get_quote(self,keys):return {keys[0]:{'last_price':105.9}}
    client=Client();settings={'index':'NIFTY'}
    assert guard_service.assess(client,settings,force=True)['status']=='READY_CALL'
    client.broken=True
    assert guard_service.assess(client,settings,force=True)['status']=='DATA_ERROR'

@pytest.mark.parametrize('kind',['automatic','manual'])
@pytest.mark.parametrize('status',['PULLBACK','REVERSAL_RISK','DATA_ERROR'])
def test_gate_blocks_actual_entry_paths_and_logs(tmp_path,monkeypatch,kind,status):
    path,settings=settings_file(tmp_path);client=FakeClient(settings)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:guard.blocked(status,'test veto',rules=guard.RULES))
    monkeypatch.setattr(engine,'_run_analysis',lambda *a:'CALL')
    if kind=='manual':result=engine.manual_run('CALL',settings_path=str(path),kite_client=client,log=lambda *a:None)
    else:result=engine.run(settings_path=str(path),kite_client=client,log=lambda *a:None)
    assert result['direction']=='NO_TRADE' and not client.orders
    rows=[json.loads(l) for p in tmp_path.glob('journals/*/*.jsonl') for l in p.read_text().splitlines()]
    assert any(e['type']=='entry_gate' and not e['approved'] and e['signal']['status']==status for e in rows)
    assert next(e for e in rows if e['type']=='run_started')['rulebook']['strict_gate']['version']==guard.VERSION
    assert any(e['type']=='final_decision' and e['direction']=='NO_TRADE' for e in rows)


def test_both_and_opposing_candidate_blocked_even_if_ready(tmp_path,monkeypatch):
    path,settings=settings_file(tmp_path);client=FakeClient(settings)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:{'status':'READY_CALL','allowed_direction':'CALL','reason':'ready'})
    for direction in ('BOTH','PUT'):
        result=engine.manual_run(direction,settings_path=str(path),kite_client=client,log=lambda *a:None)
        assert result['direction']=='NO_TRADE'
    assert not client.orders


def test_allowed_side_can_enter(tmp_path,monkeypatch):
    path,settings=settings_file(tmp_path);client=FakeClient(settings)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:guard.evaluate(candles(),105.9,NOW))
    result=engine.manual_run('CALL',settings_path=str(path),kite_client=client,log=lambda *a:None)
    assert result['direction']=='CALL'
    assert [o[2] for o in client.orders]==['BUY','SELL']


def test_failed_journal_blocks_entry(tmp_path,monkeypatch):
    _,settings=settings_file(tmp_path);client=FakeClient(settings)
    j=trade_journal.Journal(tmp_path/'journals',settings,'manual');j.failed=True
    token=trade_journal.CURRENT.set(j)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:guard.evaluate(candles(),105.9,NOW))
    try:assert not engine._entry_check(client,settings,'CALL')
    finally:trade_journal.CURRENT.reset(token)


def test_review_excludes_live_unfinished_and_failed_runs(tmp_path):
    _,settings=settings_file(tmp_path);root=tmp_path/'journals'
    for kind in ('completed','unfinished','failed','live','watch'):
        cfg={**settings,'dry_run':kind!='live'}
        j=trade_journal.Journal(root,cfg,'market_watch' if kind=='watch' else 'automatic')
        if kind=='watch':j.write({'type':'guard_watch','signal':{'status':'PULLBACK'}});continue
        j.write({'type':'entry','position':{'tradingsymbol':'TEST','qty':10}})
        j.write({'type':'exit','position':{'tradingsymbol':'TEST','qty':10},'exit_quantity':10,'estimated_gross_pnl':100})
        if kind=='failed':j.write({'type':'run_failed'})
        elif kind!='unfinished':j.write({'type':'run_finished'})
    report=day_review.summarize(root)
    assert len(report['runs'])==4 and report['guard_watch_updates']==1
    assert report['simulation_groups'][0]['trades']==1
    assert report['simulation_groups'][0]['gross_pnl']==100
    assert all(r['net_pnl'] is None for r in report['runs'])
    assert 'unseen' in day_review.render_markdown(report)
    assert 'simulated_gross_pnl' in day_review.render_csv(report)


def test_day_filter_only_reads_chosen_date(tmp_path):
    assert day_review.summarize(tmp_path,'20000101')['runs']==[]

@pytest.mark.parametrize('index',['NIFTY','BANKNIFTY','SENSEX'])
def test_guard_ui_status_and_watch_export(tmp_path,monkeypatch,index):
    import app
    path,settings=settings_file(tmp_path,index)
    monkeypatch.setattr(app,'SETTINGS_PATH',str(path))
    monkeypatch.setattr(app,'_WATCH_JOURNALS',{})
    monkeypatch.setitem(app.STATE,'logged_in',True)
    monkeypatch.setitem(app.STATE,'kite_client',FakeClient(settings))
    signal={**guard.blocked('PULLBACK','Wait for confirmation',signal_candle='test'), 'index':index}
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:signal)
    web=app.app.test_client()
    assert web.get('/api/entry_guard').json['status']=='PULLBACK'
    assert web.get('/api/entry_guard').json['index']==index
    rows=[json.loads(l) for p in tmp_path.glob('journals/*/*.jsonl') for l in p.read_text().splitlines()]
    assert sum(e['type']=='guard_watch' for e in rows)==1
    assert web.get('/journal/export?day=../../settings').status_code==400
    import zipfile,io
    archive=zipfile.ZipFile(io.BytesIO(web.get('/journal/export').data))
    assert 'DAY_END_REVIEW.md' in archive.namelist()
    report=json.loads(archive.read('day_end_summary.json'))
    assert report['guard_watch_updates']==1 and not report['simulation_groups']


def test_new_journal_failure_during_gate_also_blocks(tmp_path,monkeypatch):
    _,settings=settings_file(tmp_path);j=trade_journal.Journal(tmp_path/'journals',settings,'manual')
    def fail(event):raise OSError('disk full')
    monkeypatch.setattr(j,'write',fail)
    monkeypatch.setattr(guard_service,'assess',lambda *a,**kw:guard.evaluate(candles(),105.9,NOW))
    token=trade_journal.CURRENT.set(j)
    try:assert not engine._entry_check(FakeClient(settings),settings,'CALL')
    finally:trade_journal.CURRENT.reset(token)
