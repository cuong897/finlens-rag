"""Paired retrieval timing with fixed local query vectors; no cloud generation."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

from finlens.engine import Engine
from finlens.local import prepare_embeddings
from finlens.models import AskRequest
from finlens.providers import ModelConfig, Ollama
from finlens.store import Store


class ReloadStore(Store):
    def retrieval_inputs(self,*args,**kwargs):
        with self._vector_cache_lock:self._vector_cache=None
        return super().retrieval_inputs(*args,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=False)
    cfg=prepare_embeddings('data');embedder=Ollama(cfg)
    cases=json.loads(Path('finlens/fixtures/benchmark_development_v3_200.json').read_text(encoding='utf-8'))['cases']
    selected=[cases[i] for i in [0,15,50,75,100,145,150,165]]
    vectors={c['question']:embedder.embed([c['question']])[0] for c in selected}
    class FixedEmbedding:
        def embed(self,texts):return [vectors[text] for text in texts]
    stores={'reload_every_request':ReloadStore('data'),'warm_cache':Store('data')}
    engines={name:Engine(store,ModelConfig(embed_model=cfg.embed_model),FixedEmbedding()) for name,store in stores.items()}
    first=selected[0];request=AskRequest(company=first['company'],years=[first['report_year']],scope=first['scope'],question=first['question'],dataset='real')
    start=time.perf_counter();engines['warm_cache'].retrieve(request);cold_ms=(time.perf_counter()-start)*1000
    rows=[]
    for repeat in range(3):
        for index,case in enumerate(selected):
            request=AskRequest(company=case['company'],years=[case['report_year']],scope=case['scope'],question=case['question'],dataset='real')
            rankings={}
            order=list(engines) if (repeat+index)%2==0 else list(reversed(engines))
            for name in order:
                started=time.perf_counter();hits,mode,warnings=engines[name].retrieve(request)
                rows.append({'case':case['id'],'repeat':repeat,'strategy':name,'latency_ms':round((time.perf_counter()-started)*1000,2)})
                assert mode=='hybrid_rrf' and not warnings
                rankings[name]=[(h['id'],h['score']) for h in hits]
            assert rankings['reload_every_request']==rankings['warm_cache']
    summary={}
    for name in engines:
        values=sorted(r['latency_ms'] for r in rows if r['strategy']==name)
        summary[name]={'samples':len(values),'p50_ms':statistics.median(values),'p95_ms':values[max(0,int(len(values)*.95+.999)-1)]}
    snapshot=stores['warm_cache']._vector_cache[2]
    estimate=sys.getsizeof(dict(snapshot))+sum(sys.getsizeof(v)+len(v)*sys.getsizeof(0.0) for v in snapshot.values())
    report={'kind':'paired_cache_performance_development','warning':'8 known queries repeated 3 times, fixed identical query vectors; excludes local query embedding inference, cloud generation and HTTP. Not production load/SLA or independent quality benchmark.',
        'cold_cached_retrieval_ms':round(cold_ms,2),'summary':summary,'all_24_paired_rankings_identical':True,
        'cached_vectors':len(snapshot),'estimated_retained_vector_bytes':estimate,'rows':rows}
    (output/'cache-performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))


if __name__=='__main__':main()
