"""Financial arithmetic never delegates computation to a language model."""

from decimal import Decimal, localcontext

from finlens.models import METRICS, UNITS, UNIT_LABELS
from finlens.store import Store


class EvidenceError(ValueError):
    pass


DEBT_RATIOS = (
    ("current_ratio", "Hệ số thanh toán hiện hành", "current_assets", "current_liabilities", "lần", 1),
    ("operating_cash_coverage", "Dòng tiền kinh doanh / nợ ngắn hạn cuối kỳ", "operating_cash_flow", "current_liabilities", "%", 100),
    ("liabilities_to_assets", "Nợ phải trả / tổng tài sản", "liabilities", "total_assets", "%", 100),
)


def debt_capacity(store, company, years, scope="consolidated", dataset="real"):
    """Supported liquidity proxies, never a fabricated debt-service ratio.

    Each ratio requires complete, non-conflicting verified inputs for every
    requested year. Currency, unit and corpus checks apply to all components.
    """
    if not 1 <= len(years) <= 2:
        raise EvidenceError("Hãy chọn một hoặc hai năm để phân tích khả năng trả nợ.")
    citations, source_ids, ratios, missing = [], {}, [], []
    def cite(fact):
        key = fact["id"]
        if key not in source_ids:
            source_ids[key] = f"F{len(citations) + 1}"
            citations.append(fact_citation(fact, source_ids[key]))
        return source_ids[key]

    for key, label, numerator, denominator, unit, scale in DEBT_RATIOS:
        values, problems = [], []
        for year in sorted(years):
            inputs = []
            for metric in (numerator, denominator):
                try:
                    inputs.append(select_fact(store, company, metric, year, scope, dataset))
                except EvidenceError as exc:
                    problems.append(str(exc))
            if len(inputs) != 2:
                continue
            left, right = inputs
            if left["currency"] != right["currency"] or bool(left["is_demo"]) != bool(right["is_demo"]):
                problems.append(f"Không tính {label} năm {year}: khác tiền tệ hoặc trộn dữ liệu thật và giả lập.")
                continue
            with localcontext() as ctx:
                ctx.prec = 50
                top = Decimal(left["value"]) * Decimal(UNITS[left["unit"]])
                bottom = Decimal(right["value"]) * Decimal(UNITS[right["unit"]])
                if bottom <= 0:
                    problems.append(f"Không tính {label} năm {year}: mẫu số phải lớn hơn 0.")
                    continue
                value = top / bottom * scale
                displayed = format(value.quantize(Decimal("0.01")), "f")
            values.append({"year": year, "value": displayed,
                           "numerator": format(top, "f"), "denominator": format(bottom, "f"),
                           "currency": left["currency"], "is_demo": bool(left["is_demo"]), "_facts": inputs})
        if len({(v["currency"], v["is_demo"]) for v in values}) > 1:
            problems.append(f"Không so sánh {label}: hai kỳ khác tiền tệ hoặc nguồn thật và giả lập.")
        if problems:
            missing.extend(problems)
            continue
        for value in values:
            value["sources"] = [cite(fact) for fact in value.pop("_facts")]
            value.pop("is_demo")
        ratios.append({"metric": key, "label": label, "unit": unit, "values": values,
                       "formula": f"{METRICS[numerator]} / {METRICS[denominator]}" + (" × 100" if scale == 100 else "")})
    return {"ratios": ratios, "citations": citations, "missing_inputs": list(dict.fromkeys(missing))}


def fact_citation(fact: dict, label: str):
    return {"id": label, "document_id": fact["document_id"], "page": fact["page"],
            "company": fact["company"], "report_year": fact["report_year"],
            "filename": fact["filename"], "is_demo": bool(fact["is_demo"]),
            "period_label": fact.get("period_label"),
            "url": f"/api/documents/{fact['document_id']}/pages/{fact['page']}",
            "excerpt": f"{METRICS[fact['metric']]} ({fact['year']}): {fact['value']} {UNIT_LABELS[fact['unit']]} {fact['currency']}. {fact['note']}"}


def select_fact(store: Store, company: str, metric: str, year: int, scope: str, dataset: str):
    facts = [f for f in store.facts(company, scope, dataset, metric, [year]) if f["verified"]]
    if not facts:
        raise EvidenceError(f"Chưa có {METRICS[metric]} năm {year} được xác nhận trong phạm vi đã chọn.")
    with localcontext() as ctx:
        ctx.prec = 50
        values = {(Decimal(f["value"]) * Decimal(UNITS[f["unit"]]), f["currency"]) for f in facts}
    if len(values) != 1:
        raise EvidenceError(f"Có số liệu {METRICS[metric]} năm {year} mâu thuẫn hoặc khác tiền tệ. Cần đối chiếu các nguồn trước.")
    # Prefer the newest report only after all reported values agree.
    return sorted(facts, key=lambda f: (-f["report_year"], f["id"]))[0]


def compare(store: Store, company: str, metric: str, from_year: int, to_year: int,
            scope: str = "consolidated", dataset: str = "real"):
    if from_year >= to_year:
        raise EvidenceError("Năm gốc phải nhỏ hơn năm so sánh.")
    before = select_fact(store, company, metric, from_year, scope, dataset)
    after = select_fact(store, company, metric, to_year, scope, dataset)
    if before["currency"] != after["currency"]:
        raise EvidenceError("Hai kỳ khác tiền tệ; hệ thống chưa chuyển đổi tỷ giá.")
    if bool(before["is_demo"]) != bool(after["is_demo"]):
        raise EvidenceError("Không trộn số liệu giả lập với số liệu tài liệu thật.")
    with localcontext() as ctx:
        ctx.prec = 50
        scale = Decimal(UNITS[before["unit"]])
        left = Decimal(before["value"])
        right = Decimal(after["value"]) * Decimal(UNITS[after["unit"]]) / scale
        delta = right - left
        percentage = (delta / left * 100).quantize(Decimal("0.01")) if left > 0 else None
    note = None if percentage is not None else "Không tính phần trăm khi giá trị kỳ gốc bằng 0 hoặc âm; chỉ hiển thị chênh lệch tuyệt đối."
    return {"company": company, "metric": metric, "label": METRICS[metric],
            "from_year": from_year, "to_year": to_year, "from_value": format(left, "f"),
            "to_value": format(right, "f"), "delta": format(delta, "f"),
            "growth_percent": str(percentage) if percentage is not None else None,
            "unit": before["unit"], "unit_label": UNIT_LABELS[before["unit"]],
            "currency": before["currency"], "scope": scope, "is_demo": bool(before["is_demo"]),
            "formula": f"({format(right, 'f')} − {format(left, 'f')}) / {format(left, 'f')} × 100" if percentage is not None else None,
            "note": note, "citations": [fact_citation(before, "F1"), fact_citation(after, "F2")]}
