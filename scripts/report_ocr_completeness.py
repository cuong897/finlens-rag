"""Validate all source reviews and report the unchanged development benchmark."""
import csv
import json
from pathlib import Path

from finlens.review_scores import score_reviews
from finlens.retrieval_dataset import fingerprint
from scripts.evaluate_ocr_completeness import ROOT, BASE, check, sha
from scripts.finalize_fresh_evaluation import quality_breakdown


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main():
    protocol=check();out=ROOT/'final';out.mkdir(exist_ok=False)
    dataset=read('finlens/fixtures/holdout_answers_20261101.json')
    answers=read(ROOT/'answers/answer-check.json');manual=read(ROOT/'assistant-grades.json')
    if answers['dataset_sha256']!=fingerprint(dataset):raise ValueError('Labels changed')
    if len(answers['results'])!=40 or any(r['execution']!='completed' for r in answers['results']):
        raise ValueError('Do not report a partial run as all 40 cases')
    if set(manual['scores'])!={c['id'] for c in dataset['cases']}:raise ValueError('Review every case')
    source={k:answers[k] for k in ('kind','dataset_sha256','results')}
    (out/'quality-input.json').write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['id','reviewer','question','answer','sources','correctness','completeness','source_support','abstention_correct','notes']
    with (out/'answer-reviews.csv').open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for row in answers['results']:
            a=row['response'];g=manual['scores'][row['id']]
            writer.writerow(dict(zip(fields,[row['id'],manual['reviewer'],row['question'],a['answer'],json.dumps(a['citations'],ensure_ascii=False),*g])))
    scored=score_reviews(out/'quality-input.json',out/'answer-reviews.csv',
                        'finlens/fixtures/holdout_answers_20261101.json',out/'quality')
    quality=quality_breakdown(dataset,answers['results'],scored)
    retrieval={n:read(ROOT/n/'retrieval-ablation.json')['summary']['hybrid_context'] for n in ('recent200','legacy200')}
    before=read(BASE/'recent200/retrieval-ablation.json')['summary']['hybrid_context']
    audit=read(ROOT/'ocr-audit.json')
    result={'version':1,'date':'2026-11-03','kind':'ocr_and_completeness_development_regression',
        'measured_product_commit':protocol['frozen_product_commit'],'tests':282,
        'role':'Same previously exposed reports and questions; same-assistant source review, not independent testing.',
        'main_facts_unchanged':True,'prior_results_unchanged':True,'retrieval':retrieval,'retrieval_before':before,
        'ocr':{k:v for k,v in audit.items() if k!='recovered'},'quality':quality,
        'provider':answers['summary'],'provider_posts':answers['transport_generation_requests'],
        'sha256':{p.as_posix():sha(p) for p in [ROOT/'protocol.json',ROOT/'ocr-audit.json',ROOT/'assistant-grades.json',
            ROOT/'answers/answer-check.json',ROOT/'recent200/retrieval-ablation.json',ROOT/'legacy200/retrieval-ablation.json',out/'answer-reviews.csv']}}
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    Path('finlens/fixtures/ocr_completeness_result_20261103.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    new=retrieval['recent200'];legacy=retrieval['legacy200'];w=answers['summary'];q=quality
    lines=['# Sửa OCR và độ đầy đủ của câu trả lời — 03/11/2026','',
        '**Đây là hồi quy phát triển trên dữ liệu đã xem, không phải tập kiểm thử độc lập.** Giữ nguyên PDF, nhãn, câu hỏi và kết quả trước sửa. Không chọn đáp án đẹp nhất qua nhiều lượt.','',
        f"Mã được đo: `{protocol['frozen_product_commit']}`. 282 unittest đạt. 258 trang trong kho riêng, tái sử dụng lượt OCR gốc không đổi và tính lại lượt kiểm tra bảng; không thay số liệu đã xác nhận hoặc kho báo cáo chính.",'',
        '## Thay đổi','',
        '- Bảng OCR có ít nhất 8 số lớn được đọc kiểm tra thêm ở tối đa 400 dpi / 20 triệu điểm ảnh. Chỉ chọn bản thứ hai khi khôi phục được cấu trúc số tốt hơn; giữ cảnh báo khi hai lượt bất đồng. Tiêu chí cấu trúc không chứng minh chữ số đúng.',
        '- Dọn khoảng trắng trong nhóm số, không nối qua dòng. Chuẩn hóa dấu nhóm hỗn hợp chỉ khi bảng VND có tối thiểu 8 số cùng kiểu và tỷ lệ đồng thuận ít nhất 80%. Không thay chữ số, đoán ký tự hoặc tự xác nhận số.',
        '- Hướng dẫn sinh yêu cầu giữ cả điều kiện chung ở đoạn dẫn nhập và điều kiện riêng của nghiệp vụ. Lượt sửa nội dung cũng giữ các điều kiện này. Không thêm lượt gọi chỉ để tìm đáp án thuận lợi hơn.',
        '- Giữ dòng do OCR nhận diện trước khi ghép các ô cùng hàng; dùng trung vị đường chân chữ để tránh chữ cao/dấu đóng chen giữa nhãn. Lượt v1 từng làm Recall giảm còn 97% được giữ nguyên riêng, không ghi đè. Không sửa nhãn chuẩn.','',
        '## Kết quả','',
        '| Chỉ tiêu | Trước | Sau |','| --- | ---: | ---: |',
        f"| Mốc đối chiếu không khớp toàn trang OCR | {audit['before_missing']} | {audit['after_missing']} |",
        f"| Recall@5, 200 câu gần đây | {before['recall_at_5']:.2%} | {new['recall_at_5']:.2%} |",
        f"| MRR@5, 200 câu gần đây | {before['mrr_at_5']:.4f} | {new['mrr_at_5']:.4f} |",
        f"| Đủ toàn bộ bằng chứng trong top 5 | {before['evidence_complete_questions']}/200 | {new['evidence_complete_questions']}/200 |",
        f"| Đáp án đúng trọn vẹn trên 32 câu có đáp án | 30/32 | {q['fully_correct_answerable']}/32 |",'',
        f"Hồi quy 200 câu cũ trên kho chính giữ nguyên: Recall@5 {legacy['recall_at_5']:.2%}, MRR@5 {legacy['mrr_at_5']:.4f}, đủ bằng chứng {legacy['evidence_complete_questions']}/200. Không tái OCR kho chính trong phép so sánh này.",
        f"Không mất mốc OCR đang khớp: {len(audit['regressions'])} trường hợp thoái lui. Đây là khớp nguyên văn theo nhãn và có câu dùng lại cùng một số; không phải CER/WER hay chứng nhận mọi ô số chính xác.",
        f"Chấm đủ 40 đáp án: từ chối nhầm {q['false_refusals']}/32; từ chối đúng {q['correct_negative_refusals']}/8; đủ ý {q['reviewer_supplied_metrics']['fully_complete_rate']:.2%}; bám nguồn trọn vẹn {q['reviewer_supplied_metrics']['fully_supported_rate']:.2%} trong {q['reviewer_supplied_metrics']['source_support_denominator']} đáp án có nội dung.",'',
        '## Gemini và giới hạn','',
        f"{w['completed']}/40 câu được thực hiện; {w['valid_provider_responses']} phản hồi hợp lệ qua chuỗi, {answers['transport_generation_requests']} POST thực. p50 {w['p50_ms']/1000:.2f}s, p95 {w['p95_ms']/1000:.2f}s. Phản hồi hợp lệ gồm cả từ chối; không đồng nghĩa nội dung đúng.",
        'Cửa sổ tuần tự ngắn không chứng minh SLA hay độ ổn định dài hạn. Người viết và chấm đều là cùng trợ lý. Giữ nguyên giới hạn nhãn SAB: trang 10 có số nhưng gold chỉ chọn trang 11; không sửa gold để tăng điểm.',
        'Các ô số OCR còn bất đồng cần đối chiếu PDF. Cách đọc mới áp dụng khi nhập/xử lý lại PDF; thay mã không tự thay nội dung đã lưu của kho chính. Yêu cầu giữ đủ điều kiện trong prompt giảm bỏ sót nhưng không bảo đảm mọi câu hỏi tùy ý đều đầy đủ. Phiếu chấm lưu nguyên đáp án và nguồn.','']
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    check();print(json.dumps(quality,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
