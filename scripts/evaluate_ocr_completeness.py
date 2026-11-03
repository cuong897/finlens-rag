"""Preserve the previous baseline; evaluate OCR and completeness on exposed data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from finlens.backup import create_backup
from finlens.engine import Engine
from finlens.ingest import reprocess_pdfs
from finlens.providers import ModelConfig, Ollama
from finlens.retrieval_check import evaluate_retrieval
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store
from scripts.evaluate_policy_repair import answers, read

ROOT = Path('artifacts/ocr-completeness-v1')
BASE = Path('artifacts/scope-guard-v2')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot():
    return {
        'code': {p.as_posix(): sha(p) for p in Path('finlens').glob('*.py')},
        'labels': {p.as_posix(): sha(p) for p in Path('finlens/fixtures').glob('holdout_*20261101.json')},
        'main_facts': fingerprint(Store('data').facts()),
        'prior_results': {p.as_posix(): sha(p) for p in [BASE/'answers/answer-check.json',
            BASE/'recent200/retrieval-ablation.json', BASE/'legacy200/retrieval-ablation.json']},
    }


def check():
    protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8'))
    if snapshot()!=protocol['snapshot']:
        raise ValueError('Frozen code, labels, main facts or baseline changed')
    return protocol


def prepare():
    ROOT.mkdir(exist_ok=True)
    if (ROOT/'protocol.json').exists():
        raise ValueError('Do not overwrite a frozen run')
    protocol={'frozen_product_commit': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'snapshot':snapshot(), 'role':'Development regression on exposed sources; not independent validation.',
        'plan':'Unchanged 200 recent + 200 legacy retrieval cases; one pass of all 40 answers, max 120 cloud POSTs. No best-of-N selection.',
        'ocr_note':'Local structural checks only; numerical disagreements remain unverified.'}
    (ROOT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    create_backup(BASE/'data', ROOT/'data')
    target=ROOT/'data/ocr/tessdata';target.mkdir(parents=True)
    for lang in ('eng','vie'):
        shutil.copyfile(Path('data/ocr/tessdata')/f'{lang}.traineddata',target/f'{lang}.traineddata')
    result=reprocess_pdfs(Store(ROOT/'data'))
    (ROOT/'reprocess.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    cfg=ModelConfig(embed_model='bge-m3:latest',chat_provider='extractive')
    print(json.dumps(Engine(Store(ROOT/'data'),cfg,Ollama(cfg)).index_embeddings()),flush=True)
    check()


def retrieval():
    check()
    for name,source,labels,reports in [
        ('recent200',ROOT/'data','holdout_retrieval_20261101.json','holdout_20261101_reports.json'),
        ('legacy200',Path('data'),'benchmark_development_v3_200.json','benchmark_reports_v3.json')]:
        out=ROOT/name
        if out.exists():raise ValueError('Do not overwrite measured results')
        r=evaluate_retrieval(Store(source),out,cases=read(labels)['cases'],reports=read(reports),
            dataset_info={'name':'ocr-completeness-'+name,'split':'development',
                'label_origin':'Previously exposed sources; unchanged frozen labels. No independent-test claim.'})
        print(json.dumps({'set':name,'metrics':r['summary']['hybrid_context']},indent=2),flush=True)
        check()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','retrieval','answers'])
    action=parser.parse_args().action
    if action=='answers':answers(root=ROOT,check=check)
    else:globals()[action]()
