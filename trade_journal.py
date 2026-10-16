"""Append-only, IST-timestamped audit notes and machine-readable events.

No broker calls. No strategy self-modification. A run's source/settings fingerprints
make subsequent comparisons reproducible. Price samples are indicative, not fills.
"""
import entry_guard

import contextvars
import datetime as dt
import functools
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
CURRENT = contextvars.ContextVar('trade_journal', default=None)
WRITE_LOCK = threading.Lock()
SAFE_SETTINGS = set('index lots dry_run sl_enabled max_loss target_enabled target_profit pnl_mode time_exit_enabled time_exit force_exit_time pcr_threshold range_lookback_days gap_threshold_factor min_expected_move_points call_probability_threshold put_probability_threshold profit_margin_factor momentum_min_fraction momentum_max_fraction oi_change_clamp volatility_confidence_ratio_scale volatility_confidence_orb_bonus volatility_confidence_momentum_scale volatility_confidence_oi_buildup_scale volatility_confidence_cpr_narrow_scale historical_range_lookback_days atm_oi_change_threshold orb_minutes ai_provider gemini_model claude_model'.split())

def now():
    return dt.datetime.now(IST).isoformat(timespec='milliseconds')

def journal_root(settings_path):
    return Path(settings_path).resolve().parent / 'journals'

def source_revision():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for p in sorted(root.glob('*.py')):
        if not p.name.startswith('test_'):
            digest.update(p.name.encode()); digest.update(p.read_bytes())
    return digest.hexdigest()[:16]

def _clean(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): _clean(v, secrets) for k,v in value.items()
                if not any(t in str(k).lower() for t in ('api_key','secret','access_token','password','custom_ai_note','proxy'))}
    if isinstance(value, (list, tuple)):
        return [_clean(v, secrets) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r"(?i)((?:access_token|api_key|api_secret|request_token)[=:\s]+)[^&\s,'\"}]+", r"\1[REDACTED]", value)
        return value
    if value is None or isinstance(value, (bool,int,float)):
        return value
    return _clean(str(value), secrets)

class Journal:
    def __init__(self, root, settings, kind):
        self.run_id = dt.datetime.now(IST).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10]
        self.directory = Path(root) / self.run_id[:8]
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / (self.run_id + '.jsonl')
        self.note_path = self.directory / (self.run_id + '.md')
        self.secrets = [str(v) for k,v in settings.items() if v and any(t in k for t in ('api_key','secret','access_token'))]
        self.secrets += [str(v) for k,v in settings.get('proxy', {}).items() if k in ('user','pass') and v]
        self.failed = False
        self.last_sample = 0
        self.universe = {}
        self.mode = 'DRY_RUN' if settings.get('dry_run', True) else 'LIVE_UNVERIFIED'
        self.safe_settings = {k: settings[k] for k in SAFE_SETTINGS if k in settings}
        self.safe_settings['ai_note_hash'] = hashlib.sha256(settings.get('custom_ai_note','').encode()).hexdigest()[:16]
        self.note_path.write_text(f'# Trade journal: {self.run_id}\n\nIndex: {settings["index"]}\n\nMode: {self.mode} | Run: {kind}\n\nAll timestamps use Asia/Kolkata. Dry-run prices are indicative LTPs, not broker fills. Gross P&L excludes fees, taxes, spread and slippage. Live submissions are unverified until broker execution is independently reconciled. An unfinished run is not proof that positions are closed.\n', encoding='utf-8')
        self.write({'type':'run_started','run_kind':kind,'mode':self.mode,'settings':self.safe_settings,
                    'settings_fingerprint': hashlib.sha256(json.dumps(self.safe_settings,sort_keys=True).encode()).hexdigest()[:16],
                     'source_revision':source_revision(),
                    'rulebook': {
                        'score_policy':'OI wall base + seven fixed 0.05 directional nudges; clip to [0.05,0.95]. Heuristic, not probability.',
                        'score_inputs':['PCR','VWAP','ORB','gap','ATM OI','AI','momentum'],
                        'candidate_policy':'Tight range veto; large-gap BOTH override; CALL/PUT thresholds; AI conflict switches candidate to BOTH; premium movement gates.',
                        'final_policy':'Strict gate runs after the candidate, immediately before order preparation. NO_TRADE overrides all candidates including manual trades and BOTH.',
                        'exit_policy':'Manual stop, force/time exit, enabled combined/per-leg loss/target. Guard is warning-only for existing positions.',
                        'strict_gate':entry_guard.RULES}})

    def write(self, event):
        event = _clean(event, self.secrets)
        if event.get('type') == 'universe_ready': self.universe = event
        if event.get('type') == 'pnl_update':
            if time.monotonic() - self.last_sample < 5: return
            self.last_sample = time.monotonic()
        row = {'recorded_at':now(),'run_id':self.run_id,'mode':self.mode,**event}
        data = json.dumps(row, ensure_ascii=False, allow_nan=False)
        with WRITE_LOCK:
            with self.events_path.open('a', encoding='utf-8') as f:
                f.write(data+'\n'); f.flush(); os.fsync(f.fileno())
            if event.get('type') not in ('pnl_update','progress','log'):
                with self.note_path.open('a', encoding='utf-8') as f:
                    f.write(f'\n## {row["recorded_at"]} — {event.get("type", "event")}\n\n')
                    if event.get('decision_reason'): f.write(event['decision_reason']+'\n\n')
                    f.write('```json\n'+json.dumps(event,indent=2,ensure_ascii=False)+'\n```\n')
                    f.flush(); os.fsync(f.fileno())

