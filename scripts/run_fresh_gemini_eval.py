"""Bounded, append-only RAG answer/stability measurement on registered new PDFs."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import time
from dataclasses import replace

import httpx

from finlens.engine import Engine,valid_citations
from finlens.environment import load_env_file
from finlens.models import AskRequest
from finlens.providers import ModelConfig,ProviderError,token_usage
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store
from scripts.prepare_fresh_holdout import assert_frozen


def schedule(cases):
    if len(cases)!=40:raise ValueError('Protocol requires 40 unique cases')
    return [(wave,case) for wave,selected in [(1,cases),(2,list(reversed(cases))),(3,cases[10:30])] for case in selected]


def summarize(rows,planned=100):
    attempts=[a for r in rows for a in r['response'].get('generation_attempts',[]) if a.get('status') in {'succeeded','failed'}]
    per_model={}
    for name in sorted({a['model'] for a in attempts}):
        values=[a for a in attempts if a['model']==name];lat=sorted(a['latency_ms'] for a in values)
        per_model[name]={'attempts':len(values),'succeeded':sum(a['status']=='succeeded' for a in values),
            'errors':dict(Counter(a.get('code') for a in values if a['status']=='failed')),
            'p50_ms':statistics.median(lat),'p95_ms':lat[max(0,int(len(lat)*.95+.999)-1)]}
    latency=sorted(r['response']['latency_ms'] for r in rows)
    successful=[r for r in rows if r['response'].get('generation_model') and not r['response'].get('generation_error')]
    generated=sum(bool(r['response'].get('generation_attempted')) for r in rows)
    usage=Counter()
    for a in attempts:usage.update(token_usage(a.get('usage')) or {})
    return {'planned':planned,'completed':len(rows),'not_executed':planned-len(rows),'valid_provider_responses':len(successful),
        'observed_chain_success_rate':len(successful)/len(rows) if rows else None,
        'provider_response_rate_on_attempted_questions':len(successful)/generated if generated else None,
        'generation_not_attempted':sum(not r['response'].get('generation_attempted') for r in rows),
        'provider_attempts':len(attempts),'per_model':per_model,
        'fallback_responses':sum(r['response'].get('fallback_used',False) for r in rows),
        'p50_ms':statistics.median(latency) if latency else None,'p95_ms':latency[max(0,int(len(latency)*.95+.999)-1)] if latency else None,
        'reported_usage_totals':dict(usage),'attempts_without_total_tokens':sum('totalTokenCount' not in (a.get('usage') or {}) for a in attempts),
        'cost_estimate':None,'warning':'Repeated questions in three nearby time windows; not long-term availability, independent samples, semantic quality or SLA.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--cases-file',required=True);parser.add_argument('--env-file',required=True)
    args=parser.parse_args();root=Path(args.root)
    protocol=json.loads(Path('finlens/fixtures/holdout_20261101_protocol.json').read_text(encoding='utf-8'));assert_frozen(protocol)
    dataset=json.loads(Path(args.cases_file).read_text(encoding='utf-8'));plan=schedule(dataset['cases'])
    output=root/'gemini';output.mkdir(exist_ok=False);load_env_file(args.env_file)
    config=ModelConfig.from_env()
    config=replace(config,embed_model=config.embed_model or 'bge-m3:latest')
    if config.chat_provider!='gemini' or not config.chat_model:raise ValueError('Configured Gemini profile required')
    store=Store(root/'data');before=fingerprint(Store('data').facts());rows=[];consecutive=0;stop=None
    class CappedClient:
        used=0
        def post(self,*a,**kw):
            if self.used>=300:raise ProviderError('Evaluation provider budget exhausted',code='evaluation_budget')
            self.used+=1;return client.post(*a,**kw)
    with httpx.Client(trust_env=False) as client:
        engine=Engine(store,config);engine.provider.client=CappedClient()
        for index,(wave,case) in enumerate(plan):
            if index:
                time.sleep(60 if wave!=plan[index-1][0] else 6)
            assert_frozen(protocol)
            request=AskRequest(company=case['company'],years=case['years'],question=case['question'],scope=case['scope'],dataset='real')
            answer=engine.answer(request)
            source_identity=all((doc:=store.get_document(c['document_id'])) and doc['company']==case['company'] and doc['scope']==case['scope'] and not doc['is_demo'] and store.page(c['document_id'],c['page']) for c in answer['citations'])
            contract=bool(source_identity and answer['status']==case['expected_status'] and not answer.get('generation_error') and answer.get('generation_model'))
            if answer['mode']=='generated':contract=contract and valid_citations(answer['answer'],answer['citations'])
            row={'id':case['id'],'wave':wave,'recorded_at':datetime.now(timezone.utc).isoformat(),'question':case['question'],
                'reference':case['reference'],'execution':'completed','contract_passed':contract,'response':answer}
            rows.append(row)
            with (output/'requests.jsonl').open('a',encoding='utf-8') as handle:handle.write(json.dumps(row,ensure_ascii=False)+'\n')
            error=answer.get('generation_error');consecutive=consecutive+1 if error else 0
            print(f"{index+1}/100 wave={wave} id={case['id']} model={answer.get('generation_model')} error={error}",flush=True)
            if error and (consecutive>=3 or error['code'] in {'access_denied','missing_key','invalid_request','rate_limited','evaluation_budget'}):
                stop={'after_request':index+1,'reason':error['code'],'consecutive_failures':consecutive};break
    result={'kind':'fresh_real_rag_stability_pilot','frozen_product_commit':protocol['frozen_product_commit'],'dataset_sha256':fingerprint(dataset),
        'configured_model':config.chat_model,'fallback_models':list(config.fallback_models),'embedding_model':config.embed_model,
        'transport_generation_requests':engine.provider.client.used,
        'summary':summarize(rows),'stop':stop,'facts_unchanged':before==fingerprint(Store('data').facts()),'results':rows}
    (output/'stability.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    first={r['id']:r for r in rows if r['wave']==1}
    quality={'kind':'fresh_real_answer_quality_pilot','dataset_sha256':fingerprint(dataset),'results':[first.get(c['id'],{'id':c['id'],'execution':'skipped','reason':'provider_stop','question':c['question'],'reference':c['reference']}) for c in dataset['cases']]}
    (output/'answer-check.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result['summary'],indent=2))


if __name__=='__main__':main()
