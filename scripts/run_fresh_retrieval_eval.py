"""Measure the locked 200-case holdout with the frozen production retriever."""
import argparse
import json
from pathlib import Path

from finlens.retrieval_check import evaluate_retrieval
from finlens.retrieval_dataset import fingerprint, load_dataset
from finlens.store import Store
from scripts.prepare_fresh_holdout import assert_frozen


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='artifacts/new-holdout-final-v1')
    parser.add_argument('--cases-file',default='finlens/fixtures/holdout_retrieval_20261101.json')
    args=parser.parse_args()
    protocol=json.loads(Path('finlens/fixtures/holdout_20261101_protocol.json').read_text(encoding='utf-8'))
    assert_frozen(protocol)
    dataset=load_dataset(args.cases_file);root=Path(args.root);out=root/'retrieval'
    if out.exists():raise ValueError('Do not overwrite a held-out measurement')
    before=fingerprint(Store('data').facts())
    registry=json.loads(Path('finlens/fixtures/holdout_20261101_reports.json').read_text(encoding='utf-8'))
    reports=registry if isinstance(registry,list) else registry['reports']
    report=evaluate_retrieval(Store(root/'data'),out,cases=dataset['cases'],reports=reports,
        dataset_info={k:dataset[k] for k in ('name','split','label_origin')})
    assert_frozen(protocol)
    if before!=fingerprint(Store('data').facts()):raise ValueError('Main-store facts changed')
    print(json.dumps({'cases':report['cases'],'summary':report['summary']},indent=2))


if __name__=='__main__':main()
