"""Validate the complete source-reviewed pass without replacing old answers."""
import csv
import hashlib
import json
from pathlib import Path

from finlens.retrieval_dataset import fingerprint
from finlens.review_scores import score_reviews
from scripts.finalize_fresh_evaluation import quality_breakdown
from scripts.evaluate_scope_guard import check


def main():
    protocol=check();root=Path('artifacts/scope-guard-v2');out=root/'final';out.mkdir(exist_ok=False)
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    dataset=read('finlens/fixtures/holdout_answers_20261101.json')
    answer=read(root/'answers/answer-check.json');manual=read(root/'assistant-grades.json')
    if answer['dataset_sha256']!=fingerprint(dataset):raise ValueError('Labels changed')
    if len(answer['results'])!=40 or any(r['execution']!='completed' for r in answer['results']):raise ValueError('Incomplete run')
    if set(manual['scores'])!={c['id'] for c in dataset['cases']}:raise ValueError('Review every answer')
    quality_input={k:answer[k] for k in ('kind','dataset_sha256','results')}
    (out/'quality-input.json').write_text(json.dumps(quality_input,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['id','reviewer','question','answer','sources','correctness','completeness','source_support','abstention_correct','notes']
    with (out/'answer-reviews.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for row in answer['results']:
            a=row['response'];g=manual['scores'][row['id']]
            writer.writerow(dict(zip(fields,[row['id'],manual['reviewer'],row['question'],a['answer'],json.dumps(a['citations'],ensure_ascii=False),*g])))
    review=score_reviews(out/'quality-input.json',out/'answer-reviews.csv','finlens/fixtures/holdout_answers_20261101.json',out/'quality')
    quality=quality_breakdown(dataset,answer['results'],review)
    retrieval={name:read(root/name/'retrieval-ablation.json') for name in ['recent200','legacy200']}
    before=read('artifacts/holdout-repair-20261103/retrieval/retrieval-ablation.json')
    if retrieval['recent200']['corpus_sha256']!=before['corpus_sha256']:raise ValueError('Corpus changed')
    result={'version':1,'date':'2026-11-03','kind':'scope_and_bilingual_rows_development_regression',
        'measured_product_commit':protocol['frozen_product_commit'],'test_count':270,'label_role':'Exposed development sources, not independent test.',
        'original_results_unchanged':True,'main_facts_unchanged':True,'main_facts_sha256':protocol['main_facts_sha256'],
        'same_recent_corpus_sha256':before['corpus_sha256'],
        'retrieval':{name:{'cases':r['cases'],'summary':r['summary'],'by_company':r['by_company']} for name,r in retrieval.items()},
        'recent_before':before['summary']['hybrid_context'],'quality':quality,
        'language_review':manual.get('language_review'),'provider_window':answer['summary'],'provider_posts':answer['transport_generation_requests'],
        'scope_rejection_attempts':sum(a.get('code')=='unsupported_scope' for r in answer['results'] for a in r['response']['generation_attempts']),
        'label_scope_note':read(root/'label-scope-note.json'),
        'limitations':['Same assistant author/reviewer; all source cases previously observed.',
            'Classification guard detects a narrow syntactic broadening, not arbitrary entailment.',
            'Recall@5 and canonical pages do not establish numeric OCR fidelity or answer accuracy.',
            'Short sequential provider window is not multi-day availability or concurrent-load SLA.'],
        'sha256':{p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'answers/answer-check.json',root/'assistant-grades.json',out/'answer-reviews.csv',out/'quality/review-scores.json',root/'recent200/retrieval-ablation.json',root/'legacy200/retrieval-ablation.json',Path('finlens/fixtures/scope_guard_protocol_20261103.json')]}}
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('finlens/fixtures/scope_guard_result_20261103.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    report(out/'REPORT.md',result)
    check();print(json.dumps(quality,indent=2))


def report(path,s):
    new=s['retrieval']['recent200']['summary']['hybrid_context'];old=s['recent_before'];legacy=s['retrieval']['legacy200']['summary']['hybrid_context'];q=s['quality'];w=s['provider_window']
    pct=lambda x:f'{100*x:.2f}%'
    lines=['# FinLens — kiểm tra phạm vi và dòng tài chính song ngữ','',
        'Ngày 03/11/2026. **Hồi quy phát triển, không phải test độc lập mới.** Nhãn, PDF, corpus và embedding của bộ gần đây giữ nguyên. Giữ riêng ứng viên trước và mọi câu trả lời cũ.','',
        f"Mã đo: `{s['measured_product_commit']}`. 270 unittest đạt. Kho chính giữ nguyên facts và các báo cáo thử chỉ ở kho riêng.",'',
        '## Thay đổi','',
        '- Kiểm tra điều kiện phân loại ở chính nguồn được dẫn: chặn việc biến một nhóm được phân loại có điều kiện thành khẳng định cho toàn bộ nhóm. Cho phép câu có điều kiện, phủ định và chủ ngữ đã thu hẹp. Đây là kiểm tra hẹp, chưa phải bộ chứng minh ngữ nghĩa tổng quát.',
        '- Khớp cả nhãn dòng tiếng Việt; chịu được mất dấu/dính âm tiết ở tiêu đề. Nhãn ngắn tiếng Anh bị OCR xen chữ chỉ được khớp nới khi có mã dòng và hai cột số; giới hạn lượng nhiễu.',
        '- Ghép nhãn với dòng công thức/số kế tiếp; loại lợi nhuận thuộc cổ đông mẹ, cổ đông không kiểm soát hoặc lợi nhuận chưa phân phối khi đang hỏi lợi nhuận sau thuế toàn doanh nghiệp. Không dùng mã doanh nghiệp, số chuẩn hoặc trang gold để xếp hạng.','',
        '## Truy xuất','',
        '| Bộ | Recall@5 | MRR@5 | Đủ bằng chứng nguyên văn | p95 ms |','| --- | ---: | ---: | ---: | ---: |',
        f"| 200 câu gần đây — trước | {pct(old['recall_at_5'])} | {old['mrr_at_5']:.4f} | {old['evidence_complete_questions']}/200 | {old['latency_p95_ms']:.2f} |",
        f"| 200 câu gần đây — sau | {pct(new['recall_at_5'])} | {new['mrr_at_5']:.4f} | {new['evidence_complete_questions']}/200 | {new['latency_p95_ms']:.2f} |",
        f"| 200 câu cũ — hồi quy | {pct(legacy['recall_at_5'])} | {legacy['mrr_at_5']:.4f} | {legacy['evidence_complete_questions']}/200 | {legacy['latency_p95_ms']:.2f} |",'',
        'Recall đếm trang chuẩn trong 5 đoạn; MRR dùng trang chuẩn đầu tiên. Đủ bằng chứng yêu cầu tất cả chuỗi gốc trong cùng đoạn của từng anchor. Đây không phải thước đo suy luận hoặc đúng năm/cột/số trong đáp án. Các câu có tương quan do dùng lại dòng và họ câu hỏi.',
        'Giới hạn nhãn SAB: trang 10 đã có Net profit after tax của cả hai năm, chuyển tiếp sang trang 11. Nhãn gốc chỉ chọn trang 11 cho chỉ tiêu này. Không đổi nhãn hoặc tính một tỷ lệ thay thế thuận lợi hơn; trường hợp lấy đúng số ở trang 10 vẫn có thể là miss của metric chuẩn. Xem label-scope-note.json.','',
        '## 40 đáp án — một lượt, chấm đủ từng câu','',
        f"Đúng ý chính trên câu có đáp án: {q['fully_correct_answerable']}/32; từ chối nhầm: {q['false_refusals']}/32; từ chối đúng câu thiếu bằng chứng: {q['correct_negative_refusals']}/8. Đủ ý: {pct(q['reviewer_supplied_metrics']['fully_complete_rate'])}; bám nguồn trọn vẹn: {pct(q['reviewer_supplied_metrics']['fully_supported_rate'])} trên {q['reviewer_supplied_metrics']['source_support_denominator']} đáp án có nội dung.",
        'Trợ lý vừa viết nhãn vừa đối chiếu, không chứng nhận chấm độc lập. Phiếu answer-reviews.csv giữ nguyên nội dung/citations; importer kiểm tra hash, ID, câu hỏi và nguồn. Không chọn kết quả tốt nhất qua nhiều lượt.','',
        '## Gemini trong cửa sổ đo','',
        f"Hoàn tất {w['completed']}/40; nhận phản hồi chuỗi hợp lệ ở {w['valid_provider_responses']} lượt, {s['provider_posts']} POST thực. Có {s['scope_rejection_attempts']} lần thử bị chặn do phạm vi phân loại. Phản hồi hợp lệ có thể là từ chối, không đồng nghĩa nội dung đúng. p50 toàn luồng {w['p50_ms']/1000:.2f}s, p95 {w['p95_ms']/1000:.2f}s.",
        '| Mô hình | Thành công / lần thử | Lỗi |','| --- | ---: | --- |']
    for model,m in w['per_model'].items():lines.append(f"| {model} | {m['succeeded']}/{m['attempts']} | {json.dumps(m['errors'])} |")
    lines+=['','Không suy ra SLA hay độ ổn định dài hạn từ 40 lượt tuần tự. Không phát sinh model trả phí mới, không lưu key trong hồ sơ. Các lượt lỗi thiếu token giữ nguyên thiếu, không suy đoán hóa đơn.','',
        '## Giới hạn còn lại','',
        f"Còn {200-new['evidence_complete_questions']}/200 câu chưa đủ anchor nguyên văn trong top 5; có thể do OCR, chia đoạn, lựa chọn nguồn hoặc giới hạn trang chuẩn. Không tự xác nhận số liệu OCR. Cần nguồn chưa dùng phát triển và người chấm độc lập để kết luận tổng quát hóa; chưa thể tuyên bố mọi tiêu chí của sản phẩm đều tốt. Phần kiểm tra điều kiện không phát hiện được mọi cách diễn giải vượt phạm vi.",'']
    path.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
