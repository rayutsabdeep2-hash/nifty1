"""Offline regression tests: no credentials, broker requests or real orders."""
import datetime as dt
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import zipfile

import pytest
import ai_sentiment
import decision
import engine
from kite_client import KiteClient
from markets import market
import trade_journal


@pytest.fixture(autouse=True)
def isolate_existing_execution_tests(monkeypatch):
    monkeypatch.setattr(engine, '_entry_check', lambda *args, **kwargs: True)


def settings_file(tmp_path, index='NIFTY'):
    settings = {
        'index':index,'dry_run':True,'lots':1,'kite_api_key':'DO-NOT-SAVE-KEY',
        'kite_api_secret':'DO-NOT-SAVE-SECRET','gemini_api_key':'DO-NOT-SAVE-GEMINI',
        'proxy':{},'sl_enabled':True,'max_loss':500,'target_enabled':True,'target_profit':500,
        'time_exit_enabled':False,'time_exit':'15:00','force_exit_time':'00:00',
        'pnl_mode':'COMBINED','pcr_threshold':.15,'range_lookback_days':5,'gap_threshold_factor':.5,
        'ai_provider':'gemini','gemini_model':'test','custom_ai_note':'DO-NOT-SAVE-NOTE'
    }
    path=tmp_path/'settings.json';path.write_text(json.dumps(settings));return path,settings

class FakeClient:
    def __init__(self, settings):
        self.settings=settings;self.routing=market(settings['index']);self.orders=[];self.closed=False
        self.fail_second=False;self.requests=[];self.started=False
        self.kite=SimpleNamespace(TRANSACTION_TYPE_BUY='BUY',TRANSACTION_TYPE_SELL='SELL')
        expiry=dt.date.today()+dt.timedelta(days=3)
        self.chain=[{'name':settings['index'],'instrument_type':t,'instrument_token':100+i*2+j,
                     'strike':s,'lot_size':10,'expiry':expiry,'tradingsymbol':f'{settings["index"]}{s}{t}',
                     'exchange':self.routing['derivative_exchange']}
                    for i,s in enumerate(range(9500,10501,100)) for j,t in enumerate(['CE','PE'])]
        self.fut={'name':settings['index'],'instrument_type':'FUT','instrument_token':2,
                  'expiry':expiry,'tradingsymbol':'TESTFUT','exchange':self.routing['derivative_exchange']}
    def get_index_instrument(self,symbol,exchange):
        assert (symbol,exchange)==(self.routing['spot'],self.routing['spot_exchange'])
        return {'instrument_token':1,'tradingsymbol':symbol}
    def get_nfo_option_chain(self,index):return self.chain[0]['expiry'],self.chain
    def get_nfo_futures_instrument(self,index):return self.fut
    def get_quote(self,keys):
        self.requests.extend(keys);out={}
        for key in keys:
            if key=='NSE:INDIA VIX':out[key]={'last_price':20};continue
            exch,symbol=key.split(':',1)
            assert exch==(self.routing['spot_exchange'] if symbol==self.routing['spot'] else self.routing['derivative_exchange'])
            out[key]={'last_price':10000 if symbol==self.routing['spot'] else 100,
                      'average_price':9950,'oi':200 if symbol.endswith('PE') else 100,
                      'ohlc':{'open':9900,'close':9800}}
        return out
    def start_ticker(self,*a,**kw):self.started=True
    def wait_for_ticker(self,**kw):return True
    def get_tick(self,token):
        if token==1:return {'last_price':10000,'instrument_token':1}
        if token==2:return {'last_price':10025,'average_price':9950,'instrument_token':2}
        return {'last_price':110 if self.orders else 100,'oi':400 if token==109 else (200 if token%2 else 100),
                'instrument_token':token,'volume_traded':500,'depth':{'buy':[{'price':99}],'sell':[{'price':101}]}}
    def get_all_ticks(self):return {i['instrument_token']:self.get_tick(i['instrument_token']) for i in self.chain}
    def get_daily_history(self,*a,**kw):
        return [{'date':dt.datetime.now()-dt.timedelta(days=i),'high':10300,'low':9700,
                 'open':9800,'close':10000,'oi':100} for i in range(20,0,-1)]
    def get_intraday_history(self,*a,**kw):
        return [{'date':dt.datetime.now().replace(hour=9,minute=15)+dt.timedelta(minutes=i),
                 'open':9900,'high':9950,'low':9850,'close':9920,'volume':100} for i in range(15)]
    def place_market_order(self,symbol,exchange,side,qty):
        assert exchange==self.routing['derivative_exchange']
        if self.fail_second and side=='BUY' and symbol.endswith('PE'):raise RuntimeError('second leg rejected')
        self.orders.append((symbol,exchange,side,qty));return {'dry_run':True}
    def get_actual_position_qty(self,symbol,exchange,product,expected_qty):
        assert exchange==self.routing['derivative_exchange'];return expected_qty
    def seconds_since_last_tick(self):return 0
    def stop_ticker(self):self.closed=True


