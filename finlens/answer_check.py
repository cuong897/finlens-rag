"""Answer integration contracts and independent human-review exports.

Source existence and term overlap do not measure semantic entailment.
Provider outages are never scored as correct abstentions.
"""
import csv
from copy import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

from finlens.engine import valid_citations
from finlens.models import AskRequest
from finlens.providers import ProviderError, token_usage
from finlens.retrieval_dataset import fingerprint
from finlens.text import normalize


class GenerationBudget:
    """Bound provider invocations even if an unexpectedly routed case generates."""
    def __init__(self, backend, limit):
        self.backend, self.limit, self.used = backend, limit, 0

    def embed(self, texts):
        return self.backend.embed(texts)

    def generate_with_report(self, question, evidence):
        if self.used >= self.limit:
            raise ProviderError('Đã đạt ngân sách câu hỏi AI của lượt đánh giá.', code='evaluation_budget')
        self.used += 1
        return self.backend.generate_with_report(question,evidence)


def score_contract(case, answer, store):
    citations = answer['citations']
    checks = {
        'status': answer['status'] == case['expected_status'],
        'provider_ok': answer.get('generation_error') is None,
        'source_identity': all((page := store.page(c['document_id'],c['page'])) is not None and
            (doc := store.get_document(c['document_id'])) is not None and doc['company']==case['company'] and
            doc['scope']==case.get('scope','consolidated') and doc['is_demo'] for c in citations),
    }
    if 'expected_mode' in case: checks['mode'] = answer['mode']==case['expected_mode']
    if case.get('expected_mode')=='generated':
        checks['generation_provenance'] = bool(answer.get('generation_attempted') and answer.get('generation_model') and
            any(a.get('status')=='succeeded' and a.get('model')==answer['generation_model'] for a in answer.get('generation_attempts',[])))
        checks['citation_format'] = valid_citations(answer['answer'],citations)
    if 'source_page' in case: checks['expected_source'] = any(c['page']==case['source_page'] for c in citations)
    if 'growth' in case: checks['exact_growth'] = [c['growth_percent'] for c in answer['comparisons']]==case['growth']
    if 'exact_text' in case: checks['exact_lookup'] = case['exact_text'] in answer['answer']
    if 'premise_contradicted' in case: checks['premise_check'] = (answer.get('premise_check') or {}).get('contradicted')==case['premise_contradicted']
    terms=case.get('concept_groups',[])
    diagnostic=[any(normalize(term) in normalize(answer['answer']) for term in group) for group in terms]
    return {'contract_passed':all(checks.values()),'checks':checks,'concept_matches_diagnostic':diagnostic,
            'semantic_review':'pending' if case.get('requires_model') else 'not_applicable',
            'warning':'Term matching and citation format do not establish answer correctness or entailment.'}


