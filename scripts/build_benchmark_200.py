"""Build the fixed 200-case retrieval set from reviewed source-table rows.

No retrieval code, model, generated answer or retrieval result is used here.
Run: python -m scripts.build_benchmark_200 [--check]
"""
import argparse
import json
from pathlib import Path

from finlens.benchmark import reference_integer
from finlens.retrieval_dataset import fingerprint, validate_cases

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "finlens/fixtures"
PRIOR = ("total_assets", "cash", "inventory", "liabilities", "equity", "revenue", "pat", "cfo")
COMPARE = ("revenue", "pat", "cfo", "financial_income")
MULTI = {
    "liquidity": ("current_assets", "current_liabilities"),
    "leverage": ("liabilities", "equity"),
    "margin": ("revenue", "cogs"),
    "profit": ("pbt", "pat"),
    "cashflows": ("cfo", "cfi", "cff"),
    "assets": ("total_assets", "noncurrent_assets"),
    "cash_profit": ("cfo", "pat"),
    "operating_costs": ("selling", "admin"),
}


def section(row):
    return {"balance": "bảng cân đối kế toán", "income": "báo cáo kết quả kinh doanh",
            "cashflow": "báo cáo lưu chuyển tiền tệ", "note_balance": "thuyết minh báo cáo tài chính",
            "note_flow": "thuyết minh chi phí theo yếu tố"}[row["kind"]]


def build_dataset():
    ledger = json.loads((FIXTURES / "benchmark_targets_v3.json").read_text(encoding="utf-8"))
    reports = json.loads((FIXTURES / "benchmark_reports_v3.json").read_text(encoding="utf-8"))
    cases = []
    for company_index, report in enumerate(reports):
        company = report["company"]
        rows = ledger["targets"][company]
        lookup = {r["id"]: r for r in rows}

        def add(suffix, question, category, requests):
            evidence, answers, target_ids = [], [], []
            for key, year in requests:
                row = lookup[key]
                raw = row["values"][str(year)]
                target = f"{key}:{year}"
                target_ids.append(target)
                evidence.append({"page": row["page"], "quotes": [row["label"], raw]})
                answer = {"target": target, "year": year, "raw_value": raw,
                          "value": reference_integer(raw), "unit": "one", "currency": "VND"}
                if row["kind"] in {"balance", "note_balance"}:
                    answer["as_of"] = "2024-12-31" if year == 2024 else "2024-01-01"
                answers.append(answer)
            cases.append({"id": f"{company.lower()}-v3-{suffix}", "company": company,
                          "report_year": report["year"], "scope": report["scope"],
                          "filename": report["filename"], "source_sha256": report["sha256"],
                          "question": f"{company} (hợp nhất): {question}", "category": category,
                          "target_ids": target_ids, "gold_pages": sorted({a["page"] for a in evidence}),
                          "evidence": evidence, "reference_answers": answers, "evidence_origin": "rendered_pdf",
                          "annotation": {"reviewed_on": "2026-10-31", "reviewer": "assistant",
                                         "method": "Original PDF pages rendered and visually checked; source labels, number columns, signs and VND units transcribed before first retrieval scoring.",
                                         "human_review_status": "pending",
                                         "number_policy": "Original printed separators/signs; integer VND. Opening 01/01/2024 is the comparative 2023 year-end balance."}})

        for i, row in enumerate(rows):
            label = row["question_label_vi"]
            period = "tại ngày 31/12/2024" if row["kind"] in {"balance", "note_balance"} else "trong năm 2024"
            table = section(row)
            templates = [f"Theo {table}, {label} {period} là bao nhiêu?",
                         f"Tra {label} {period} trong {table}, kèm nguồn.",
                         f"Hãy tìm giá trị {label} {period} được trình bày ở {table}."]
            add(row["id"], templates[(i + company_index) % 3],
                "note_lookup" if row["kind"].startswith("note_") else "statement_lookup", [(row["id"], 2024)])
        for key in PRIOR:
            row = lookup[key]
            period = "ở cột đầu năm 01/01/2024 (số cuối năm 2023)" if row["kind"] == "balance" else "ở cột năm 2023"
            add(f"prior-{key}", f"Trong báo cáo năm 2024, {row['question_label_vi']} {period} trên {section(row)} là bao nhiêu?",
                "prior_year_lookup", [(key, 2023)])
        for name, keys in MULTI.items():
            labels = [lookup[k]["question_label_vi"] for k in keys]
            listing = ", ".join(labels[:-1]) + " và " + labels[-1]
            period = "tại cuối năm 2024" if all(lookup[k]["kind"] == "balance" for k in keys) else "của năm 2024"
            add(f"multi-{name}", f"Nêu đồng thời {listing} {period}, dẫn nguồn cho từng giá trị.",
                "multi_value", [(key, 2024) for key in keys])
        for key in COMPARE:
            row = lookup[key]
            add(f"compare-{key}", f"Đối chiếu {row['question_label_vi']} năm 2024 với năm 2023 trong {section(row)}: nêu cả hai số ở hai cột.",
                "year_comparison", [(key, 2024), (key, 2023)])
    validate_cases(cases, strict=True)
    return {"version": 1, "name": "finlens-new-company-holdout-v3-200", "split": "test",
            "label_origin": "Assistant-authored from original PDF tables rendered and checked on 2026-10-31; no independent human adjudicator. Fixed retrieval/parser from 4eff99a; labels committed before first scoring.",
            "protocol": {"version": 3, "frozen_implementation_commit": "4eff99a",
                         "previously_evaluated_companies": ["FPT", "CMG", "ELC", "HPG", "PNJ", "VHC", "DHG"],
                         "test_companies": [r["company"] for r in reports],
                         "design": "Four new companies x 50 questions: 22 statement + 8 note + 8 prior-column + 8 multi-value + 4 year-comparison. 120 distinct company/metric rows; cases share source pages and targets.",
                         "source_policy": "Complete 2024 consolidated reports, official issuer PDFs, 427 pages; no gold-page filtering. GMD audited financial statements, other three annual reports. Source choice by size/accessibility/labelability before scores.",
                         "question_policy": "Structured Vietnamese question templates over distinct reviewed rows; not sampled production conversations. Multi-value/comparison cases deliberately reuse scalar targets, not independent observations.",
                         "metric_policy": "Existing top-5 chunk page-reference recall/MRR and strict literal anchor coverage unchanged. Original PDF visual labels missing in parsed text remain in denominator and score zero for missing anchors.",
                         "answer_policy": "VND integers with original printed signs; 2023 balance answers use opening 01/01/2024 column. Retrieval only, no arithmetic or generation accuracy evaluated.",
                         "targets_sha256": fingerprint(ledger), "generation_evaluated": False,
                         "independent_human_adjudication": False},
            "reports": reports, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the versioned dataset matches the transcription ledger")
    args = parser.parse_args()
    dataset = build_dataset()
    path = FIXTURES / "benchmark_holdout_v3_200.json"
    if args.check:
        if dataset != json.loads(path.read_text(encoding="utf-8")):
            raise SystemExit("Versioned dataset differs from the source-row ledger")
    else:
        path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(dataset['cases'])} cases; dataset SHA-256 {fingerprint(dataset)}")


if __name__ == "__main__":
    main()
