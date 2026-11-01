"""Check supported draft metrics against existing visual development labels.

Uses already-exposed reports and pilot labels, not a new independent test. The target
protocol records VND integers; unsigned/currency/scale inference is not gold.
No facts are written and no generation or embedding provider is called.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import time

import pymupdf

from finlens.pdf_text import read_page, VERSION
from finlens.retrieval_dataset import fingerprint
from finlens.store import Store
from finlens.suggestions import extract_page, parse_amount
from scripts.evaluate_pdf_quality import score_fields


METRICS = {'current_assets':'current_assets','cash':'cash','inventory':'inventory',
    'total_assets':'total_assets','liabilities':'liabilities','current_liabilities':'current_liabilities',
    'equity':'equity','revenue':'revenue','gross_profit':'gross_profit','interest':'interest_expense',
    'pat':'net_income','cfo':'operating_cash_flow'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default='data');parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();store=Store(args.data_dir);before=fingerprint(store.facts())
    output=Path(args.output_dir);output.mkdir(parents=True,exist_ok=False)
    source=Path('finlens/fixtures/benchmark_targets_v3.json')
    targets=json.loads(source.read_text(encoding='utf-8'))['targets']
    reports=json.loads(Path('finlens/fixtures/benchmark_development_v3_200.json').read_text(encoding='utf-8'))['reports']
    pilot_path=Path('finlens/fixtures/public_pilot.json')
    pilot=json.loads(pilot_path.read_text(encoding='utf-8'))
    documents=store.documents();rows=[];started=time.perf_counter()
    for report in reports+pilot:
        document=next(d for d in documents if d['company']==report['company'] and d['report_year']==report['year'] and d['sha256']==report['sha256'])
        path=store.pdf_path(document['id'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=report['sha256']:raise ValueError('Source checksum mismatch')
        pages=defaultdict(list)
        for target in targets.get(report['company'],[]):
            if target['id'] not in METRICS:continue
            for year,raw in target['values'].items():
                value=parse_amount(raw)
                if value is None:raise ValueError('Unparseable existing visual label')
                pages[target['page']].append({'metric':METRICS[target['id']],'year':int(year),'value':value,
                    'raw_value':raw,'unit':'one','currency':'VND','source_label':target['label']})
        for fact in report.get('facts',[]):
            pages[fact['page']].append({'metric':fact['metric'],'year':fact['year'],'value':fact['value'],
                'raw_value':None,'unit':'one','currency':'VND','source_label':fact['label']})
        with pymupdf.open(path) as pdf:
            for number,fields in sorted(pages.items()):
                text,warnings=read_page(pdf[number-1],ocr_dir=store.root/'ocr/tessdata')
                extracted=extract_page(pdf[number-1],document['id'],number,context=document,ocr_dir=store.root/'ocr/tessdata')
                keys={(f['metric'],f['year']) for f in fields}
                candidates=[c for c in extracted['candidates'] if (c['metric'],c['year']) in keys]
                row={'company':report['company'],'report_year':report['year'],'page':number,'source_sha256':report['sha256'],
                    **score_fields(fields,text,candidates),
                    'outside_label_scope_proposals':len(extracted['candidates'])-len(candidates),
                    'warnings':warnings+extracted['warnings']}
                rows.append(row)
                (output/(report['company']+'-%d-%03d.txt'%(report['year'],number))).write_text(text,encoding='utf-8')
        print('Checked '+report['company'],flush=True)
    groups={}
    for name,selected in [('all',rows)]+[(company,[r for r in rows if r['company']==company]) for company in sorted({r['company'] for r in rows})]:
        total=sum(r['expected_fields'] for r in selected);correct=sum(r['exact_drafts'] for r in selected);proposals=sum(r['proposals'] for r in selected)
        groups[name]={'pages':len(selected),'fields':total,'exact_drafts':correct,'proposals':proposals,
            'recall_within_supported_metrics':correct/total,'precision_within_label_scope':correct/proposals if proposals else None,
            'wrong_drafts':sum(r['wrong_drafts'] for r in selected),'numeric_text_preserved':sum(r['numeric_text_preserved'] for r in selected),'numeric_text_expected':sum(r['numeric_text_expected'] for r in selected)}
    result={'kind':'existing_real_pdf_draft_development','warning':'Existing assistant visual labels already exposed for development. 12 supported target metrics plus 29 pilot fields. Total receivables targets excluded because the app metric means trade receivables. Extra proposals outside gold metric/year/page scope are not judged false. Not independent, production accuracy or full-document coverage.',
        'parser_version':VERSION,'targets_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'pilot_labels_sha256':hashlib.sha256(pilot_path.read_bytes()).hexdigest(),
        'groups':groups,'rows':rows,'facts_unchanged':before==fingerprint(store.facts()),'facts_sha256':before,
        'duration_seconds':round(time.perf_counter()-started,2),'generation_calls':0,'facts_written':0}
    (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(groups,indent=2))
    if not result['facts_unchanged']:raise SystemExit(1)


if __name__=='__main__':main()
