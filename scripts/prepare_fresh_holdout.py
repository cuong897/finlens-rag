"""Prepare only a separately registered held-out corpus; no chat calls or tuning."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

from finlens.engine import Engine
from finlens.ingest import ingest_pdf
from finlens.local import prepare_embeddings
from finlens.providers import ModelConfig, Ollama
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store


def assert_frozen(protocol):
    for name,digest in protocol['frozen_product_files'].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen product changed: '+name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--index',action='store_true');args=parser.parse_args()
    protocol=json.loads(Path('finlens/fixtures/holdout_20261101_protocol.json').read_text(encoding='utf-8'))
    assert_frozen(protocol)
    root=Path(args.root);store=Store(root/'data');reports=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    existing=Store('data');before=fingerprint(existing.facts())
    old_companies={d['company'] for d in existing.documents()};old_sha={d['sha256'] for d in existing.documents()}
    forbidden=json.loads(Path('finlens/fixtures/exposed_sources.json').read_text(encoding='utf-8'))
    old_companies.update(d['company'] for d in forbidden);old_sha.update(d['sha256'] for d in forbidden)
    ocr=store.root/'ocr/tessdata';ocr.mkdir(parents=True,exist_ok=True)
    for language in ['eng','vie']:shutil.copyfile(Path('data/ocr/tessdata')/(language+'.traineddata'),ocr/(language+'.traineddata'))
    results=[]
    for report in reports:
        if report['company'] in old_companies or report['sha256'] in old_sha:raise ValueError('Exposed issuer/source')
        source=root/'sources'/report['filename'];content=source.read_bytes()
        if hashlib.sha256(content).hexdigest()!=report['sha256']:raise ValueError('Source checksum mismatch')
        print('Ingesting '+report['company'],flush=True);started=time.perf_counter()
        context={k:report[k] for k in ['period_start','period_end','period_label']}
        result=ingest_pdf(store,content,report['filename'],report['company'],report['year'],report['scope'],context=context)
        document=next(d for d in store.documents() if d['id']==result['document']['id'])
        if document['page_count']!=report['pages']:raise ValueError('Page count mismatch')
        store.record_source(result['document']['id'],report['url'],report['publisher'])
        results.append({'company':report['company'],'created':result['created'],'document_id':result['document']['id'],'seconds':round(time.perf_counter()-started,2)})
        print('Imported '+report['company']+' '+str(round(time.perf_counter()-started,1))+'s',flush=True)
    if args.index:
        config=prepare_embeddings('data',ModelConfig(embed_model='bge-m3:latest',chat_provider='extractive'))
        class Progress(Ollama):
            def embed(self,texts,**kwargs):
                result=super().embed(texts,**kwargs);self.count+=len(texts)
                if self.count%80==0:print('Embedded '+str(self.count)+' new chunks',flush=True)
                return result
        provider=Progress(config);provider.count=0
        print(json.dumps(Engine(store,config,provider).index_embeddings()),flush=True)
    assert_frozen(protocol)
    if before!=fingerprint(existing.facts()) or store.facts():raise ValueError('Facts modified')
    (root/'preparation.json').write_text(json.dumps({'imports':results,'main_facts_unchanged':True,'heldout_facts':0,'product_frozen':True,'cloud_generation_calls':0},indent=2),encoding='utf-8')


if __name__=='__main__':main()
