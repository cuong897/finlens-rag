"""Validate all supplied reviews and report exposed-source regression honestly."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess

from finlens.review_scores import score_reviews
from finlens.retrieval_dataset import fingerprint
from scripts.evaluate_policy_repair import ROOT,FIXTURES,check_frozen,read
from scripts.finalize_fresh_evaluation import quality_breakdown


def main():
    protocol=check_frozen();dataset=read('holdout_answers_20261101.json')
    answer=json.loads((ROOT/'answers/answer-check.json').read_text(encoding='utf-8'))
    supplied=json.loads((ROOT/'assistant-grades.json').read_text(encoding='utf-8'))
    retrieval=json.loads((ROOT/'retrieval/retrieval-ablation.json').read_text(encoding='utf-8'))
    baseline=read('holdout_20261101_result.json')
    probe=json.loads((ROOT/'flash38-longer-timeout.json').read_text(encoding='utf-8'))
    if len(answer['results'])!=40 or any(r['execution']!='completed' for r in answer['results']):raise ValueError('40 complete answers required')
    if set(supplied['scores'])!={c['id'] for c in dataset['cases']}:raise ValueError('Every answer needs an explicit source review')
    out=ROOT/'final';out.mkdir(exist_ok=False)
    # score_reviews supports the answer-check contract with a top-level dataset
    # hash. Keep provider telemetry separate, without changing any raw answer.
    quality_input={k:answer[k] for k in ('kind','dataset_sha256','results')}
    (out/'quality-input.json').write_text(json.dumps(quality_input,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['id','reviewer','question','answer','sources','correctness','completeness','source_support','abstention_correct','notes']
    with (out/'answer-reviews.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in answer['results']:
            a=r['response'];g=supplied['scores'][r['id']]
            writer.writerow(dict(zip(fields,[r['id'],supplied['reviewer'],r['question'],a['answer'],json.dumps(a['citations'],ensure_ascii=False),*g])))
    review=score_reviews(out/'quality-input.json',out/'answer-reviews.csv',FIXTURES/'holdout_answers_20261101.json',out/'quality')
    quality=quality_breakdown(dataset,answer['results'],review)
    provider_window={**answer['summary'],'warning':'One pass of 40 unique, previously exposed cases. Not independent generalization, concurrent load or multi-day availability. The generic three-wave warning in raw telemetry was inherited from the earlier summarizer; raw rows are preserved.'}
    sources=read('holdout_20261101_reports.json')
    files=[ROOT/'preparation.json',ROOT/'assistant-grades.json',ROOT/'answers/requests.jsonl',ROOT/'answers/answer-check.json',
           ROOT/'retrieval/retrieval-ablation.json',ROOT/'flash38-longer-timeout.json',out/'answer-reviews.csv',out/'quality-input.json',out/'quality/review-scores.json',
           FIXTURES/'policy_repair_protocol_20261103.json',FIXTURES/'holdout_retrieval_20261101.json',FIXTURES/'holdout_answers_20261101.json']
    summary={'version':1,'kind':'development_regression_after_fresh_source_failures','date':'2026-11-03',
        'measured_product_commit':protocol['frozen_product_commit'],'report_commit_parent':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'label_role':'Previously held-out sources now exposed and used for fixes; not independent test.',
        'unchanged_retrieval_labels_sha256':fingerprint(read('holdout_retrieval_20261101.json')),
        'unchanged_answer_labels_sha256':fingerprint(dataset),
        'source_documents':len(sources),'source_pages':sum(s['pages'] for s in sources),'chunks':retrieval['eligible_chunks'],
        'baseline_artifacts_unchanged':True,'main_facts_unchanged':True,'main_facts_sha256':protocol['main_facts_sha256'],
        'regression_tests_passed':258,
        'retrieval':{'cases':200,'summary':retrieval['summary'],'by_company':retrieval['by_company'],
            'visual_label_missing_cases':len({r['id'] for r in retrieval['visual_labels_missing_from_extracted_text']})},
        'quality':quality,'separate_language_review':supplied.get('separate_language_review'),
        'provider_window':provider_window,'provider_posts':answer['transport_generation_requests'],
        'separate_timeout_probe':probe,
        'comparison':{'baseline_commit':baseline['frozen_product_commit'],'retrieval_before':baseline['retrieval']['summary']['hybrid_context'],
            'quality_before':baseline['quality'],'provider_conditions_note':'Different observation dates and actual fallback model mixture; not a controlled comparison of model quality or cloud availability.'},
        'limitations':['Same assistant authors/reviews references; no independent human adjudication.',
            'The 200 questions reuse 64 financial rows and shared policy families; not 200 independent facts.',
            'The 40 answer cases cover policies/unsupported questions, not numeric answer accuracy or every RAG use case.',
            'Finding readable text or the right page does not certify OCR values or claim entailment.',
            '40 sequential requests and three separate synthetic probes are not a multi-day or concurrent SLA measurement.',
            'All four issuers must remain development sources for these changes; a new untouched corpus is needed for generalization.'],
        'artifact_sha256':{p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (FIXTURES/'policy_repair_result_20261103.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    write_report(out/'REPORT.md',summary)
    print(json.dumps({'quality':quality,'provider_window':answer['summary']},indent=2))


def write_report(path,s):
    old=s['comparison']['retrieval_before'];new=s['retrieval']['summary']['hybrid_context']
    q=s['quality'];before=s['comparison']['quality_before'];w=s['provider_window'];pct=lambda v:f'{v*100:.2f}%'
    lines=['# FinLens — sửa OCR và chính sách kế toán, 03/11/2026','',
        '**Đây là hồi quy phát triển trên tập đã lộ, không phải test độc lập mới.** Giữ nguyên 200 câu, 40 câu nội dung, nhãn và PDF từ đợt 01/11. Không ghi đè kết quả ban đầu. Bốn nguồn đã được thêm vào exposed_sources.json trước khi sửa.','',
        f"Mã được đo: `{s['measured_product_commit']}`. {s['source_documents']} báo cáo / {s['source_pages']} trang / {s['chunks']} đoạn embedding. Kho thử riêng có 0 facts; 59 facts trong kho chính giữ nguyên SHA256.",'',
        '## Thay đổi','',
        '- OCR tính hợp các vùng ảnh chưa có chữ, không cộng chồng diện tích logo/stamp. Khi nhiều dải ảnh chiếm ít nhất nửa trang, ghép và OCR toàn trang; vẫn giữ đường đọc cũ cho PDF đã có lớp chữ.',
        '- Không nhận báo cáo dài chỉ đọc được tối đa 10% trang (ít nhất 10 trang); cảnh báo chung khi ít nhất 25% trang quá ít chữ. Phần duyệt/xác nhận số liệu vẫn bắt buộc.',
        '- Tách ý định hỏi chính sách/định nghĩa/phương pháp khỏi hỏi giá trị số. Thêm truy xuất thuyết minh song ngữ và ưu tiên đoạn thực sự có quy tắc; câu hỏi tỷ giá cuối kỳ ưu tiên đoạn có cả tỷ giá mua và bán.',
        '- Sửa lời nhắc để trả lời chính sách, giữ phạm vi phải thu/tài sản/nợ phải trả và thuật ngữ bình quân gia quyền. Đây là hướng dẫn và xếp hạng nguồn, chưa phải bộ chứng minh suy luận đúng.',
        '- Từ chối nêu đầy đủ câu hỏi chưa đủ bằng chứng thay vì mặc định nói thiếu nguyên nhân.','',
        '## Kết quả giữ nguyên nhãn','',
        '| Chỉ số | Trước | Sau |','| --- | ---: | ---: |',
        f"| Recall trang@5 / 200 câu | {pct(old['recall_at_5'])} | {pct(new['recall_at_5'])} |",
        f"| MRR@5 | {old['mrr_at_5']:.4f} | {new['mrr_at_5']:.4f} |",
        f"| Đủ bằng chứng nguyên văn trong top 5 | {old['evidence_complete_questions']}/200 | {new['evidence_complete_questions']}/200 |",
        f"| Đúng hoàn toàn trên câu có đáp án | {before['fully_correct_answerable']}/32 | {q['fully_correct_answerable']}/32 |",
        f"| Từ chối nhầm câu có đáp án | {before['false_refusals']}/32 | {q['false_refusals']}/32 |",
        f"| Từ chối đúng câu thiếu bằng chứng | {before['correct_negative_refusals']}/8 | {q['correct_negative_refusals']}/8 |",'',
        f"Chấm tất cả 40 đáp án mới một lần duy nhất, không chọn lần tốt nhất: đúng hoàn toàn gồm từ chối đúng {pct(q['reviewer_supplied_metrics']['fully_correct_rate'])}; đủ ý {pct(q['reviewer_supplied_metrics']['fully_complete_rate'])}; nguồn hỗ trợ trọn vẹn {pct(q['reviewer_supplied_metrics']['fully_supported_rate'])} trên {q['reviewer_supplied_metrics']['source_support_denominator']} đáp án có nội dung. Phiếu chấm giữ nguyên câu trả lời và citation JSON, importer kiểm tra hash/ID/nội dung/nguồn.",'',
        '| Doanh nghiệp | Recall@5 | MRR@5 | Đủ anchor | Đúng câu có đáp án |','| --- | ---: | ---: | ---: | ---: |']
    for c,m in s['retrieval']['by_company'].items():
        m=m['hybrid_context'];g=q['by_company'][c]
        lines.append(f"| {c} | {pct(m['recall_at_5'])} | {m['mrr_at_5']:.4f} | {m['evidence_complete_questions']}/50 | {g['fully_correct_answerable']}/8 |")
    lines+=['',
        'Điểm nội dung và ngôn ngữ được tách riêng: BCM nguyên giá tài sản trả đúng ý và có nguồn nhưng toàn bộ bằng tiếng Anh, chưa đạt yêu cầu giao diện tiếng Việt. BCM ghi nhận doanh thu nêu đúng năm điều kiện cốt lõi nhưng bỏ sót điều kiện hợp đồng cho phép trả hàng, nên điểm đầy đủ chỉ 1/2. Không diễn giải tỷ lệ đúng ý chính thành chất lượng hoàn hảo.',
        f"BCM có văn bản trên 79/79 trang sau sửa, nhưng chưa đồng nghĩa chữ số đúng hết. Còn {s['retrieval']['visual_label_missing_cases']}/200 câu có chuỗi ảnh chuẩn không còn nguyên văn trong bản trích xuất; có thể gồm lỗi chữ số, dấu phân cách hoặc khoảng trắng OCR. {200-new['evidence_complete_questions']}/200 câu chưa đủ toàn bộ anchor trong top 5. Không sửa nhãn để nâng điểm và không tự xác nhận số liệu OCR.",
        f"Truy xuất context p50 {new['latency_p50_ms']:.2f}ms, p95 {new['latency_p95_ms']:.2f}ms, gồm query embedding cục bộ, không gồm giao diện/HTTP ứng dụng. Các phương án BM25/dense/RRF/context đầy đủ có trong retrieval-ablation.json; thay đổi nhập liệu và truy xuất được đo chung, không tách đóng góp từng thay đổi.",'',
        '## Gemini','',
        f"Trong 40 câu mới, hoàn tất {w['completed']}/40; {w['valid_provider_responses']}/{w['completed']-w['generation_not_attempted']} lượt thực sự gọi mô hình nhận được phản hồi hợp lệ của chuỗi; có {s['provider_posts']} POST sinh thực sự. Phản hồi hợp lệ có thể là từ chối, không phải thước đo nội dung đúng. p50 toàn luồng {w['p50_ms']/1000:.2f}s, p95 {w['p95_ms']/1000:.2f}s.",'',
        '| Mô hình | Thành công / thực gọi | Lỗi |','| --- | ---: | --- |']
    for model,m in w['per_model'].items():lines.append(f"| {model} | {m['succeeded']}/{m['attempts']} | {json.dumps(m['errors'])} |")
    lines+=['',
        'Thử riêng Flash 3.8 bằng ba đoạn văn giả lập ngắn, không fallback, chờ tối đa 30 giây/lượt:',
        *[f"- Lượt {i+1}: {r['status']}, {r.get('code','không lỗi')}, {r['latency_ms']/1000:.2f}s." for i,r in enumerate(s['separate_timeout_probe']['results'])],
        'Thời gian chờ dài hơn vẫn chưa giúp ba lượt này thành công, nên giữ chính sách 10 giây/lần và chuỗi dự phòng hiện tại. Không kết luận Flash 3.8 luôn không hoạt động; đây là số đo của cửa sổ/tài khoản hiện tại. Không thử model trả phí khác. Token của lượt lỗi giữ thiếu, không suy đoán hóa đơn.',
        'Các lượt trước/sau khác ngày và có thể khác mô hình dự phòng thực trả lời. Không coi chênh lệch điểm là đánh giá đối chứng chất lượng các mô hình.','',
        '## Giới hạn và bàn giao','',
        '- 258 unittest đạt. Mã sản phẩm được khóa trong phép đo; kết quả test 01/11 và facts chính được kiểm tra không đổi.',
        '- Bộ 200 câu dùng lại 64 dòng tài chính; 40 câu nội dung dùng các họ câu chính sách chung. Trợ lý vừa viết nhãn vừa chấm. Chưa có người chấm độc lập, thử tải đồng thời, theo dõi nhiều ngày hoặc bộ doanh nghiệp chưa xem sau các sửa đổi này.',
        '- Recall và bám nguồn còn khoảng trống; chưa nghiệm thu tất cả tiêu chí là tốt. Muốn kết luận tổng quát hóa cần báo cáo chưa dùng phát triển, khóa pipeline và nhãn trước khi đo.',
        '- Hồ sơ: summary.json, answer-reviews.csv, quality/review-scores.json, answers/requests.jsonl, retrieval/retrieval-ablation.json và flash38-longer-timeout.json. Mã/nhãn/JSON tóm tắt có commit; tài liệu, PDF, dữ liệu và .env tiếp tục ở ngoài Git.','']
    path.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
