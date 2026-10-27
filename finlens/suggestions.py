"""Conservative, local drafts from aligned PDF rows; never confirmed facts."""

from collections import defaultdict
from decimal import Decimal
import re
import statistics

import pymupdf

from finlens.models import METRICS
from finlens.text import tokens


LABELS = {
    "revenue": ["doanh thu thuan", "net revenue from sales of goods and rendering of services", "net revenue"],
    "net_income": ["loi nhuan sau thue", "profit after tax", "profit after corporate income tax"],
    "total_assets": ["tong tai san", "total assets"],
    "current_assets": ["tai san ngan han", "current assets"],
    "liabilities": ["no phai tra", "liabilities", "total liabilities"],
    "current_liabilities": ["no ngan han", "short term liabilities", "current liabilities"],
    "equity": ["von chu so huu", "owners equity", "owner s equity"],
    "cash": ["tien va cac khoan tuong duong tien", "tien va tuong duong tien", "cash and cash equivalents"],
    "receivables": ["phai thu ngan han cua khach hang", "phai thu khach hang", "short term trade accounts receivable"],
    "inventory": ["hang ton kho", "inventories"],
    "gross_profit": ["loi nhuan gop ve ban hang va cung cap dich vu", "loi nhuan gop", "gross profit from sales of goods and rendering of services"],
    "interest_expense": ["chi phi lai vay", "interest expense"],
    "operating_cash_flow": ["luu chuyen tien thuan tu hoat dong kinh doanh", "net cash inflows from operating activities", "net cash flows from operating activities"],
}
NOTICE = "Đề xuất chưa được xác nhận. Hãy đối chiếu dòng, cột năm, đơn vị và phạm vi với PDF gốc trước khi lưu. Không tự đưa vào phép tính."


def clean(text):
    return " ".join(tokens(text))


def number_format(words):
    # A single 1,234 is ambiguous. Learn grouping only from values containing
    # at least two identical thousands separators on this page.
    separators = set()
    for word in words:
        raw = word[4].strip("()-−")
        for separator in (",", "."):
            if re.fullmatch(r"\d{1,3}(?:" + re.escape(separator) + r"\d{3}){2,}", raw):
                separators.add(separator)
    return next(iter(separators)) if len(separators) == 1 else None


def parse_amount(raw, grouping=None):
    text = raw.strip().replace("−", "-")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    for separator in (",", "."):
        if re.fullmatch(r"-?\d{1,3}(?:" + re.escape(separator) + r"\d{3}){2,}", text):
            grouping = separator
    if re.fullmatch(r"-?\d{1,24}", text):
        pass
    elif grouping and re.fullmatch(r"-?\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+(?:" + (r"\." if grouping == "," else ",") + r"\d{1,8})?", text):
        text = text.replace(grouping, "").replace(",", ".")
    elif re.fullmatch(r"-?\d+[.,]\d{1,2}", text):
        text = text.replace(",", ".")
    else:
        return None
    if negative:
        if text.startswith("-"):
            return None
        text = "-" + text
    if not re.fullmatch(r"-?\d{1,24}(?:\.\d{1,8})?", text):
        return None
    return format(Decimal(text), "f")


def rows_from_words(words):
    rows = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        center = (word[1] + word[3]) / 2
        tolerance = max(2, (word[3] - word[1]) * 0.2)
        if rows and abs(center - rows[-1]["y"]) <= tolerance:
            rows[-1]["words"].append(word)
        else:
            rows.append({"y": center, "words": [word]})
    for row in rows:
        row["words"].sort(key=lambda w: w[0])
    return rows


def currency_unit(rows, header, height):
    nearby = [row for row in rows if header["y"] - 6 * height <= row["y"] <= header["y"] + 2 * height]
    lines = [clean(" ".join(word[4] for word in row["words"])) for row in nearby]
    text = " ".join(lines)
    currencies = {code for code in ("VND", "USD", "EUR") if code.lower() in text.split()}
    if re.search(r"\b(?:don vi(?: tinh)?|unit)\b.*\bdong\b", text):
        currencies.add("VND")
    # "ty" in "Cong ty" is a company label, not "ty dong". A scale must
    # occur in a unit declaration or directly next to a currency marker.
    declarations = [line for line in lines if re.search(r"\b(?:don vi(?: tinh)?|units?|currency)\b", line)
                    or re.search(r"\b(?:billion|billions|million|millions|thousand|thousands|ty|trieu|nghin|ngan) (?:vnd|usd|eur|dong)\b|\b(?:vnd|usd|eur|dong|in) (?:billion|billions|million|millions|thousand|thousands|ty|trieu|nghin|ngan)\b", line)]
    if any(re.search(r"\d", line) for line in declarations):
        return None
    allowed = set("tinh of in vnd usd eur dong viet nam vietnam vietnamese dollar dollars euro euros one billion billions million millions thousand thousands ty trieu nghin ngan".split())
    for line in declarations:
        declared = re.split(r"\b(?:don vi(?: tinh)?|units?|currency)\b", line, maxsplit=1)
        if len(declared) == 2 and (not declared[1].strip() or set(declared[1].split()) - allowed):
            return None
    unit_text = " ".join(declarations)
    scales = {unit for unit, pattern in [("billion", r"\b(?:billion|billions|ty)\b"),
                                        ("million", r"\b(?:million|millions|trieu)\b"),
                                        ("thousand", r"\b(?:thousand|thousands|nghin|ngan)\b")]
              if re.search(pattern, unit_text)}
    if len(currencies) != 1 or len(scales) > 1:
        return None
    return next(iter(currencies)), next(iter(scales)) if scales else "one"


