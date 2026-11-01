"""Measure first retrieval, preparation and warm retrieval without chat calls.

This is a local development profile, not a controlled model-cold or load test.
The resident state of Ollama is not changed or forced by this script.
"""
import argparse
import json
from pathlib import Path
import time

from finlens.engine import Engine
from finlens.models import AskRequest
from finlens.providers import ModelConfig, Ollama
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store


class TimedStore(Store):
    def retrieval_inputs(self, *args, **kwargs):
        start = time.perf_counter()
        try: return super().retrieval_inputs(*args, **kwargs)
        finally: self.inputs_ms = (time.perf_counter() - start) * 1000


class TimedEmbedder(Ollama):
    def embed(self, *args, **kwargs):
        start = time.perf_counter()
        try: return super().embed(*args, **kwargs)
        finally: self.embed_ms = (time.perf_counter() - start) * 1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--embedding-model', default='bge-m3:latest')
    args = parser.parse_args()
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=False)
    store = TimedStore(args.data_dir)
    before = fingerprint(store.facts())
    cases = json.loads(Path('finlens/fixtures/benchmark_development_v3_200.json').read_text(encoding='utf-8'))['cases']
    cases = [cases[i] for i in [0, 15, 50, 75, 100, 145, 150, 165]]
    config = ModelConfig(embed_model=args.embedding_model, chat_provider='extractive')
    reports = {}; rankings = {}
    for phase in ['unprepared', 'prepared']:
        store = TimedStore(args.data_dir); provider = TimedEmbedder(config)
        engine = Engine(store, config, provider)
        preparation = engine.prepare_retrieval() if phase == 'prepared' else None
        rows = []; rankings[phase] = []
        for case in cases:
            request = AskRequest(question=case['question'], company=case['company'], years=[case['report_year']], scope=case['scope'], dataset='real')
            provider.embed_ms = 0
            start = time.perf_counter(); hits, mode, warnings = engine.retrieve(request)
            elapsed = (time.perf_counter() - start) * 1000
            rows.append({'id': case['id'], 'total_ms': round(elapsed, 2), 'snapshot_ms': round(store.inputs_ms, 2),
                'local_embedding_ms': round(provider.embed_ms, 2), 'ranking_and_other_ms': round(elapsed - store.inputs_ms - provider.embed_ms, 2), 'mode': mode, 'warnings': warnings})
            rankings[phase].append([(h['id'], h['score']) for h in hits])
        reports[phase] = {'preparation': preparation, 'rows': rows}
    result = {'kind': 'retrieval_startup_development_profile',
        'limitation': '8 known development queries, sequential phases; model residency uncontrolled. Preparation shifts cost to startup. No generation, no quality/SLA claim.',
        'phases': reports, 'identical_rankings': rankings['unprepared'] == rankings['prepared'],
        'facts_unchanged': before == fingerprint(store.facts()), 'facts_sha256': before}
    (out / 'startup-profile.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if not result['facts_unchanged'] or not result['identical_rankings']: raise SystemExit(1)


if __name__ == '__main__': main()
