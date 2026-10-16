"""Evidence bundle for human/AI review. No automatic strategy changes."""
import csv
import io
import json
from collections import defaultdict
from pathlib import Path


def summarize(root, day=None):
    rows=[];watch_updates=0;alert_count=0
    for path in sorted(Path(root).glob(f'{day or "*"}/*.jsonl')):
        events=[];damaged=False
        for line in path.read_text(encoding='utf-8').splitlines():
            try:events.append(json.loads(line))
            except (ValueError,TypeError):damaged=True
        start=next((e for e in events if e.get('type')=='run_started'),None)
        if not start:continue
        if start.get('run_kind')=='market_watch':
            watch_updates+=sum(e.get('type')=='guard_watch' for e in events)
            alert_count+=sum(e.get('type')=='watch_alert' for e in events);continue
        settings=start.get('settings',{});entries=[e for e in events if e.get('type')=='entry']
        exits=[e for e in events if e.get('type')=='exit']
        decision=next((e for e in reversed(events) if e.get('type') in ('final_decision','decision')), {})
        candidate=next((e for e in reversed(events) if e.get('type')=='decision'), {})
        finished=any(e.get('type')=='run_finished' for e in events)
        failed=any(e.get('type')=='run_failed' for e in events)
        matched=bool(entries) and len(entries)==len(exits)
        if matched:
            entry_qty={e['position']['tradingsymbol']:e['position']['qty'] for e in entries}
            exit_qty={e['position']['tradingsymbol']:e.get('exit_quantity') for e in exits}
            matched=entry_qty==exit_qty and len(entry_qty)==len(entries)
        valid=matched and finished and not failed and not damaged and start.get('mode')=='DRY_RUN' and start.get('run_kind')!='recovery'
        if valid:status='COMPLETED_SIMULATION'
        elif damaged or failed:status='FAILED_OR_DAMAGED'
        elif not finished:status='UNFINISHED'
        elif not entries:status='NO_ENTRY'
        else:status='UNVERIFIED_OR_UNMATCHED'
        row={
            'run_id':start['run_id'],'started_at':start['recorded_at'],'index':settings.get('index'),
            'mode':start.get('mode'),'run_kind':start.get('run_kind'),'status':status,
            'candidate':candidate.get('direction'),'final_decision':decision.get('direction'),
            'decision_reason':decision.get('decision_reason',''),
            'trigger_alert_id':next((e.get('alert_context',{}).get('id') for e in events if e.get('type')=='user_execute_request'),None),
            'entry_count':len(entries),'exit_count':len(exits),
            'simulated_gross_pnl':sum(e.get('estimated_gross_pnl',0) for e in exits) if valid else None,
            'net_pnl':None,'source_revision':start.get('source_revision'),
            'settings_fingerprint':start.get('settings_fingerprint')}
        rows.append(row)
    groups={}
    for row in rows:
        if row['status']!='COMPLETED_SIMULATION':continue
        key=(row['index'],row['run_kind'],row['source_revision'],row['settings_fingerprint'])
        group=groups.setdefault(key,{'index':key[0],'run_kind':key[1],'source_revision':key[2],'settings_fingerprint':key[3],
                                    'trades':0,'wins':0,'losses':0,'flat':0,'gross_pnl':0})
        pnl=row['simulated_gross_pnl'];group['trades']+=1;group['gross_pnl']+=pnl
        group['wins' if pnl>0 else 'losses' if pnl<0 else 'flat']+=1
    for group in groups.values():
        group['win_rate']=group['wins']/group['trades']
        group['average_gross_pnl']=group['gross_pnl']/group['trades']
    return {'scope':day or 'all saved days','runs':rows,'simulation_groups':list(groups.values()),'guard_watch_updates':watch_updates,'setup_alerts':alert_count,
            'limitations':['Only completed, matched dry-run entries/exits contribute to performance.',
                           'Live, recovery, unfinished, failed and unmatched runs are excluded from performance.',
                           'P&L uses indicative LTPs; fees/taxes/spread/slippage excluded; net P&L is unknown.',
                           'A small sample cannot establish accuracy; separate index/style/version/settings and evaluate on unseen sessions.',
                           'Guard warnings and blocked entries are evidence, not proof that avoided trades would have lost.']}


def render_markdown(report):
    lines=['# Day-end review', '',f'Scope: {report["scope"]}', '',f'Runs: {len(report["runs"])} | Saved market-state updates: {report["guard_watch_updates"]}', '',
           '| Index | Style | Code version | Settings | Completed trades | Wins | Losses | Gross P&L |',
           '|---|---|---|---|---:|---:|---:|---:|']
    for g in report['simulation_groups']:
        lines.append(f'| {g["index"]} | {g["run_kind"]} | {g["source_revision"]} | {g["settings_fingerprint"]} | {g["trades"]} | {g["wins"]} | {g["losses"]} | {g["gross_pnl"]:.2f} |')
    lines+=['','## Limits','']+['- '+x for x in report['limitations']]
    lines+=['','## Review request to use with this bundle','',
            'Read the raw events and rulebooks before recommending changes. Separate data/execution errors from entry/exit logic. Review the largest losses, early exits, adverse/favorable excursion and strict-gate blocks. Check whether indicator inputs were fresh. Do not infer win probability from the heuristic score. Propose one testable change at a time; specify evidence, downside, validation on subsequent unseen sessions, and rollback conditions. Do not silently update or deploy live trading code.','',
            '## Next-session experiment','',
            '- Observation and affected rule:', '- Proposed change and rationale:', '- Version/settings baseline:',
            '- New-session validation and cost assumptions:', '- Accept/reject criterion:', '- Rollback condition:']
    return '\n'.join(lines)+'\n'


def render_csv(report):
    output=io.StringIO()
    if report['runs']:
        w=csv.DictWriter(output,fieldnames=list(report['runs'][0]));w.writeheader();w.writerows(report['runs'])
    return output.getvalue()
