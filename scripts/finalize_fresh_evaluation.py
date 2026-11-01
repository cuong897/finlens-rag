"""Publish measured results and explicitly supplied source-review judgments.

No model calls and no automatic semantic grading. First-wave answers alone are
graded; repeat waves measure operational behavior, never select a best answer.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path

from finlens.retrieval_dataset import fingerprint
from finlens.review_scores import score_reviews
from finlens.store import Store
from scripts.prepare_fresh_holdout import assert_frozen
from scripts.run_fresh_gemini_eval import summarize


def quality_breakdown(dataset, results, review):
    cases={c['id']:c for c in dataset['cases']}
    answers={r['id']:r['response'] for r in results if r['execution']=='completed'}
    grades={g['id']:g for g in review['scores']}
    if set(cases)!=set(answers) or set(cases)!=set(grades):
        raise ValueError('Do not publish partial or selected quality scores')
    positives=[i for i,c in cases.items() if c['expected_status']=='answered']
    negatives=[i for i in cases if i not in positives]
    answered=[i for i in positives if answers[i]['status']=='answered']
    false_refusals=[i for i in positives if answers[i]['status']=='insufficient_evidence' and not answers[i].get('generation_error')]
    safe_negatives=[i for i in negatives if answers[i]['status']=='insufficient_evidence' and grades[i]['abstention_correct']=='yes']
    refusals=[i for i in answers if answers[i]['status']=='insufficient_evidence']
    correct_positive=[i for i in positives if grades[i]['correctness']==2 and i in answered]
    by_company={}
    for company in sorted({c['company'] for c in cases.values()}):
        ids=[i for i in cases if cases[i]['company']==company]
        pos=[i for i in positives if i in ids]
        by_company[company]={'cases':len(ids),'fully_correct_including_negative':sum(grades[i]['correctness']==2 for i in ids),
            'answerable_cases':len(pos),'fully_correct_answerable':sum(i in correct_positive for i in pos),
            'false_refusals':sum(i in false_refusals for i in pos)}
    return {'cases':len(cases),'answerable':len(positives),'unsupported':len(negatives),
        'answers_supplied_on_answerable':len(answered),'fully_correct_answerable':len(correct_positive),
        'end_to_end_correct_answerable_rate':len(correct_positive)/len(positives),
        'conditional_correctness_on_supplied_answers':len(correct_positive)/len(answered) if answered else None,
        'false_refusals':len(false_refusals),'false_refusal_rate':len(false_refusals)/len(positives),
        'correct_negative_refusals':len(safe_negatives),'refusal_precision':len(safe_negatives)/len(refusals) if refusals else None,
        'negative_refusal_recall':len(safe_negatives)/len(negatives),
        'partial_answer_ids':[i for i in answered if grades[i]['correctness']==1],
        'provider_error_cases':sum(bool(a.get('generation_error')) for a in answers.values()),
        'by_company':by_company,'reviewer_supplied_metrics':review['quality_metrics']}


def repeat_summary(rows):
    grouped=defaultdict(list)
    for row in rows:grouped[row['id']].append(row)
    repeated={i:rs for i,rs in grouped.items() if len(rs)>1}
    return {'repeated_unique_cases':len(repeated),
        'cases_with_status_change':sum(len({r['response']['status'] for r in rs})>1 for rs in repeated.values()),
        'cases_with_exact_answer_text_change':sum(len({r['response']['answer'] for r in rs})>1 for rs in repeated.values()),
        'note':'Status or exact-text consistency is not semantic correctness. Only the first wave received complete semantic review.',
        'by_wave':{str(w):summarize([r for r in rows if r['wave']==w],planned=sum(r['wave']==w for r in rows)) for w in sorted({r['wave'] for r in rows})}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='artifacts/new-holdout-final-v1')
    args=parser.parse_args();root=Path(args.root)
    fixture=Path('finlens/fixtures')
    protocol=json.loads((fixture/'holdout_20261101_protocol.json').read_text(encoding='utf-8'))
    assert_frozen(protocol)
    dataset=json.loads((fixture/'holdout_answers_20261101.json').read_text(encoding='utf-8'))
    result=json.loads((root/'gemini/answer-check.json').read_text(encoding='utf-8'))
    stability=json.loads((root/'gemini/stability.json').read_text(encoding='utf-8'))
    retrieval=json.loads((root/'retrieval/retrieval-ablation.json').read_text(encoding='utf-8'))
    supplied=json.loads((root/'assistant-grades.json').read_text(encoding='utf-8'))
    if result['dataset_sha256']!=fingerprint(dataset) or stability['dataset_sha256']!=fingerprint(dataset):raise ValueError('Answer labels changed')
    if len(retrieval['results'])!=200 or len(stability['results'])!=100:raise ValueError('Protocol incomplete')
    output=root/'final';output.mkdir(exist_ok=False)
    review_csv=output/'answer-reviews.csv'
    fields=['id','reviewer','question','answer','sources','correctness','completeness','source_support','abstention_correct','notes']
    cases={c['id']:c for c in dataset['cases']}
    with review_csv.open('w',encoding='utf-8-sig',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for r in result['results']:
            c=cases[r['id']];a=r['response'];g=supplied['scores'][r['id']]
            writer.writerow(dict(zip(fields,[r['id'],supplied['reviewer'],c['question'],a['answer'],
                json.dumps(a['citations'],ensure_ascii=False),*g])))
    review=score_reviews(root/'gemini/answer-check.json',review_csv,fixture/'holdout_answers_20261101.json',output/'quality')
    quality=quality_breakdown(dataset,result['results'],review)
    repeats=repeat_summary(stability['results'])
    main_facts=fingerprint(Store('data').facts())
    if main_facts!=protocol['main_store_facts_sha256'] or Store(root/'data').facts():raise ValueError('Confirmed facts changed')
    source_registry=json.loads((fixture/'holdout_20261101_reports.json').read_text(encoding='utf-8'))
    input_paths=[fixture/'holdout_20261101_protocol.json',fixture/'holdout_20261101_reports.json',
        fixture/'holdout_retrieval_20261101.json',fixture/'holdout_targets_20261101.json',fixture/'holdout_answers_20261101.json',
        root/'preparation.json',root/'selection.json',root/'assistant-grades.json',root/'gemini/requests.jsonl',
        root/'gemini/stability.json',root/'gemini/answer-check.json',root/'retrieval/retrieval-ablation.json',
        review_csv,output/'quality/review-scores.json']
    summary={'version':1,'kind':'frozen_product_new_source_measurement','frozen_product_commit':protocol['frozen_product_commit'],
        'product_frozen_verified':True,'main_facts_unchanged':True,'main_facts_sha256':main_facts,
        'retrieval_dataset_sha256':fingerprint(json.loads((fixture/'holdout_retrieval_20261101.json').read_text(encoding='utf-8'))),
        'answer_dataset_sha256':fingerprint(dataset),'companies':[r['company'] for r in source_registry],
        'retrieval':{'cases':200,'summary':retrieval['summary'],'by_company':retrieval['by_company'],
            'missing_literal_evidence_cases':len({r['id'] for r in retrieval['visual_labels_missing_from_extracted_text']})},
        'quality':quality,'stability':{'summary':stability['summary'],'transport_generation_requests':stability['transport_generation_requests'],
            'first_completed_at':stability['results'][0]['recorded_at'],'last_completed_at':stability['results'][-1]['recorded_at'],
            'per_model_timeout_seconds':10,'chain_deadline_seconds':30,'repeats':repeats,
            'cooldown_skips':dict(Counter(a['model'] for r in stability['results'] for a in r['response']['generation_attempts'] if a['status']=='skipped'))},
        'regression_tests':{'command':'.venv/Scripts/python.exe -m unittest discover -s tests -q','passed':248},
        'limitations':['Same assistant authors and reviews labels; no independently verified human ground truth.',
            'Numeric targets and question families are reused; 200 questions are correlated, not 200 independent facts.',
            'The 40 answer cases cover accounting policies and unsupported questions, not numeric calculation accuracy.',
            'Company/year/scope are supplied; retrieval scoring does not execute the complete answer routing path.',
            '100 sequential requests reuse 40 cases in three nearby waves; not concurrency, multi-day availability or SLA.',
            '3.8 timeouts are under the current 10-second per-model policy; do not establish inability with a longer timeout.',
            'The isolated corpus contains no confirmed facts; no changes or new reports were added to the user main store.',
            'Any subsequent tuning informed by these results makes these sources development data for that change; require new untouched sources for another generalization claim.'],
        'evidence_sha256':{p.as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    # A small tracked audit record; PDFs, docs, keys and raw data remain ignored.
    (fixture/'holdout_20261101_result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    write_report(output/'REPORT.md',summary)
    assert_frozen(protocol)
    print(json.dumps({'quality':quality,'repeats':{k:v for k,v in repeats.items() if k!='by_wave'}},indent=2))


def write_report(path,s):
    q=s['quality'];st=s['stability'];m=st['summary'];pct=lambda x:f'{100*x:.2f}%'
    lines=['# FinLens — kiểm thử mới ngày 01/11/2026','',
        '**Kết luận: chưa đạt chất lượng tốt đồng đều trên dữ liệu mới.** Chuỗi dự phòng duy trì phản hồi API, nhưng nhập PDF, định tuyến câu hỏi và độ đầy đủ của câu trả lời còn lỗi. Không sửa sản phẩm để cải thiện điểm trong phép thử này.','',
        f"Mã sản phẩm khóa: `{s['frozen_product_commit']}`. Bốn báo cáo hợp nhất kiểm toán 2024 của SAB, MWG, BCM, VNM; 258 trang, 308 đoạn có embedding BGE-M3. Nạp toàn bộ PDF vào kho riêng, không chọn trang theo đáp án, không có fact đã xác nhận. Mã được kiểm tra SHA256 trước/sau; dữ liệu chính giữ nguyên.",'',
        '## 1. Giao thức và độ tin cậy','',
        '- Khóa kế hoạch trước phép đo; khóa 40 câu trước sinh đáp án; khóa 200 câu trước chấm truy xuất. Nhãn được commit f2da22e, câu trả lời d4a59bb. Không thay mã Python sản phẩm từ 0dd791c.',
        '- 200 câu: 4 × (16 dòng tài chính × 2 năm + 8 câu đa chỉ tiêu + 4 so sánh + 6 chính sách). Có 64 dòng chỉ tiêu gốc, dùng lại ở các biến thể. Mốc 2023 là cột so sánh trong báo cáo 2024, không phải tài liệu 2023 riêng.',
        '- 40 câu nội dung: 32 câu chính sách có đáp án trong PDF + 8 câu thiếu bằng chứng. Dùng chung 8 họ câu chính sách giữa các doanh nghiệp; không đại diện đầy đủ mọi câu hỏi tài chính.',
        '- 100 lượt tuần tự: 40 câu lần đầu, 40 câu đảo thứ tự lần hai, 20 câu MWG/BCM lần ba; cách 6 giây giữa lượt và 60 giây giữa đợt. Không chọn câu trả lời tốt nhất trong các lần thử.',
        '- Người viết nhãn và người chấm đều là trợ lý trong phiên này. Đã đối chiếu nguồn, nhưng không phải chấm độc lập bởi con người. Nguồn mới so với phát triển trong kho, không chứng minh mô hình chưa thấy các báo cáo công khai khi huấn luyện.',
        '- Đổi nguồn trước khi đo: MSN không truy cập được, thay BCM; MWG/VNM bản tóm tắt thiếu thuyết minh, thay bằng báo cáo kiểm toán đầy đủ. Tệp selection.json ghi lý do. BCM lỗi nhập liệu vẫn được giữ nguyên trong mẫu.','',
        '## 2. Truy xuất — 200 câu','',
        '| Phương án | Recall trang@5 | MRR@5 | Recall bằng chứng@5 | Đủ bằng chứng / 200 |',
        '| --- | ---: | ---: | ---: | ---: |']
    for name,v in s['retrieval']['summary'].items():lines.append(f"| {name} | {pct(v['recall_at_5'])} | {v['mrr_at_5']:.4f} | {pct(v['evidence_recall_at_5'])} | {v['evidence_complete_questions']} |")
    lines+=['','Bản hybrid_context phản ánh bộ xếp hạng ngữ cảnh trong sản phẩm; hybrid_rrf là phép đối chứng. Không đổi phương án sau khi thấy điểm. Recall trang lấy trung bình tỷ lệ các trang chuẩn xuất hiện trong 5 đoạn; MRR@5 dùng vị trí trang chuẩn đầu tiên. Đoạn trùng trang vẫn chiếm ngân sách. Bằng chứng là chuỗi trích dẫn đúng dấu, số và dấu phân cách trong cùng một đoạn; độ bao phủ chuỗi không chứng minh suy luận đúng.','',
        '| Doanh nghiệp | Số câu | Recall@5 (context) | MRR@5 | Đủ bằng chứng |','| --- | ---: | ---: | ---: | ---: |']
    for company,v in s['retrieval']['by_company'].items():
        v=v['hybrid_context'];lines.append(f"| {company} | {v['cases']} | {pct(v['recall_at_5'])} | {v['mrr_at_5']:.4f} | {v['evidence_complete_questions']}/50 |")
    lines+=['',f"Có {s['retrieval']['missing_literal_evidence_cases']}/200 câu có ít nhất một nhãn ảnh không còn nguyên văn trong văn bản trích xuất. Không loại khỏi mẫu. BCM đạt 0/50 vì PDF gồm nhiều dải ảnh nhỏ trên một trang; bộ nhận diện OCR hiện tại bỏ sót phần thân. Đọc OCR toàn trang riêng chỉ phục vụ gán nhãn, không đưa ngược vào chỉ mục. Đây là lỗi nhập liệu thực, không thể sửa chỉ bằng tăng top-k hay đổi Gemini.",'',
        '## 3. Chất lượng 40 đáp án đầu tiên','',
        '| Chỉ số | Kết quả |','| --- | ---: |',
        f"| Đúng hoàn toàn, gồm cả từ chối đúng | {q['reviewer_supplied_metrics']['fully_correct_rate']*40:.0f}/40 = {pct(q['reviewer_supplied_metrics']['fully_correct_rate'])} |",
        f"| Đúng hoàn toàn trên câu có đáp án trong PDF | {q['fully_correct_answerable']}/{q['answerable']} = {pct(q['end_to_end_correct_answerable_rate'])} |",
        f"| Đúng hoàn toàn khi đã cung cấp câu trả lời | {q['fully_correct_answerable']}/{q['answers_supplied_on_answerable']} = {pct(q['conditional_correctness_on_supplied_answers'])} |",
        f"| Đầy đủ hoàn toàn | {q['reviewer_supplied_metrics']['fully_complete_rate']*40:.0f}/40 = {pct(q['reviewer_supplied_metrics']['fully_complete_rate'])} |",
        f"| Được nguồn hỗ trợ hoàn toàn trong 18 đáp án có nội dung | {pct(q['reviewer_supplied_metrics']['fully_supported_rate'])} |",
        f"| Từ chối nhầm câu có đáp án | {q['false_refusals']}/{q['answerable']} = {pct(q['false_refusal_rate'])} |",
        f"| Từ chối đúng trên câu thiếu bằng chứng | {q['correct_negative_refusals']}/{q['unsupported']} |",
        f"| Precision của từ chối | {q['correct_negative_refusals']}/22 = {pct(q['refusal_precision'])} |",'',
        'Thang 0–2: 0 sai/thiếu đáp án cốt lõi; 1 đúng hoặc đủ một phần; 2 đúng/đủ hoàn toàn trong phạm vi được hỏi. Nguồn N/A cho 22 câu từ chối không lỗi provider, nên không dùng 22 câu đó để nâng tỷ lệ bám nguồn. Từ chối đúng nhưng lời đáp chung chung được điểm đầy đủ 1, không phải 2. Bảng chấm có nguyên câu trả lời, trích nguồn và ghi chú từng câu trong answer-reviews.csv.','',
        '| Doanh nghiệp | Đúng câu có đáp án | Từ chối nhầm |','| --- | ---: | ---: |']
    for company,v in q['by_company'].items():lines.append(f"| {company} | {v['fully_correct_answerable']}/{v['answerable_cases']} | {v['false_refusals']} |")
    lines+=['',
        'Hai lỗi nội dung được chấm một phần: SAB dịch weighted average thành “bình quân chia đều”; VNM lấy tỷ giá mua dành cho khoản phải thu rồi nói chung cho mọi khoản mục tiền tệ, bỏ thiếu tỷ giá bán cho nợ phải trả. 14 từ chối nhầm gồm bốn câu chính sách giá trị hàng tồn kho bị đưa sang đường tra số liệu, bảy câu BCM thiếu phần thân nguồn và ba câu SAB có nguồn nhưng chưa trả được đáp án. Không gộp các lỗi này thành lỗi kết nối API.','',
        '## 4. Độ ổn định Gemini — 100 lượt RAG','',
        f"100/100 lượt đã hoàn tất, không bỏ lượt. 10 lượt đi theo nhánh nội bộ và không gọi Gemini; **90/90 lượt có gọi Gemini nhận được phản hồi hợp lệ từ một mô hình trong chuỗi**. Có 102 yêu cầu sinh thực sự tới nhà cung cấp, 90 thành công và 12 thất bại; 90 phản hồi đều do mô hình dự phòng. Phản hồi hợp lệ có thể là từ chối, không đồng nghĩa đáp án đúng.",'',
        '| Mô hình | Thành công / thực gọi | Lỗi | p50 / p95 ms |','| --- | ---: | --- | ---: |']
    for model,v in m['per_model'].items():lines.append(f"| {model} | {v['succeeded']}/{v['attempts']} | {json.dumps(v['errors'])} | {v['p50_ms']:.0f} / {v['p95_ms']:.0f} |")
    first=datetime.fromisoformat(st['first_completed_at']);last=datetime.fromisoformat(st['last_completed_at'])
    lines+=['',
        f"p50 toàn luồng {m['p50_ms']/1000:.2f}s; p95 {m['p95_ms']/1000:.2f}s. Khoảng giữa lượt đầu hoàn tất và lượt cuối hoàn tất: {(last-first).total_seconds()/60:.2f} phút ({st['first_completed_at']} → {st['last_completed_at']}). Thời gian trong tiến trình gồm truy xuất và sinh; không gồm giao diện/HTTP phía ứng dụng, không tính thời gian ngủ giữa lượt vào latency.",
        'Flash 3.8 được thử 5 lần với giới hạn hiện hành 10 giây/mô hình và đều timeout; 85 lượt khác bị bỏ qua do cooldown. Không diễn giải thành 0/90 hay khẳng định mô hình không thể trả lời khi chờ lâu hơn. Flash 3.6 có 4 lỗi giới hạn tốc độ và 3 lỗi dịch vụ; cooldown bỏ qua thêm 64 lượt. Chuỗi dự phòng đã che được các lỗi này trong cửa sổ đo. p50/p95 mỗi mô hình tính trên toàn bộ lần thử, gồm cả lỗi.',
        f"Có {m['attempts_without_total_tokens']} lần thử không có tổng token trả về; tổng được báo cáo là {m['reported_usage_totals']['totalTokenCount']} token. Không nội suy token hay tiền cho lượt timeout; không xác nhận hóa đơn tài khoản bằng dữ liệu này.",
        f"Trong {st['repeats']['repeated_unique_cases']} câu có thử lặp, {st['repeats']['cases_with_status_change']} câu đổi trạng thái trả lời/từ chối; {st['repeats']['cases_with_exact_answer_text_change']} câu đổi nguyên văn. Đây không phải tỷ lệ nhất quán ngữ nghĩa. Ví dụ VNM tỷ giá cuối kỳ vẫn nói quá rộng ở lần hai, còn SAB bình quân gia quyền đổi cách diễn đạt giữa hai mô hình. Chỉ 40 đáp án đợt đầu được chấm nội dung đầy đủ.",
        'Phép thử tuần tự trong thời gian ngắn, có dùng lại câu hỏi và cơ chế cooldown; không phải 100 quan sát độc lập, không chứng minh SLA, tải đồng thời hoặc độ ổn định nhiều ngày.','',
        '## 5. Việc cần sửa sau phép đo','',
        '1. Nhận diện trang scan bằng tổng vùng ảnh/độ phủ trang thay vì chỉ từng ảnh lớn; thêm cổng phát hiện trang trống trước khi công bố nhập thành công.',
        '2. Phân biệt câu hỏi chính sách kế toán với yêu cầu lấy số liệu; không đòi fact đã xác nhận cho câu giải thích “giá trị hàng tồn kho”.',
        '3. Cải thiện truy xuất chính sách song ngữ và kiểm tra phạm vi mệnh đề; đặc biệt tài sản/phải thu/nợ phải trả, không chỉ kiểm tra trích dẫn có tồn tại.',
        '4. Đo riêng timeout dài hơn cho Flash 3.8 trước khi quyết định thời gian chờ hoặc ưu tiên mô hình; không đánh đổi độ trễ chỉ bằng phỏng đoán.',
        '5. Sau khi sửa, coi bốn doanh nghiệp này là dữ liệu phát triển cho thay đổi đó. Đo lại hồi quy trên chúng và dùng báo cáo chưa xem khác để xác nhận khả năng tổng quát hóa.','',
        '## 6. Kiểm chứng và tái lập','',
        '- 248 kiểm thử unittest đã chạy và đạt. Mọi tệp Python sản phẩm giữ đúng SHA256, 59 fact trong kho chính giữ đúng fingerprint; kho thử có 0 fact.',
        '- Nhãn: finlens/fixtures/holdout_{retrieval,answers,targets}_20261101.json; registry nguồn holdout_20261101_reports.json; kế hoạch holdout_20261101_protocol.json.',
        '- Công cụ: scripts/prepare_fresh_holdout.py, scripts/run_fresh_retrieval_eval.py, scripts/run_fresh_gemini_eval.py, scripts/finalize_fresh_evaluation.py. Chuẩn bị chỉ mục có thể lâu do OCR; không chạy lại sinh chỉ để chọn kết quả đẹp hơn.',
        '- Dữ liệu gốc và kết quả đầy đủ trong artifacts/new-holdout-final-v1; các tệp báo cáo/dữ liệu/PDF/.env tiếp tục bị Git bỏ qua theo yêu cầu. JSON tóm tắt kiểm toán được lưu cùng mã. Không có API key trong bộ hồ sơ.',
        '- Cấu hình phiên bản và phần cứng được ghi riêng ở environment.json và hardware.json: Python 3.13.2 trên Windows 11, Ryzen 9 8945HS, RAM khoảng 15,29 GiB; BGE-M3 566,7M F16, vector 1024 chiều, digest được lưu. Danh sách GPU là thiết bị có trên máy, không phải phép đo GPU thực sự đã sử dụng. Gemini dùng maxOutputTokens 2048, thinking LOW, JSON schema; không cấu hình temperature, nên dùng mặc định nhà cung cấp.',
        '- Điểm này là đường cơ sở trên dữ liệu mới của bản 0dd791c. Không trộn với điểm các benchmark cũ từng dùng để sửa mô hình truy xuất, và không lấy “tỷ lệ API trả về 100%” làm độ chính xác nội dung.','']
    path.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':main()
