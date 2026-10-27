"""Financial arithmetic never delegates computation to a language model."""

from decimal import Decimal, localcontext

from finlens.models import METRICS, UNITS, UNIT_LABELS
from finlens.store import Store


class EvidenceError(ValueError):
    pass


def fact_citation(fact: dict, label: str):
    return {"id": label, "document_id": fact["document_id"], "page": fact["page"],
            "company": fact["company"], "report_year": fact["report_year"],
            "filename": fact["filename"], "is_demo": bool(fact["is_demo"]),
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
