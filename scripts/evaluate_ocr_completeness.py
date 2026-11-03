"""Preserve the previous baseline; evaluate OCR and completeness on exposed data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import re

from finlens.backup import create_backup
from finlens.engine import Engine
from finlens.ingest import reprocess_pdfs
from finlens.providers import ModelConfig, Ollama
from finlens.retrieval_check import evaluate_retrieval
from finlens.retrieval_dataset import fingerprint, quote_text
from finlens.store import Store
from scripts.evaluate_policy_repair import answers, read

ROOT = Path('artifacts/ocr-completeness-v2')
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
    for path,digest in protocol.get('v1_preserved',{}).items():
        if sha(path)!=digest:raise ValueError('Earlier layout regression was changed')
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


def prepare_cached_ocr():
    """Reuse the unchanged v5 first OCR pass, then run the new second pass.

    Public PDFs and original derived text stay in their prior store. This is a
    new corpus/run, not an edit of the measured v1 that lost retrieval recall.
    """
    import pymupdf
    from finlens.ocr_quality import improve_table_ocr, tidy_amounts
    from finlens.pdf_text import VERSION, _compact, read_page
    original=subprocess.check_output(['git','show','ec6959c:finlens/ocr.py'],text=True,encoding='utf-8')
    if original!=Path('finlens/ocr.py').read_text(encoding='utf-8'):
        raise ValueError('Cached first OCR pass no longer matches the implementation')
    ROOT.mkdir(exist_ok=False)
    protocol={'frozen_product_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'snapshot':snapshot(),'role':'Exposed development sources; separate regression after the v1 layout failure.',
        'plan':'200 recent and 200 legacy retrieval cases; one pass of 40 answers, max 120 cloud POSTs.',
        'first_pass':'Reuse unchanged financial-layout-v5-tiled-ocr text from scope-guard-v2; only new checked table OCR/spacing is recomputed.',
        'v1_preserved':{str(p):sha(p) for p in Path('artifacts/ocr-completeness-v1').glob('*200/retrieval-ablation.json')}}
    (ROOT/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    create_backup(BASE/'data',ROOT/'data')
    target=ROOT/'data/ocr/tessdata';target.mkdir(parents=True)
    for lang in ('eng','vie'):
        shutil.copyfile(Path('data/ocr/tessdata')/f'{lang}.traineddata',target/f'{lang}.traineddata')
    store=Store(ROOT/'data')
    for doc in store.documents():
        if store.processing_version(doc['id'])!='financial-layout-v5-tiled-ocr':
            raise ValueError('Expected the unchanged cached first-pass corpus')
        with store.connection() as db:
            pages=[r['text'] for r in db.execute('SELECT text FROM pages WHERE document_id=? ORDER BY page',(doc['id'],))]
        warnings=list(doc['warnings'])
        path=store.pdf_path(doc['id'])
        if sha(path)!=doc['sha256']:raise ValueError('Original PDF changed')
        with pymupdf.open(path) as pdf:
            for index,text in enumerate(pages,1):
                if not any(w.startswith(f'Trang {index}: OCR cục bộ (') for w in warnings):continue
                if '[Vùng trang' in text or pdf[index-1].rect.width/pdf[index-1].rect.height>1.3:
                    # Facing-page/sidebar decisions require the original geometry;
                    # reread these pages rather than infer a cached region.
                    text,issues=read_page(pdf[index-1],ocr_dir=target)
                    pages[index-1]=text
                    warnings.extend(f'Trang {index}: {w}' for w in issues)
                    continue
                text,issues=improve_table_ocr(pdf[index-1],text,target)
                text,count=tidy_amounts(text);pages[index-1]=_compact(text)
                warnings.extend(f'Trang {index}: {w}' for w in issues)
                if count:warnings.append(f'Trang {index}: Đã chuẩn hóa khoảng trắng/dấu nhóm ở {count} số OCR; không đổi chữ số, cần kiểm tra PDF gốc.')
        store.replace_derived_text(doc['id'],pages,warnings,VERSION)
        print('Reprocessed checked table OCR:',doc['company'],flush=True)
    cfg=ModelConfig(embed_model='bge-m3:latest',chat_provider='extractive')
    print(json.dumps(Engine(store,cfg,Ollama(cfg)).index_embeddings()),flush=True)
    check()


def amend_spacing():
    """One pre-measurement migration from v6; no second image/model selection.

    AMOUNT already accepted whitespace, so amount_digits/selection scores are
    unchanged. Only group spaces in the selected OCR text need updating.
    """
    if any((ROOT/p).exists() for p in ('answers','recent200','legacy200','initial-ocr-protocol.json')):
        raise ValueError('Amend only once, before any retrieval or answer measurement')
    protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8'))
    current=snapshot()
    for key in ('labels','main_facts','prior_results'):
        if current[key]!=protocol['snapshot'][key]:raise ValueError('Protected inputs changed')
    changed={p for p in current['code'] if current['code'][p]!=protocol['snapshot']['code'].get(p)}
    if changed!={'finlens/pdf_text.py','finlens/ocr_quality.py'}:
        raise ValueError('Unexpected product changes for spacing migration')
    create_backup(ROOT/'data',ROOT/'initial-ocr-data')
    shutil.copyfile(ROOT/'protocol.json',ROOT/'initial-ocr-protocol.json')
    from finlens.ocr_quality import tidy_amounts, amount_digits
    from finlens.pdf_text import VERSION
    store=Store(ROOT/'data');audit=[]
    for doc in store.documents():
        if store.processing_version(doc['id'])!='financial-layout-v6-checked-numeric-ocr':
            raise ValueError('Expected the completed v6 corpus')
        with store.connection() as db:
            pages=[r['text'] for r in db.execute('SELECT text FROM pages WHERE document_id=? ORDER BY page',(doc['id'],))]
        warnings=list(doc['warnings']);edits=[]
        for index,original in enumerate(pages,1):
            if not any(w.startswith(f'Trang {index}: OCR cục bộ (') for w in warnings):continue
            cleaned,count=tidy_amounts(original)
            if cleaned!=original:
                assert re.sub(r'\D','',cleaned)==re.sub(r'\D','',original)
                assert amount_digits(cleaned)==amount_digits(original)
                pages[index-1]=cleaned;edits.append(index)
                warnings.append(f'Trang {index}: Đã khép khoảng trắng trong {count} số OCR; không đổi chữ số.')
        if edits:store.replace_derived_text(doc['id'],pages,warnings,VERSION)
        else:
            with store.connection() as db:
                db.execute('UPDATE document_processing SET version=? WHERE document_id=?',(VERSION,doc['id']))
        audit.append({'company':doc['company'],'changed_pages':edits})
    protocol.update(frozen_product_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),snapshot=current,
        amendment={'before_first_measurement':True,'reason':'Safe group-space cleanup on continuation pages; no change to digits or OCR candidate scores.',
            'initial_protocol':'initial-ocr-protocol.json','initial_corpus':'initial-ocr-data','changes':audit})
    (ROOT/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    cfg=ModelConfig(embed_model='bge-m3:latest',chat_provider='extractive')
    print(json.dumps(Engine(store,cfg,Ollama(cfg)).index_embeddings()),flush=True)
    check()


def audit_ocr():
    check();old=Store(BASE/'data');new=Store(ROOT/'data');rows=[]
    for case in read('holdout_retrieval_20261101.json')['cases']:
        doc=next(d for d in new.documents() if d['company']==case['company'])
        for anchor in case['evidence']:
            contains=lambda store:all(quote_text(q) in quote_text(store.page(doc['id'],anchor['page'])['text']) for q in anchor['quotes'])
            rows.append({'id':case['id'],'company':case['company'],**anchor,'before':contains(old),'after':contains(new)})
    audit={'note':'Literal anchor records, not unique OCR errors or character error rate; queries reuse some anchors.',
        'before_missing':sum(not r['before'] for r in rows),'after_missing':sum(not r['after'] for r in rows),
        'recovered':[r for r in rows if not r['before'] and r['after']],
        'regressions':[r for r in rows if r['before'] and not r['after']],
        'remaining':[r for r in rows if not r['after']]}
    (ROOT/'ocr-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in audit.items()},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','retrieval','answers','amend_spacing','prepare_cached_ocr','audit_ocr'])
    action=parser.parse_args().action
    if action=='answers':answers(root=ROOT,check=check)
    else:globals()[action]()
