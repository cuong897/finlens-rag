"""Development regression after the 01 November untouched-source measurement.

Never overwrites that baseline or promotes these now-exposed sources to test.
The live answer pass is bounded to 40 cases; no best-of-repeat selection.
"""
import argparse
from dataclasses import replace
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time

import httpx

from finlens.engine import Engine,valid_citations
from finlens.environment import load_env_file
from finlens.models import AskRequest
from finlens.providers import Gemini,ModelConfig,ProviderError,Ollama
from finlens.retrieval_check import evaluate_retrieval
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store
from scripts.prepare_fresh_holdout import assert_frozen
from scripts.run_fresh_gemini_eval import summarize


ROOT=Path('artifacts/holdout-repair-20261103')
FIXTURES=Path('finlens/fixtures')


def read(name):return json.loads((FIXTURES/name).read_text(encoding='utf-8'))


def check_frozen():
    protocol=read('policy_repair_protocol_20261103.json')
    assert_frozen(protocol)
    if fingerprint(Store('data').facts())!=protocol['main_facts_sha256']:raise ValueError('Main facts changed')
    baseline=Path('artifacts/new-holdout-final-v1')
    for relative,sha in protocol['baseline_artifacts'].items():
        if hashlib.sha256((baseline/relative).read_bytes()).hexdigest()!=sha:raise ValueError('Original result changed')
    return protocol


def index():
    cfg=ModelConfig(embed_model='bge-m3:latest',chat_provider='extractive')
    print(json.dumps(Engine(Store(ROOT/'data'),cfg,Ollama(cfg)).index_embeddings()),flush=True)


def retrieval():
    check_frozen();dataset=read('holdout_retrieval_20261101.json');out=ROOT/'retrieval'
    if out.exists():raise ValueError('Do not overwrite regression results')
    result=evaluate_retrieval(Store(ROOT/'data'),out,cases=dataset['cases'],reports=read('holdout_20261101_reports.json'),
        dataset_info={'name':'accounting-policy-and-tiled-OCR-development-regression-20261103','split':'development',
            'label_origin':'The original 200 questions/labels are unchanged, but all four sources are now exposed and used for these fixes. Development regression only, not new generalization evidence.'})
    check_frozen();print(json.dumps(result['summary'],indent=2),flush=True)


def answers():
    protocol=check_frozen();load_env_file('.env');dataset=read('holdout_answers_20261101.json')
    cfg=replace(ModelConfig.from_env(),embed_model='bge-m3:latest')
    if cfg.chat_provider!='gemini' or not cfg.chat_model:raise ValueError('Configured Gemini profile required')
    out=ROOT/'answers';out.mkdir(exist_ok=False);rows=[];consecutive=0;stop=None
    class CappedClient:
        used=0
        def post(self,*args,**kwargs):
            if self.used>=120:raise ProviderError('Regression call budget exhausted',code='evaluation_budget')
            self.used+=1
            return client.post(*args,**kwargs)
    with httpx.Client(trust_env=False) as client:
        engine=Engine(Store(ROOT/'data'),cfg);engine.provider.client=CappedClient()
        for i,c in enumerate(dataset['cases']):
            if i:time.sleep(6)
            check_frozen()
            a=engine.answer(AskRequest(company=c['company'],years=c['years'],scope=c['scope'],question=c['question'],dataset='real'))
            contract=a['status']==c['expected_status'] and not a.get('generation_error') and bool(a.get('generation_model'))
            if a['mode']=='generated':contract=contract and valid_citations(a['answer'],a['citations'])
            row={'id':c['id'],'wave':1,'recorded_at':datetime.now(timezone.utc).isoformat(),'question':c['question'],
                 'reference':c['reference'],'execution':'completed','contract_passed':contract,'response':a}
            rows.append(row)
            with (out/'requests.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            error=a.get('generation_error');consecutive=consecutive+1 if error else 0
            print(f"{i+1}/40 {c['id']} {a['status']} {a.get('generation_model')} {error}",flush=True)
            if error and (consecutive>=3 or error['code'] in {'access_denied','missing_key','invalid_request','rate_limited','evaluation_budget'}):
                stop={'after':i+1,'reason':error['code']};break
    first={r['id']:r for r in rows}
    result={'kind':'exposed_source_answer_regression','dataset_sha256':fingerprint(dataset),
        'product_commit':protocol['frozen_product_commit'],'label_role':'development, not independent test',
        'transport_generation_requests':engine.provider.client.used,'summary':summarize(rows,planned=40),'stop':stop,
        'results':[first.get(c['id'],{'id':c['id'],'execution':'skipped','reason':'provider_stop'}) for c in dataset['cases']]}
    (out/'answer-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    check_frozen();print(json.dumps(result['summary'],indent=2),flush=True)


def probe():
    """Separate short synthetic evidence, longer wait; no automatic retries."""
    check_frozen();load_env_file('.env');cfg=replace(ModelConfig.from_env(),chat_model='gemini-3.8-flash',fallback_models=())
    prompts=[('Explain the inventory measurement policy.', 'Inventories are stated at the lower of cost and net realisable value. Cost is determined on a weighted average basis.'),
             ('Explain the depreciation method.', 'Tangible fixed assets are depreciated on a straight-line basis over their estimated useful lives.'),
             ('Explain the cash-equivalent classification conditions.', 'Cash equivalents are short-term highly liquid investments readily convertible to known amounts of cash with insignificant risk of changes in value.')]
    path=ROOT/'flash38-longer-timeout.json'
    if path.exists():raise ValueError('Do not overwrite probe results')
    results=[]
    with httpx.Client(trust_env=False) as client:
        provider=Gemini(cfg,client)
        for q,text in prompts:
            if results:time.sleep(6)
            started=time.perf_counter()
            try:
                g=provider.generate_with_metadata(q,[{'id':'S1','excerpt':text}],timeout=30)
                r={'question':q,'status':'succeeded','answer':g.text,'usage':g.usage}
            except ProviderError as exc:r={'question':q,'status':'failed','code':exc.code,'usage':exc.usage}
            r['latency_ms']=round((time.perf_counter()-started)*1000,2);results.append(r)
            path.write_text(json.dumps({'kind':'separate_synthetic_timeout_diagnostic','model':cfg.chat_model,'timeout_seconds':30,
                'max_attempts':3,'note':'Short synthetic excerpts, not comparable to full RAG contexts or a model quality benchmark. No fallback or retries.','results':results},indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in r.items() if k not in ['answer','usage']}),flush=True)
            if r.get('code') in {'rate_limited','access_denied','invalid_request','missing_key'}:break


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['index','retrieval','answers','probe'])
    args=parser.parse_args();globals()[args.action]()
