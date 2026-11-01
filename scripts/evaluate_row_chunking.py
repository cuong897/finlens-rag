"""Evaluate experimental line chunks on a private copy; production is untouched.

Run: python -m scripts.evaluate_row_chunking --output-dir artifacts/row-chunks-v1
All evaluated questions are development data. No Gemini generation is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from finlens.engine import Engine
from finlens.local import prepare_embeddings
from finlens.providers import Ollama
from finlens.retrieval_check import evaluate_retrieval
from finlens.retrieval_dataset import fingerprint, load_dataset, quote_text
from finlens.store import Store
from finlens.text import chunk_page_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--cases-file', default='finlens/fixtures/benchmark_development_v3_200.json')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    source, output = Path(args.data_dir).resolve(), Path(args.output_dir).resolve()
    if source == output or source in output.parents or output in source.parents:
        parser.error('Thư mục thử nghiệm phải tách khỏi kho nguồn.')
    ds = load_dataset(args.cases_file)
    if ds['split'] != 'development':
        parser.error('Thử nghiệm chỉ dùng tập development; giữ test riêng.')
    output.mkdir(parents=True, exist_ok=False)
    root = output/'data'; root.mkdir()
    with sqlite3.connect(f'{source.as_uri()}/finlens.sqlite3?mode=ro',uri=True) as original:
        with sqlite3.connect(root/'finlens.sqlite3') as clone:
            original.backup(clone)
    store = Store(root)
    facts_before = fingerprint(store.facts())
    documents = {}
    for case in ds['cases']:
        document = store.find_document(case['company'],case['report_year'],case['scope'],case['source_sha256'])
        if not document: raise ValueError('Thiếu tài liệu đúng checksum.')
        documents[document['id']] = document
    # Only required originals are copied, preserving paths and byte identity.
    for document in documents.values():
        with store.connection() as db:
            relative = db.execute('SELECT pdf_path FROM documents WHERE id=?',(document['id'],)).fetchone()[0]
        if relative:
            src, dst = (source/relative).resolve(), (root/relative).resolve()
            if not src.is_relative_to(source) or not dst.is_relative_to(root): raise ValueError('Đường dẫn nguồn không hợp lệ.')
            if hashlib.sha256(src.read_bytes()).hexdigest()!=document['sha256']: raise ValueError('Checksum PDF sai.')
            dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    cfg = prepare_embeddings(args.data_dir)
    cache = store.embeddings(cfg.embed_model)
    reusable = {c['text']:cache[c['id']] for c in store.chunks() if c['id'] in cache}
    coverage, old_count, new_count, reused = [],0,0,0
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for document_id in documents:
            old_count += db.execute('SELECT count(*) FROM chunks WHERE document_id=?',(document_id,)).fetchone()[0]
            db.execute('DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)',(document_id,))
            db.execute('DELETE FROM chunks WHERE document_id=?',(document_id,))
            for page in db.execute('SELECT page,text FROM pages WHERE document_id=? ORDER BY page',(document_id,)).fetchall():
                for chunk in chunk_page_rows(page['text']):
                    cursor=db.execute('INSERT INTO chunks(document_id,page,text) VALUES (?,?,?)',(document_id,page['page'],chunk));new_count+=1
                    if chunk in reusable:
                        db.execute('INSERT INTO embeddings VALUES (?,?,?)',(cursor.lastrowid,cfg.embed_model,json.dumps(reusable[chunk])));reused+=1
    for case in ds['cases']:
        doc=next(d for d in documents.values() if d['company']==case['company'] and d['sha256']==case['source_sha256'])
        chunks=store.chunks(case['company'],[case['report_year']],case['scope'],'real')
        for anchor in case['evidence']:
            coverage.append(any(c['page']==anchor['page'] and c['document_id']==doc['id'] and
                all(quote_text(q) in quote_text(c['text']) for q in anchor['quotes']) for c in chunks))
    print(json.dumps({'old_chunks':old_count,'new_chunks':new_count,'reused_embeddings':reused,'corpus_anchor_coverage':sum(coverage),'anchors':len(coverage)}),flush=True)
    index=Engine(store,cfg).index_embeddings()
    print(json.dumps(index),flush=True)
    result=evaluate_retrieval(store,output/'evaluation',cases=ds['cases'],dataset_info={k:v for k,v in ds.items() if k!='cases'},embedder=Ollama(cfg),embed_model=cfg.embed_model)
    assert fingerprint(store.facts())==facts_before
    summary={'policy':'experimental-whole-lines-v1','production_changed':False,'facts_unchanged':True,
        'old_chunks':old_count,'new_chunks':new_count,'reused_embeddings':reused,'corpus_anchors':len(coverage),'corpus_anchors_covered':sum(coverage),
        'summary':result['summary'],'dataset_sha256':result['dataset']['sha256'],'implementation_sha256':result['implementation_sha256']}
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