def record(event):
    journal = CURRENT.get()
    if journal is None: return
    try:
        journal.write(event)
    except OSError as exc:
        journal.failed = True
        # Never prevent an exit because a disk filled after entry.
        print(f'[JOURNAL WRITE FAILED] {type(exc).__name__}; audit trail may be incomplete', file=sys.stderr)

def spot_snapshot(client):
    j = CURRENT.get()
    token = j.universe.get('spot_token') if j else None
    tick = client.get_tick(token) if token is not None else None
    return {'underlying_ltp': tick.get('last_price') if tick else None,
            'underlying_price_source':'last_received_tick' if tick else 'unavailable',
            'underlying_tick_time':tick.get('exchange_timestamp') if tick else None}

def journaled(kind):
    def decorate(fn):
        signature = inspect.signature(fn)
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs); bound.apply_defaults()
            settings_path = bound.arguments['settings_path']
            settings = json.loads(Path(settings_path).read_text())
            # Failure to create initial audit files prevents this run from starting.
            j = Journal(journal_root(settings_path), settings, kind)
            token = CURRENT.set(j)
            if bound.arguments.get('alert_context'):
                record({'type':'user_execute_request','alert_context':bound.arguments['alert_context'],
                        'meaning':'Alert attribution only; fresh analysis and strict gate are required.'})
            original_log = bound.arguments.get('log', print)
            def logged(message):
                record({'type':'log','message':str(message)})
                original_log(message)
            bound.arguments['log'] = logged
            try:
                result = fn(*bound.args, **bound.kwargs)
                record({'type':'run_finished','result':result,
                        'execution_verification':'simulated' if j.mode=='DRY_RUN' else 'NOT_VERIFIED_WITH_BROKER'})
                return result
            except BaseException as exc:
                record({'type':'run_failed','error_type':type(exc).__name__,
                        'message':str(exc),'position_status':'UNKNOWN — verify broker positions in live mode'})
                raise
            finally:
                CURRENT.reset(token)
        return wrapped
    return decorate

FILE_PATTERN = re.compile(r'^\d{8}/\d{8}T\d{6}-[a-f0-9]{10}\.(md|jsonl)$')
def resolve_note(root, name):
    if not FILE_PATTERN.fullmatch(name): raise ValueError('Invalid journal filename')
    root = Path(root).resolve(); path = (root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise ValueError('Journal not found')
    return path

def append_review(root, name, note):
    path = resolve_note(root, name)
    if path.suffix != '.md': raise ValueError('Choose a readable note')
    note = note.strip()
    if not note or len(note)>10000: raise ValueError('Review must be 1–10000 characters')
    with WRITE_LOCK:
        with path.open('a',encoding='utf-8') as f:
            f.write(f'\n## Reviewer note — {now()}\n\n{note}\n'); f.flush(); os.fsync(f.fileno())
    return path