def run_answer_checks(engine, dataset, output_dir, *, live=False, max_model_questions=2, interval=15, pause=time.sleep):
    if not 0<=max_model_questions<=10 or not 0<=interval<=60:
        raise ValueError('Budget or interval outside supported bounds.')
    if not live and engine.config.chat_model:
        raise ValueError('Offline evaluation must not have a generation model configured.')
    results=[];reserved=0;outage=False
    if live:
        engine=copy(engine)
        engine.provider=GenerationBudget(engine.provider,max_model_questions)
    for case in dataset['cases']:
        if case.get('requires_model'):
            reason='live_disabled' if not live else 'provider_error_stop' if outage else 'model_question_budget' if reserved>=max_model_questions else None
            if reason:
                results.append({'id':case['id'],'execution':'skipped','reason':reason,'question':case['question'],'reference':case.get('reference','')});continue
            if reserved and interval:pause(interval)
            reserved+=1
        request=AskRequest(company=case['company'],years=case['years'],question=case['question'],scope=case.get('scope','consolidated'),dataset='demo')
        started=time.perf_counter();answer=engine.answer(request)
        outcome=score_contract(case,answer,engine.store)
        results.append({'id':case['id'],'execution':'completed','latency_ms':round((time.perf_counter()-started)*1000,2),
                        'question':case['question'],'reference':case.get('reference',''),**outcome,'response':answer})
        if answer.get('generation_error'):outage=True
    executed=[r for r in results if r['execution']=='completed']
    attempted=[r for r in executed if r['response'].get('generation_attempted')]
    provider_attempts=[a for r in attempted for a in r['response'].get('generation_attempts',[]) if a.get('status') in {'failed','succeeded'}]
    reported_usage={}
    for attempt in provider_attempts:
        for key,value in (token_usage(attempt.get('usage')) or {}).items():
            reported_usage[key]=reported_usage.get(key,0)+value
    correct_abstain=sum(r['response']['status']=='insufficient_evidence' and r['checks']['provider_ok']
        for r in executed if next(c for c in dataset['cases'] if c['id']==r['id'])['expected_status']=='insufficient_evidence')
    report={'kind':'synthetic_answer_integration_development','generated_at':datetime.now(timezone.utc).isoformat(),
        'warning':dataset['label_origin'],'dataset_sha256':fingerprint(dataset),'live':live,
        'planned':len(results),'completed':len(executed),'skipped':len(results)-len(executed),
        'contract_passed':sum(r['contract_passed'] for r in executed),'model_questions_reserved':reserved,
        'generation_questions_attempted':len(attempted),'provider_attempts_recorded':sum(a.get('status') in {'failed','succeeded'} for r in attempted for a in r['response'].get('generation_attempts',[])),
        'generation_questions_succeeded':sum(bool(r['response'].get('generation_model')) and r['checks']['provider_ok'] for r in attempted),
        'provider_error_stop':outage,'correct_abstentions_contract_only':correct_abstain,
        'reported_usage_totals':reported_usage or None,
        'provider_attempts_with_total_tokens':sum('totalTokenCount' in (a.get('usage') or {}) for a in provider_attempts),
        'provider_attempts_without_total_tokens':sum('totalTokenCount' not in (a.get('usage') or {}) for a in provider_attempts),
        'cost_estimate':None,
        'semantic_accuracy':None,'source_entailment_accuracy':None,'human_review_status':'pending',
        'latency_p50_ms':statistics.median(r['latency_ms'] for r in executed) if executed else None,
        'generation_latency_p50_ms':statistics.median(r['latency_ms'] for r in attempted) if attempted else None,
        'generation_latency_p95_ms':sorted(r['latency_ms'] for r in attempted)[max(0,int(len(attempted)*.95+.999)-1)] if attempted else None,
        'results':results}
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=True)
    (output/'answer-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'review-rubric.md').write_text(
        '# Human review rubric\n\nReview each completed answer against the supplied source excerpts and reference. '
        'Use correctness/completeness/source_support: 0 = incorrect or unsupported, 1 = partly correct/supported, 2 = fully correct/supported. '
        'Check every factual claim, amount, sign, year and unit. For abstention_correct use yes/no or N/A. '
        'Provider errors and skipped cases are not correct abstentions. Enter reviewer identity and explain disagreements. '
        'Do not treat keyword matches or valid citation IDs as semantic correctness. '
        'Keep failed answers in the denominator of any declared end-to-end metric. '
        'Report sample size, provider failures, skipped cases and adjudication separately.\n',encoding='utf-8')
    with (output/'human-review.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=['id','execution','question','reference','answer','sources','model','provider_error','generation_usage','timings_ms','correctness','completeness','source_support','abstention_correct','reviewer','notes'])
        writer.writeheader()
        for row in results:
            response=row.get('response',{})
            writer.writerow({'id':row['id'],'execution':row['execution'],'question':row.get('question',''),
                'reference':row.get('reference',''),'answer':response.get('answer',''),
                'sources':json.dumps(response.get('citations',[]),ensure_ascii=False),
                'model':response.get('generation_model'),'provider_error':json.dumps(response.get('generation_error')),
                'generation_usage':json.dumps(response.get('generation_usage')),'timings_ms':json.dumps(response.get('timings_ms'))})
    return report
