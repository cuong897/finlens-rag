"""Replay recorded outages locally, then check one previously unexecuted case."""
from dataclasses import replace
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import httpx

from finlens.engine import Engine
from finlens.environment import load_env_file
from finlens.models import AskRequest
from finlens.providers import ModelConfig, Ollama, ProviderError
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store

ROOT=Path('artifacts/excerpt-repair-20261109')
PREVIOUS=Path('artifacts/ocr-completeness-v2/answers-combined/answer-check.json')


def main():
    global ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root',default=str(ROOT))
    parser.add_argument('--replay-only',action='store_true')
    args=parser.parse_args();ROOT=Path(args.output_root);ROOT.mkdir(parents=True,exist_ok=True)
    if (ROOT/'replay.json').exists() or (ROOT/'targeted.json').exists():raise ValueError('Do not overwrite outcomes')
    before=json.loads(PREVIOUS.read_text(encoding='utf-8'));prior_sha=hashlib.sha256(PREVIOUS.read_bytes()).hexdigest()
    dataset=json.loads(Path('finlens/fixtures/holdout_answers_20261101.json').read_text(encoding='utf-8'))
    cases={c['id']:c for c in dataset['cases']}
    code={p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('finlens').glob('*.py')}
    facts=fingerprint(Store('data').facts())
    protocol={'date':'2026-11-09','product_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'prior_result_sha256':prior_sha,'code_sha256':code,'main_facts_sha256':facts,
        'plan':'Replay all six recorded provider errors with a simulated failure and local retrieval, zero cloud calls.'+('' if args.replay_only else ' One separate live MWG recognition case, maximum four POSTs, no repeated question.'),
        'target_id':None if args.replay_only else 'mwg-sales-recognition','role':'Development regression; targeted check does not replace the interrupted 40-case run.'}
    (ROOT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    cfg=ModelConfig(chat_model='simulated-outage',embed_model='bge-m3:latest')
    store=Store('artifacts/ocr-completeness-v2/data');replay=[]
    for row in before['results']:
        if row['execution']!='completed' or not row['response'].get('generation_error'):continue
        class Failure:
            evidence=None
            def embed(self,texts):return Ollama(cfg).embed(texts)
            def generate_with_report(self,question,evidence):
                self.evidence=evidence
                raise ProviderError('Simulated recorded outage',code=row['response']['generation_error']['code'],retryable=True)
        provider=Failure();engine=Engine(store,cfg,provider);c=cases[row['id']]
        answer=engine.answer(AskRequest(question=c['question'],company=c['company'],years=c['years'],scope=c['scope'],dataset='real'))
        # A changed retrieval corpus must not be mistaken for an excerpt-only fix.
        assert all(any(old['excerpt']==new['excerpt'] and old['page']==new['page'] for new in provider.evidence)
                   for old in row['response']['citations'])
        replay.append({'id':row['id'],'before':row['response'],'after':answer})
    (ROOT/'replay.json').write_text(json.dumps({'cloud_calls':0,'simulated_outages':True,'results':replay},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Local outage replays:',len(replay),flush=True)
    if args.replay_only:
        assert code=={p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('finlens').glob('*.py')}
        assert facts==fingerprint(Store('data').facts())
        assert prior_sha==hashlib.sha256(PREVIOUS.read_bytes()).hexdigest()
        return
    c=cases['mwg-sales-recognition']
    assert next(r for r in before['results'] if r['id']==c['id'])['execution']=='skipped'
    load_env_file('.env');cfg=replace(ModelConfig.from_env(),embed_model='bge-m3:latest')
    with httpx.Client(trust_env=False) as client:
        class Cap:
            used=0
            def post(self,*args,**kwargs):
                if self.used>=4:raise ProviderError('Targeted check budget exhausted',code='evaluation_budget')
                self.used+=1;return client.post(*args,**kwargs)
        engine=Engine(store,cfg);engine.provider.client=Cap()
        answer=engine.answer(AskRequest(question=c['question'],company=c['company'],years=c['years'],scope=c['scope'],dataset='real'))
        (ROOT/'targeted.json').write_text(json.dumps({'id':c['id'],'question':c['question'],'provider_posts':engine.provider.client.used,
            'protocol':protocol,'response':answer},ensure_ascii=False,indent=2),encoding='utf-8')
        print(c['id'],answer['status'],answer.get('generation_model'),answer.get('generation_error'),flush=True)
    assert code=={p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('finlens').glob('*.py')}
    assert facts==fingerprint(Store('data').facts())
    assert prior_sha==hashlib.sha256(PREVIOUS.read_bytes()).hexdigest()


if __name__=='__main__':main()
