"""Validate reviewer-supplied answer scores without certifying independence."""
import csv
import json
from pathlib import Path

from finlens.retrieval_dataset import fingerprint


def score_reviews(results_file,reviews_file,cases_file,output_dir):
    report=json.loads(Path(results_file).read_text(encoding='utf-8'))
    dataset=json.loads(Path(cases_file).read_text(encoding='utf-8'))
    expected_sha=report.get('summary',report).get('dataset_sha256')
    if expected_sha!=fingerprint(dataset):raise ValueError('Bộ câu hỏi không khớp checksum kết quả.')
    cases={c['id']:c for c in dataset['cases']}
    answers={r['id']:r for r in report['results'] if r['execution']=='completed'}
    if len(cases)!=len(dataset['cases']) or set(answers)-set(cases) or len(answers)!=sum(r['execution']=='completed' for r in report['results']):raise ValueError('ID kết quả/bộ câu hỏi không hợp lệ.')
    with Path(reviews_file).open(encoding='utf-8-sig',newline='') as handle:
        rows=list(csv.DictReader(handle))
    seen=set();scored=[]
    for row in rows:
        ident=row.get('id','')
        if ident in seen or ident not in cases:raise ValueError('Phiếu chấm có ID trùng hoặc không tồn tại.')
        seen.add(ident)
        grades=[row.get(k,'').strip() for k in ['correctness','completeness','source_support']]
        if not any(grades) and not row.get('abstention_correct','').strip():continue
        if ident not in answers:raise ValueError('Không chấm câu chưa được chạy.')
        answer=answers[ident]['response']
        if row.get('question')!=cases[ident]['question'] or row.get('answer')!=answer['answer']:
            raise ValueError('Nội dung phiếu chấm khác câu trả lời đã lưu.')
        try:source_matches=json.loads(row.get('sources',''))==answer['citations']
        except json.JSONDecodeError:source_matches=False
        if not source_matches:raise ValueError('Nguồn trong phiếu chấm không khớp kết quả.')
        if not row.get('reviewer','').strip():raise ValueError('Cần ghi người chấm cho mỗi phiếu đã chấm.')
        abstained=answer['status']=='insufficient_evidence'
        if grades[0] not in {'0','1','2'} or grades[1] not in {'0','1','2'}:
            raise ValueError('Điểm đúng/đủ phải là 0, 1 hoặc 2.')
        if grades[2] not in {'0','1','2'} and not (grades[2]=='N/A' and abstained and not answer.get('generation_error')):
            raise ValueError('Điểm nguồn phải là 0, 1, 2; N/A chỉ cho từ chối không lỗi provider.')
        abstention=row.get('abstention_correct','').strip().lower()
        if cases[ident]['expected_status']=='insufficient_evidence' or abstained:
            if abstention not in {'yes','no'}:raise ValueError('Cần chấm từ chối yes/no cho tình huống từ chối.')
            if answer.get('generation_error') and abstention=='yes':raise ValueError('Lỗi provider không phải từ chối đúng.')
        elif abstention not in {'','n/a'}:
            raise ValueError('Không chấm từ chối cho tình huống có đáp án và đã trả lời.')
        scored.append({'id':ident,'reviewer':row['reviewer'].strip(),'correctness':int(grades[0]),'completeness':int(grades[1]),
                       'source_support':None if grades[2]=='N/A' else int(grades[2]),'abstention_correct':abstention,
                       'notes':row.get('notes','')})
    complete=bool(answers) and len(scored)==len(answers)
    metrics=None
    if complete:
        supporting=[r for r in scored if r['source_support'] is not None]
        refusals=[r for r in scored if r['abstention_correct'] in {'yes','no'}]
        metrics={'fully_correct_rate':sum(r['correctness']==2 for r in scored)/len(scored),
                 'fully_complete_rate':sum(r['completeness']==2 for r in scored)/len(scored),
                 'fully_supported_rate':sum(r['source_support']==2 for r in supporting)/len(supporting) if supporting else None,
                 'source_support_denominator':len(supporting),'abstention_judged_count':len(refusals),
                 'correct_abstention_rate':sum(r['abstention_correct']=='yes' for r in refusals)/len(refusals) if refusals else None}
    result={'kind':'reviewer_supplied_answer_scores','completed_answers':len(answers),'scored_answers':len(scored),
        'pending_answers':len(answers)-len(scored),'unexecuted_cases':len(dataset['cases'])-len(answers),
        'review_coverage':len(scored)/len(answers) if answers else None,'all_completed_answers_reviewed':complete,
        'quality_metrics':metrics,'independent_review_verified':False,
        'warning':'Reviewer identities and judgments are supplied, not independently verified by this importer. No quality aggregate is published while completed answers remain unreviewed. These scores inherit the dataset scope and do not establish generalization.',
        'dataset_sha256':fingerprint(dataset),'scores':scored}
    output=Path(output_dir);output.mkdir(parents=True,exist_ok=False)
    (output/'review-scores.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