def events(tmp_path):
    return [json.loads(line) for p in tmp_path.glob('journals/*/*.jsonl') for line in p.read_text().splitlines()]

@pytest.mark.parametrize('index',['NIFTY','BANKNIFTY','SENSEX'])
def test_complete_automatic_dry_run_with_decision_and_journal(tmp_path,monkeypatch,index):
    path,settings=settings_file(tmp_path,index);client=FakeClient(settings)
    monkeypatch.setattr(ai_sentiment,'get_ai_sentiment',lambda *a,**kw:{'sentiment':'POSITIVE','reason':'test news'})
    result=engine.run(settings_path=str(path),kite_client=client,log=lambda *a:None)
    rows=events(tmp_path)
    assert result['direction']=='CALL'
    assert [o[2] for o in client.orders]==['BUY','SELL']
    assert client.closed
    de=next(e for e in rows if e['type']=='decision')
    assert de['decision_rule']=='CALL_THRESHOLD' and de['index']==index
    en=next(e for e in rows if e['type']=='entry')['position']
    ex=next(e for e in rows if e['type']=='exit')
    assert en['entry_price']==100 and ex['exit_price']==110
    assert ex['estimated_gross_pnl']==100 and ex['reason']=='FORCE_EXIT_TIME'
    assert ex['position']['max_favorable_pnl']==100
    assert ex['underlying_ltp']==10000
    assert all(e['recorded_at'].endswith('+05:30') for e in rows)
    all_text=''.join(p.read_text() for p in tmp_path.glob('journals/*/*'))
    assert 'DO-NOT-SAVE' not in all_text
    assert rows[-1]['type']=='run_finished'

@pytest.mark.parametrize('index',['NIFTY','BANKNIFTY','SENSEX'])
def test_rest_fallbacks_and_instrument_exchange(tmp_path,monkeypatch,index):
    _,settings=settings_file(tmp_path,index);client=FakeClient(settings)
    universe=engine.build_option_universe(client,index,lambda *a:None)
    client.get_tick=lambda t:None
    client.get_all_ticks=lambda :{}
    monkeypatch.setattr(engine, "_wait_for_ticks", lambda *a,**kw:False)
    data,_=engine.gather_strike_oi(client,universe,lambda *a:None)
    assert len(data)==11
    assert engine.get_atm_premiums(client,universe,lambda *a:None)['ce_premium']==100
    assert engine.get_vwap(client,universe,lambda *a:None)==9950
    # Verify actual KiteClient instrument routing against a fake SDK.
    seen=[]
    sdk=SimpleNamespace(instruments=lambda exchange:seen.append(exchange) or client.chain+[client.fut])
    real=KiteClient.__new__(KiteClient);real.kite=sdk
    assert real.get_nfo_option_chain(index)[1]==client.chain
    assert real.get_nfo_futures_instrument(index)==client.fut
    assert seen==[market(index)['derivative_exchange']]*2


def test_stop_prevents_entry(tmp_path):
    path,settings=settings_file(tmp_path);client=FakeClient(settings);stop=threading.Event();stop.set()
    engine.manual_run('BOTH',settings_path=str(path),kite_client=client,stop_event=stop,log=lambda *a:None)
    assert not client.orders
    assert any(e['type']=='entry_cancelled' for e in events(tmp_path))


def test_second_leg_error_retains_first_for_cleanup(tmp_path):
    path,settings=settings_file(tmp_path,'SENSEX');client=FakeClient(settings);client.fail_second=True
    with pytest.raises(RuntimeError,match='second leg'):
        engine.manual_run('BOTH',settings_path=str(path),kite_client=client,log=lambda *a:None)
    assert [o[2] for o in client.orders]==['BUY','SELL']
    rows=events(tmp_path)
    assert any(e['type']=='exit' and e['reason']=='SAFETY_NET_EXIT' for e in rows)
    assert rows[-1]['type']=='run_failed'


