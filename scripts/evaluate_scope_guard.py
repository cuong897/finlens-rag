"""Frozen exposed-source regression; no label edits or best-of-N answers."""
import argparse
import hashlib
import json
from pathlib import Path

from finlens.retrieval_dataset import fingerprint
from finlens.retrieval_check import evaluate_retrieval
from finlens.store import Store
from scripts.prepare_fresh_holdout import assert_frozen
from scripts.evaluate_policy_repair import answers as run_answers,read

ROOT=Path('artifacts/scope-guard-v1')


def check():
    p=read('scope_guard_protocol_20261103.json');assert_frozen(p)
    if fingerprint(Store('data').facts())!=p['main_facts_sha256']:raise ValueError('Main facts changed')
    for name,sha in p['prior_result_sha256'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=sha:raise ValueError('Prior result changed')
    return p


def retrieval():
    check()
    for name,store_path,filename,reports in [
        ('recent200','artifacts/holdout-repair-20261103/data','holdout_retrieval_20261101.json','holdout_20261101_reports.json'),
        ('legacy200','data','benchmark_development_v3_200.json','benchmark_reports_v3.json')]:
        output=ROOT/name
        if output.exists():raise ValueError('Do not overwrite a measured regression')
        dataset=read(filename)
        result=evaluate_retrieval(Store(store_path),output,cases=dataset['cases'],reports=read(reports),
            dataset_info={'name':'scope-guard-v1-'+name,'split':'development',
                'label_origin':'Previously observed sources; unchanged questions/labels, no tuning after freeze. Development regression, not untouched-test evidence.'})
        check();print(json.dumps({'set':name,'cases':result['cases'],'summary':result['summary']},indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['retrieval','answers']);a=p.parse_args()
    if a.action=='retrieval':retrieval()
    else:
        # Reuse the bounded answer runner and its source/contract records, with
        # an isolated output root and a new immutable product protocol.
        # The corpus itself is read-only and shared with the previous regression.
        # Store needs original IDs, vectors and PDFs. Use a verified backup
        # once, keeping this run independent from the old output directory.
        from finlens.backup import create_backup
        target=ROOT/'data'
        if not target.exists():create_backup('artifacts/holdout-repair-20261103/data',target)
        run_answers(root=ROOT,check=check)
