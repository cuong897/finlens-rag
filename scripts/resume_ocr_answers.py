"""Continue only unexecuted OCR-regression cases after a provider outage.

The first seven responses, including errors, remain in the aggregate. No
question is repeated and no successful answer replaces an unsuccessful one.
"""
from dataclasses import replace
from datetime import datetime, timezone
import json
import time

import httpx

from finlens.engine import Engine, valid_citations
from finlens.environment import load_env_file
from finlens.models import AskRequest
from finlens.providers import ModelConfig, ProviderError
from finlens.store import Store
from scripts.evaluate_ocr_completeness import ROOT, check, sha
from scripts.evaluate_policy_repair import read
from scripts.run_fresh_gemini_eval import summarize


def main():
    protocol=check();original=ROOT/'answers/answer-check.json'
    before=json.loads(original.read_text(encoding='utf-8'))
    original_sha=sha(original)
    if not before.get('stop'):raise ValueError('Only resume an interrupted pass')
    previous=[r for r in before['results'] if r['execution']=='completed']
    ids={r['id'] for r in previous}
    dataset=read('holdout_answers_20261101.json')
    pending=[c for c in dataset['cases'] if c['id'] not in ids]
    if not pending:raise ValueError('No unexecuted cases remain')
    out=ROOT/'answers-combined';out.mkdir(exist_ok=False)
    plan={'date':'2026-11-09','previous_result_sha256':original_sha,'previous_completed_ids':sorted(ids),
        'pending_ids':[c['id'] for c in pending],'max_total_posts':120,'pause_at_least_seconds':120,
        'note':'Second availability window, each question executed at most once. Earlier failures are retained.'}
    (out/'resume-protocol.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(previous[-1]['recorded_at'])).total_seconds()
    remaining=max(0,120-elapsed)
    if remaining:print(f'Waiting for prior cooldown: {remaining:.1f}s',flush=True)
    while remaining>0:
        delay=min(5,remaining);time.sleep(delay);remaining-=delay
    load_env_file('.env');config=replace(ModelConfig.from_env(),embed_model='bge-m3:latest')
    rows=list(previous);consecutive=0;stop=None
    with httpx.Client(trust_env=False) as client:
        class Cap:
            used=before['transport_generation_requests']
            def post(self,*args,**kwargs):
                if self.used>=120:raise ProviderError('Regression budget exhausted',code='evaluation_budget')
                self.used+=1;return client.post(*args,**kwargs)
        engine=Engine(Store(ROOT/'data'),config);engine.provider.client=Cap()
        for index,c in enumerate(pending):
            if index:time.sleep(6)
            check()
            a=engine.answer(AskRequest(company=c['company'],years=c['years'],scope=c['scope'],question=c['question'],dataset='real'))
            passed=a['status']==c['expected_status'] and not a.get('generation_error') and bool(a.get('generation_model'))
            if a['mode']=='generated':passed=passed and valid_citations(a['answer'],a['citations'])
            row={'id':c['id'],'wave':1,'availability_window':2,'recorded_at':datetime.now(timezone.utc).isoformat(),
                'question':c['question'],'reference':c['reference'],'execution':'completed','contract_passed':passed,'response':a}
            rows.append(row)
            with (out/'requests.jsonl').open('a',encoding='utf-8') as handle:
                handle.write(json.dumps(row,ensure_ascii=False)+'\n')
            error=a.get('generation_error');consecutive=consecutive+1 if error else 0
            print(f"{len(rows)}/40 {c['id']} {a['status']} {a.get('generation_model')} {error}",flush=True)
            if error and (consecutive>=3 or error['code'] in {'access_denied','missing_key','invalid_request','rate_limited','evaluation_budget'}):
                stop={'after':len(rows),'reason':error['code']};break
        completed={r['id']:r for r in rows}
        result={**before,'product_commit':protocol['frozen_product_commit'],'availability_windows':2,
            'original_result_sha256':original_sha,'stop':stop,'transport_generation_requests':engine.provider.client.used,
            'summary':summarize(rows,planned=40),
            'results':[completed.get(c['id'],{'id':c['id'],'execution':'skipped','reason':'provider_stop'}) for c in dataset['cases']]}
        result['summary']['warning']='Two availability windows; each case executed once. All initial provider failures retained. Exposed development data, not an independent benchmark or SLA.'
        (out/'answer-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert sha(original)==original_sha
    assert result['results'][:len(previous)]==previous
    check();print(json.dumps(result['summary'],indent=2),flush=True)


if __name__=='__main__':main()