def test_analysis_only_and_no_trade_logged(tmp_path,monkeypatch):
    path,settings=settings_file(tmp_path);client=FakeClient(settings)
    monkeypatch.setattr(ai_sentiment,'get_ai_sentiment',lambda *a,**kw:{'sentiment':'NEUTRAL','reason':'none'})
    engine.analyze_only(settings_path=str(path),kite_client=client,log=lambda *a:None)
    assert not client.orders
    assert any(e['type']=='analysis_complete' for e in events(tmp_path))
    def no_trade(*args):
        engine._emit(args[-1],{'type':'decision','direction':'NO_TRADE','decision_reason':'test'})
        return 'NO_TRADE'
    monkeypatch.setattr(engine,'_run_analysis',no_trade)
    engine.run(settings_path=str(path),kite_client=client,log=lambda *a:None)
    assert any(e['type']=='no_trade' for e in events(tmp_path))


def test_journal_redacts_secrets_and_reviews_preserve_data(tmp_path):
    _,settings=settings_file(tmp_path)
    j=trade_journal.Journal(tmp_path/'journals',settings,'test')
    j.write({'type':'error','message':'DO-NOT-SAVE-KEY failed','access_token':'hidden'})
    assert 'DO-NOT-SAVE' not in j.events_path.read_text()
    name=j.note_path.relative_to(tmp_path/'journals').as_posix()
    before=j.events_path.read_bytes()
    trade_journal.append_review(tmp_path/'journals',name,'Investigate late entry. Do not tune on this trade alone.')
    assert j.events_path.read_bytes()==before
    assert 'Investigate late entry' in j.note_path.read_text()
    with pytest.raises(ValueError):trade_journal.resolve_note(tmp_path/'journals','../../settings.json')


def test_web_selector_export_and_index_change_guard(tmp_path,monkeypatch):
    import app
    path,settings=settings_file(tmp_path)
    monkeypatch.setattr(app,'SETTINGS_PATH',str(path))
    monkeypatch.setattr(app,'LAST_ANALYSIS_PATH',str(tmp_path/'last_analysis_state.json'))
    monkeypatch.setitem(app.STATE,'running',False)
    client=app.app.test_client()
    assert b'value="SENSEX"' in client.get('/').data
    assert client.post('/api/settings',json={'index':'SENSEX'}).json['index_changed']
    monkeypatch.setitem(app.STATE,'running',True)
    assert client.post('/api/settings',json={'index':'BANKNIFTY'}).status_code==409
    j=trade_journal.Journal(tmp_path/'journals',settings,'test')
    name=j.note_path.relative_to(tmp_path/'journals').as_posix()
    assert client.get('/journal').status_code==200
    assert client.post('/journal/review',data={'name':name,'note':'Test review'}).status_code==302
    assert b'Test review' in client.get('/journal/file/'+name).data
    archive=zipfile.ZipFile(io.BytesIO(client.get('/journal/export').data))
    assert len(archive.namelist())==5
    assert 'DAY_END_REVIEW.md' in archive.namelist()


def test_ai_prompt_uses_selected_index():
    prompt=ai_sentiment._fixed_prompt({'index':'SENSEX'})
    assert 'INTRADAY SENSEX' in prompt and 'SENSEX spot' in prompt


def test_large_gap_explanation_discloses_override():
    result=decision.evaluate_decision(.5,'NEUTRAL',True)
    assert result['direction']=='BOTH' and result['rule']=='LARGE_GAP_OVERRIDE'
    assert 'bypasses' in result['reason']

@pytest.mark.parametrize('reason,price,per_leg',[('TARGET',110,False),('STOP_LOSS',90,False),('TARGET',110,True),('MANUAL_STOP',110,False)])
def test_journal_exit_reasons(tmp_path,monkeypatch,reason,price,per_leg):
    path,settings=settings_file(tmp_path)
    settings.update(force_exit_time='23:59',target_profit=50,max_loss=50,pnl_mode='PER_LEG' if per_leg else 'COMBINED')
    path.write_text(json.dumps(settings));client=FakeClient(settings)
    old_tick=client.get_tick
    def tick(token):
        data=old_tick(token)
        if token>=100:data['last_price']=price if client.orders else 100
        return data
    client.get_tick=tick
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,15,10,0)
    monkeypatch.setattr(engine,'datetime',SimpleNamespace(datetime=Clock,date=dt.date,time=dt.time,timedelta=dt.timedelta))
    if reason=='MANUAL_STOP':
        original=engine.monitor_and_exit
        def stop_monitor(k,p,s,event,log,**kw):
            event.set();return original(k,p,s,event,log,**kw)
        monkeypatch.setattr(engine,'monitor_and_exit',stop_monitor)
    engine.manual_run('CALL',settings_path=str(path),kite_client=client,log=lambda *a:None)
    exit_row=next(e for e in events(tmp_path) if e['type']=='exit')
    assert exit_row['reason']==reason and exit_row['exit_price']==price
    assert exit_row['estimated_gross_pnl']==(price-100)*10
