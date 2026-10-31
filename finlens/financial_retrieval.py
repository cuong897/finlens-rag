"""Bilingual financial concepts and source-row ranking; no answer labels or values."""

import re
from functools import lru_cache
from finlens.text import normalize, tokens

# Domain vocabulary is independent of company, year, amount and benchmark pages.
# key | Vietnamese phrases | English row labels | default statement
_CATALOG = """
current_assets|tai san ngan han|current assets|balance
noncurrent_assets|tai san dai han|non current assets;long term assets|balance
cash_eq|tien va tuong duong tien;tien va cac khoan tuong duong tien|cash and cash equivalents|balance
cash|tien mat;khoan tien khong gom;cash excluding|cash|balance
short_invest|dau tu tai chinh ngan han;dau tu ngan han|short term financial investments;short term investment|balance
receivables|tong phai thu ngan han;tong cac khoan phai thu ngan han|short term receivables;accounts receivable short term|balance
trade_receivables|phai thu khach hang;phai thu thuong mai|trade accounts receivable;accounts receivable from customers;trade receivables|balance
prepayments|tra truoc cho nguoi ban|prepayments to suppliers|balance
inventory|hang ton kho thuan;hang ton kho sau du phong;hang ton kho|inventories;inventory|balance
fixed_assets|tai san co dinh|fixed assets|balance
construction|xay dung co ban do dang|construction in progress|balance
total_assets|tong tai san|total assets|balance
liabilities|no phai tra|liabilities;total liabilities|balance
current_liabilities|no ngan han|current liabilities;short term liabilities|balance
long_liabilities|no dai han|non current liabilities;long term liabilities|balance
short_borrowings|vay ngan han|short term borrowings|balance
long_borrowings|vay dai han|long term borrowings|balance
equity|von chu so huu|owners equity;owner s equity;equity|balance
retained|loi nhuan giu lai;loi nhuan chua phan phoi|retained profits;undistributed earnings;retained earnings|balance
gross_revenue|doanh thu ban hang va cung cap dich vu truoc;doanh thu truoc giam tru|revenue from sales of goods;revenue from sale of goods|income
deductions|cac khoan giam tru doanh thu;giam tru doanh thu|revenue deductions;less deduction;sales deductions|income
revenue|doanh thu thuan;doanh so thuan;net sales|net revenue|income
cost_sales|gia von hang ban|cost of sales;cost of goods sold and services rendered|income
gross_profit|loi nhuan gop|gross profit|income
financial_income|doanh thu tai chinh;doanh thu hoat dong tai chinh|financial income;finance income|income
financial_expenses|chi phi tai chinh|financial expenses;finance costs|income
interest_expense|chi phi lai vay|interest expense;borrowing costs|income
selling|chi phi ban hang|selling expenses|income
admin|chi phi quan ly doanh nghiep|general and administration expenses;general and administrative expenses|income
pretax|loi nhuan ke toan truoc thue;loi nhuan truoc thue|accounting profit before tax;profit before tax|income
net_income|loi nhuan sau thue;net income|net profit after tax;profit after tax|income
operating_profit|loi nhuan thuan tu hoat dong kinh doanh|net operating profit;net operating profit from operating activities|income
depreciation|khau hao|depreciation and amortisation;depreciation and amortization|cashflow
interest_paid|tien lai vay da tra|interest paid|cashflow
tax_paid|thue thu nhap doanh nghiep da nop|income tax paid;cit paid;corporate income tax paid|cashflow
cfo|dong tien kinh doanh;dong tien thuan tu hoat dong kinh doanh;luu chuyen tien thuan tu hoat dong kinh doanh;operating cash flow|net cash flows from operating activities;net cash inflows from operating activities;net cash generated from operating activities|cashflow
capex|chi mua sam xay dung tai san;chi mua sam tai san;chi mua sam xay dung|payments for additions to fixed assets;purchases of fixed assets;payments for purchases of fixed assets|cashflow
cfi|dong tien thuan tu hoat dong dau tu;luu chuyen tien thuan tu hoat dong dau tu|net cash flows from investing activities;net cash outflows from investing activities;net cash used in investing activities|cashflow
borrowings_received|tien thu tu di vay|proceeds from borrowings|cashflow
cff|dong tien thuan tu hoat dong tai chinh;luu chuyen tien thuan tu hoat dong tai chinh|net cash flows from financing activities;net cash inflows outflows from financing activities;net cash generated from financing activities|cashflow
goods_transit|hang mua dang di duong|goods in transit|notes
raw_materials|nguyen vat lieu|raw materials|notes
finished_goods|thanh pham|finished goods|notes
merchandise|gia goc hang hoa|merchandise inventories;merchandise|notes
goods_sales|doanh thu ban hang hoa|sales of goods|notes
services_sales|doanh thu cung cap dich vu|provision of services;revenue from rendering of services|notes
deposit_interest|lai tien gui|interest income from deposits;interest income|notes
fx_loss|lo chen lech ty gia da thuc hien|realised foreign exchange losses;realized foreign exchange losses|notes
transport|chi phi van chuyen|transportation expenses;transportation costs|notes
audit_fee|phi kiem toan|financial statements audit fee;audit and non audit services performed by the auditor;audit fees|notes
lease_prepaid|khoan thue hoat dong|operating leases|notes
jewelry_revenue|doanh thu thuan tu ban trang suc|net revenue from sales of jewelry|notes
jewelry_cost|gia von trang suc|cost of jewelry sold|notes
selling_rent|chi phi thue|rental fee;rent expenses|notes
marketing|quang cao va tiep thi;chi phi quang cao|advertising and marketing;advertising expenses|notes
staff_cost|chi phi nhan vien|staff costs;employee costs|notes
materials_cost|chi phi nguyen vat lieu|raw materials;raw material costs|notes
"""
CONCEPTS = []
for _row in _CATALOG.strip().splitlines():
    _key, _vi, _en, _kind = _row.split("|")
    CONCEPTS.append({"key": _key, "vi": _vi.split(";"), "labels": _en.split(";"), "kind": _kind})


