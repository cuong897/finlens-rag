"""Evaluate exact numeric text preservation and conservative draft extraction.

No generation or facts writes. Synthetic corpus metrics are not real-report
accuracy. Missing or failed pages remain in the denominator.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import pymupdf

from finlens.ocr import needs_ocr
from finlens.pdf_text import read_page, VERSION
from finlens.suggestions import extract_page


def assemble(source):
    labels = json.loads((source / 'labels.json').read_text(encoding='utf-8'))
    with pymupdf.open(source / 'native-source.pdf') as native, pymupdf.open() as result:
        for item, page in zip(labels['pages'], native):
            mode = item['mode']
            if mode == 'native':
                result.insert_pdf(native, from_page=page.number, to_page=page.number)
                continue
            target = result.new_page(width=595, height=842)
            # Native heading survives in mixed pages; a footer alone survives in
            # the footer mode. Neither is enough to recover the raster table.
            clip = pymupdf.Rect(0, 65, 595, 780) if mode == 'mixed' else pymupdf.Rect(0, 0, 595, 780)
            dpi = 120 if mode == 'scan_120' else 200
            pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
            target.insert_image(clip, stream=pix.tobytes('png'))
            if mode == 'mixed':
                target.insert_text((35, 40), 'CONSOLIDATED BALANCE SHEET', fontsize=14)
            if mode == 'scan_with_footer':
                target.insert_text((35, 815), 'Electronic copy - please consult the original document', fontsize=9)
        result.save(source / 'quality-corpus.pdf', garbage=4, deflate=True)
    labels['pdf_sha256'] = hashlib.sha256((source / 'quality-corpus.pdf').read_bytes()).hexdigest()
    (source / 'labels.json').write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assembled_pages': len(labels['pages']), 'sha256': labels['pdf_sha256']}))


def score_fields(fields, text, candidates):
    expected = {(f['metric'], f['year'], f['value'], f['unit'], f['currency']) for f in fields}
    if len(expected) != len(fields): raise ValueError('Duplicate gold field')
    actual = Counter((c['metric'], c['year'], c['value'], c['unit'], c['currency']) for c in candidates)
    import re
    return {'expected_fields':len(expected),
        'numeric_text_expected':sum(f.get('raw_value') is not None for f in fields),
        'numeric_text_preserved':sum(bool(re.search(r'(?<![\d.,])' + re.escape(f['raw_value']) + r'(?![\d.,])', text)) for f in fields if f.get('raw_value') is not None),
        'exact_drafts':len(expected & actual.keys()), 'proposals':len(candidates),
        'wrong_drafts':sum(count for key,count in actual.items() if key not in expected),
        'duplicates':sum(max(0,n-1) for n in actual.values()),
        'missing_fields':[f for f in fields if (f['metric'],f['year'],f['value'],f['unit'],f['currency']) not in actual],
        'unexpected_drafts':[c for c in candidates if (c['metric'],c['year'],c['value'],c['unit'],c['currency']) not in expected],
        'all_drafts_unverified':all(not c['verified'] for c in candidates)}


def evaluate(source, output, ocr_dir):
    labels = json.loads((source / 'labels.json').read_text(encoding='utf-8'))
    path = source / 'quality-corpus.pdf'
    if hashlib.sha256(path.read_bytes()).hexdigest() != labels['pdf_sha256']:
        raise ValueError('Source checksum mismatch')
    output.mkdir(parents=True, exist_ok=False)
    rows = []; groups = {}; start = time.perf_counter()
    with pymupdf.open(path) as pdf:
        if len(pdf) != len(labels['pages']): raise ValueError('Page count mismatch')
        for item, page in zip(labels['pages'], pdf):
            started = time.perf_counter(); error = None; text = ''; warnings = []; candidates = []
            detected = None
            try:
                detected = needs_ocr(page)
                text, warnings = read_page(page, ocr_dir=ocr_dir)
                candidates = extract_page(page, 'synthetic-corpus', item['page'], ocr_dir=ocr_dir)['candidates']
            except Exception as exc:
                error = type(exc).__name__
            row = {'id': item['id'], 'page': item['page'], 'mode': item['mode'], 'language': item['language'],
                **score_fields(item['fields'], text, candidates),
                'ocr_expected': item['expected_ocr'], 'ocr_detected': detected,
                'warnings': warnings, 'error': error, 'latency_ms': round((time.perf_counter()-started)*1000,2)}
            rows.append(row)
            (output / f"page-{item['page']:03d}.txt").write_text(text, encoding='utf-8')
            if item['page'] % 10 == 0: print(f"Processed {item['page']}/{len(pdf)}", flush=True)
    for key, selected in [('all', rows)] + [(mode, [r for r in rows if r['mode']==mode]) for mode in sorted({r['mode'] for r in rows})] + [(lang,[r for r in rows if r['language']==lang]) for lang in ['vi','en']]:
        fields = sum(r['expected_fields'] for r in selected); correct = sum(r['exact_drafts'] for r in selected); wrong = sum(r['wrong_drafts'] for r in selected); proposals = sum(r['proposals'] for r in selected)
        groups[key] = {'pages': len(selected), 'fields': fields, 'numeric_text_preserved': sum(r['numeric_text_preserved'] for r in selected),
            'exact_drafts': correct, 'draft_recall': correct/fields if fields else None,
            'draft_precision': correct/proposals if proposals else None, 'proposals':proposals,
            'wrong_drafts': wrong, 'errors': sum(r['error'] is not None for r in selected)}
    report = {'kind':'synthetic_pdf_quality_development', 'warning':labels['limitations'], 'parser_version':VERSION,
        'pymupdf_version':pymupdf.VersionBind, 'pdf_sha256':labels['pdf_sha256'],
        'labels_sha256':hashlib.sha256((source/'labels.json').read_bytes()).hexdigest(),
        'duration_seconds':round(time.perf_counter()-start,2), 'groups':groups, 'rows':rows,
        'facts_written':0, 'generation_calls':0, 'independent_evaluation':False}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(groups,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True); parser.add_argument('--assemble',action='store_true')
    parser.add_argument('--output-dir'); parser.add_argument('--ocr-dir',default='data/ocr/tessdata')
    args = parser.parse_args(); source = Path(args.source_dir)
    if args.assemble: assemble(source)
    elif not args.output_dir: parser.error('--output-dir is required for evaluation')
    else: evaluate(source,Path(args.output_dir),Path(args.ocr_dir))


if __name__ == '__main__': main()
