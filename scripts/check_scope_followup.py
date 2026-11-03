"""Four bounded follow-ups; never replace the preceding 40-case answers."""
from dataclasses import replace
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import httpx

from finlens.engine import Engine
from finlens.environment import load_env_file
from finlens.models import AskRequest
from finlens.providers import ModelConfig,ProviderError
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-root',default='artifacts/scope-guard-final');args=parser.parse_args()
    root=Path(args.output_root);root.mkdir(exist_ok=True);path=root/'followup.json'
    if path.exists():raise ValueError('Do not overwrite targeted responses')
    ids=['vnm-inventory-value','vnm-fx-closing','bcm-inventory-value','mwg-sales-recognition']
    dataset=json.loads(Path('finlens/fixtures/holdout_answers_20261101.json').read_text(encoding='utf-8'))
    code={p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('finlens').glob('*.py')}
    facts=fingerprint(Store('data').facts());cases=[next(c for c in dataset['cases'] if c['id']==i) for i in ids]
    old=Path('artifacts/scope-guard-v2/answers/answer-check.json');old_sha=hashlib.sha256(old.read_bytes()).hexdigest()
    protocol={'product_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'code_sha256':code,
        'cases_sha256':fingerprint(dataset),'case_ids':ids,'max_posts':12,'max_quality_repairs_per_question':1,'chain_deadline_seconds':30,'previous_40_answers_sha256':old_sha,
        'note':'Known development cases, separate follow-up; not an independent test or replacement aggregate.'}
    (root/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    load_env_file('.env');config=replace(ModelConfig.from_env(),embed_model='bge-m3:latest');rows=[]
    with httpx.Client(trust_env=False) as client:
        class Cap:
            used=0
            def post(self,*a,**kw):
                if self.used>=12:raise ProviderError('Follow-up budget exhausted',code='evaluation_budget')
                self.used+=1;return client.post(*a,**kw)
        engine=Engine(Store('artifacts/scope-guard-v2/data'),config);engine.provider.client=Cap()
        for i,c in enumerate(cases):
            if i:time.sleep(6)
            a=engine.answer(AskRequest(company=c['company'],years=c['years'],scope=c['scope'],question=c['question']))
            rows.append({'id':c['id'],'question':c['question'],'response':a})
            path.write_text(json.dumps({'kind':'targeted_followup','protocol':protocol,'provider_posts':engine.provider.client.used,'results':rows},ensure_ascii=False,indent=2),encoding='utf-8')
            print(c['id'],a['status'],a.get('generation_model'),a.get('generation_error'),flush=True)
            if a.get('generation_error') and a['generation_error']['code'] in {'rate_limited','access_denied','missing_key','invalid_request'}:break
    assert facts==fingerprint(Store('data').facts())
    assert hashlib.sha256(old.read_bytes()).hexdigest()==old_sha
    assert code=={p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('finlens').glob('*.py')}


if __name__=='__main__':main()