@lru_cache(maxsize=12000)
def clean(text):
    return " ".join(tokens(text))


def contains(text, phrase):
    return f" {phrase} " in f" {text} "


def profile(query):
    q = clean(query)
    causal_text = q.replace("theo yeu to", "")
    if any(contains(causal_text, phrase) for phrase in ("vi sao", "tai sao", "nguyen nhan", "giai thich", "nho dau", "yeu to", "dong luc", "why", "reason", "explain", "drivers")):
        return []
    matches = []
    for concept in CONCEPTS:
        phrases = concept["vi"] + concept["labels"]
        spans = [(m.start(), m.end(), concept) for phrase in phrases for m in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", q)]
        matches.extend(spans)
    chosen, occupied = [], []
    for start, end, concept in sorted(matches, key=lambda item: (-(item[1] - item[0]), item[0])):
        if any(start < b and end > a for a, b in occupied):
            continue
        if concept["key"] not in {c["key"] for c in chosen}:
            chosen.append(dict(concept))
        occupied.append((start, end))
    # Negation is meaningful for cash versus cash equivalents.
    if "khong gom tuong duong tien" in q:
        chosen = [c for c in chosen if c["key"] != "cash_eq"]
    explicit_notes = "thuyet minh" in q or "notes to" in q
    explicit_kinds = set()
    if "bang can doi" in q or "balance sheet" in q: explicit_kinds.add("balance")
    if "ket qua kinh doanh" in q or "ket qua hoat dong kinh doanh" in q or "income statement" in q: explicit_kinds.add("income")
    if "luu chuyen tien te" in q or "cash flow statement" in q: explicit_kinds.add("cashflow")
    for c in chosen:
        if explicit_notes: c["kind"] = "notes"
        elif len(explicit_kinds) == 1: c["kind"] = next(iter(explicit_kinds))
    return chosen


def expansion(query):
    return " ".join(label for concept in profile(query) for label in concept["labels"][:2])


def statement_kinds(text):
    header = " ".join(text[:1500].split())
    # Repeated side navigation is title case; actual headings are usually uppercase.
    if "NOTES TO THE" in header or "NOTES TO CONSOLIDATED" in header:
        return {"notes"}
    kinds = set()
    for kind, patterns in {
        "balance": ["BALANCE SHEET", "STATEMENT OF FINANCIAL POSITION"],
        "income": ["INCOME STATEMENT", "STATEMENT OF INCOME", "STATEMENT OF PROFIT"],
        "cashflow": ["CASH FLOW STATEMENT", "STATEMENT OF CASH FLOWS"],
    }.items():
        if any(p in header for p in patterns): kinds.add(kind)
    if not kinds:
        normalized = clean(header)
        if "notes" in normalized or "thuyet minh" in normalized: return {"notes"}
        if "bang can doi" in normalized or "balance sheet" in normalized: kinds.add("balance")
        if "ket qua" in normalized or "income statement" in normalized or "statement of income" in normalized: kinds.add("income")
        if "luu chuyen tien te" in normalized or "cash flow statement" in normalized or "statement of cash flows" in normalized: kinds.add("cashflow")
    return kinds


def label_strength(text, concept):
    lines = [clean(line) for line in text.splitlines() if line.strip()]
    best = 0.0
    for line in lines:
        if not re.search(r"\d", line): continue
        for label in concept["labels"]:
            if not contains(line, label): continue
            # Avoid a broader/sibling row being mistaken for the requested item.
            if concept["key"] == "cash" and ("equivalent" in line or "flows" in line): continue
            if concept["key"] == "current_assets" and ("non current" in line or "other current" in line): continue
            if concept["key"] == "liabilities" and any(x in line for x in ("current liabilities", "term liabilities", "other liabilities")): continue
            if concept["key"] == "gross_revenue" and ("net revenue" in line or "gross profit" in line): continue
            if concept["key"] == "goods_sales" and ("net revenue" in line or "cost of" in line): continue
            if concept["key"] == "retained" and ("previous years" in line or "current year" in line or "brought forward" in line): continue
            if concept["key"] == "inventory" and ("allowance" in line or "provision" in line or "change in" in line): continue
            before = line.split(label, 1)[0].strip()
            exact = not before or bool(re.fullmatch(r"[0-9 ]+", before))
            best = max(best, 1.0 + .25 * exact + .02 * min(8, len(label.split())))
    if not best and any(contains(clean(text), label) for label in concept["labels"]):
        best = .25
    return best


def rank_rows(query, hits, page_texts, limit=5):
    concepts = profile(query)
    if not concepts: return hits[:limit]
    ranked = []
    for rank, hit in enumerate(hits):
        kinds = statement_kinds(hit["text"])
        if not kinds: kinds = statement_kinds(page_texts.get((hit["document_id"], hit["page"]), ""))
        strengths = {}
        for c in concepts:
            strength = label_strength(hit["text"], c)
            if strength:
                strengths[c["key"]] = strength * (3 if c["kind"] in kinds else .6 if kinds else 1)
        quality = max(strengths.values(), default=0)
        score = quality + .12 / (1 + rank)
        ranked.append({"hit": hit, "strengths": strengths, "score": score})
    selected, covered, page_counts, seen = [], set(), {}, set()
    while ranked and len(selected) < limit:
        def priority(row):
            gain = sum(v for key, v in row["strengths"].items() if key not in covered)
            return gain + row["score"]
        ranked.sort(key=priority, reverse=True)
        row = ranked.pop(0); hit = row["hit"]
        page = (hit["document_id"], hit["page"])
        signature = clean(hit["text"])
        new = {key for key,v in row["strengths"].items() if v >= 3 and key not in covered}
        if signature in seen or (page_counts.get(page, 0) >= 1 and not new): continue
        selected.append({**hit, "context_rank": 2 if max(row["strengths"].values(), default=0) >= 3 else 1 if row["strengths"] else 0})
        seen.add(signature); covered.update(key for key,v in row["strengths"].items() if v >= 3)
        page_counts[page] = page_counts.get(page, 0) + 1
    return selected