def identify_metric(label):
    normalized = clean(label)
    normalized = re.sub(r"\b\d+\b", "", normalized)
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"^(?:[a-z]|i{1,3}|iv|v)\s+", "", normalized)
    normalized = re.sub(r"^including\s+", "", normalized)
    matches = {metric for metric, labels in LABELS.items() if normalized in labels}
    return next(iter(matches)) if len(matches) == 1 else None


def extract_page(page, document_id, page_number):
    if page.rotation:
        return {"candidates": [], "warnings": ["Trang bị xoay; hãy nhập chỉ tiêu thủ công sau khi kiểm tra PDF."], "notice": NOTICE}
    words = page.get_text("words")
    if len(words) > 12000:
        return {"candidates": [], "warnings": ["Trang có quá nhiều từ để ghép dòng an toàn; hãy đối chiếu và nhập thủ công."], "notice": NOTICE}
    if not words:
        return {"candidates": [], "warnings": ["Trang chưa có văn bản để đọc số liệu; có thể cần OCR."], "notice": NOTICE}
    height = statistics.median(w[3] - w[1] for w in words)
    rows = rows_from_words(words)
    headers = []
    for row in rows:
        columns = [w for w in row["words"] if w[0] > page.rect.width * 0.35 and re.fullmatch(r"(?:19|20)\d{2}", w[4])]
        context = currency_unit(rows, row, height) if columns else None
        currency_below = any(clean(w[4]) in {"vnd", "usd", "eur"}
                             and height * 0.25 < (w[1] + w[3]) / 2 - row["y"] < height * 2
                             and any(abs(w[2] - c[2]) < height for c in columns) for w in words) if columns else False
        left_words = [w[4] for w in row["words"] if columns and w[2] < columns[0][0] and w[0] > page.rect.width * 0.2]
        bare_year_row = len(columns) >= 2 and clean(" ".join(left_words)) in {"", "year", "nam", "chi tieu", "code note"}
        if columns and context and (currency_below or bare_year_row) and len({w[4] for w in columns}) == len(columns):
            headers.append({**row, "columns": columns, "context": context})
    grouping = number_format(words)
    candidates, warnings = [], []
    for row in rows:
        possible = [h for h in headers if h["y"] + height * 0.7 < row["y"]]
        if not possible:
            continue
        header = possible[-1]
        currency, unit = header["context"]
        cells = []
        for column in header["columns"]:
            aligned = [w for w in row["words"] if abs(w[2] - column[2]) <= max(5, height * 0.7)]
            if len(aligned) == 1:
                value = parse_amount(aligned[0][4], grouping)
                if value is not None:
                    cells.append((column, aligned[0], value))
        if not cells:
            continue
        start = page.rect.width * 0.2 if page.rect.width > page.rect.height * 1.2 else 0
        left = min(cell[1][0] for cell in cells)
        labels = [w for w in row["words"] if start <= w[0] and w[2] < left - height * 0.2 and not re.fullmatch(r"\d+|\d+\([a-z]\)", w[4])]
        label = " ".join(w[4] for w in labels)
        metric = identify_metric(label)
        if not metric:
            continue
        for column, word, value in cells:
            candidates.append({"document_id": document_id, "page": page_number, "metric": metric,
                               "label": METRICS[metric], "source_label": label, "year": int(column[4]),
                               "value": value, "raw_value": word[4], "unit": unit, "currency": currency,
                               "verified": False, "method": "aligned_pdf_columns",
                               "row_excerpt": " | ".join(w[4] for w in row["words"]),
                               "bbox": [round(min(w[0] for w in labels), 2), round(min(w[1] for w in labels + [word]), 2), round(word[2], 2), round(max(w[3] for w in labels + [word]), 2)]})
    grouped = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["metric"], candidate["year"]].append(candidate)
    unique = []
    for (metric, year), group in grouped.items():
        if len({(c["value"], c["unit"], c["currency"]) for c in group}) > 1:
            warnings.append(f"Có nhiều dòng {METRICS[metric]} năm {year} khác giá trị; cần nhập thủ công để tránh chọn nhầm dòng tổng và dòng chi tiết.")
        else:
            unique.append(group[0])
    if not unique:
        warnings.append("Chưa xác định chắc chắn dòng chỉ tiêu, cột năm và đơn vị trên trang này. Bạn vẫn có thể đối chiếu và nhập thủ công.")
    return {"candidates": unique[:80], "warnings": warnings, "notice": NOTICE}


def suggest_page(path, document_id, page_number):
    try:
        with pymupdf.open(path) as pdf:
            if not 1 <= page_number <= len(pdf):
                raise ValueError("Không tìm thấy trang nguồn.")
            return extract_page(pdf[page_number - 1], document_id, page_number)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ValueError("Không đọc được PDF để đề xuất số liệu. Hãy mở tệp gốc để kiểm tra.") from exc
