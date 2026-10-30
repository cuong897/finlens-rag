"""Conservative, local drafts from aligned PDF rows; never confirmed facts."""

from collections import defaultdict
from decimal import Decimal
from datetime import date
import re
import statistics

import pymupdf

from finlens.models import METRICS
from finlens.text import tokens


LABELS = {
    "revenue": ["doanh thu thuan", "net revenue from sales of goods and rendering of services", "net revenue from sale of goods and rendering of services", "net revenue"],
    "net_income": ["loi nhuan sau thue", "net profit after tax", "profit after tax", "profit after corporate income tax"],
    "total_assets": ["tong tai san", "total assets"],
    "current_assets": ["tai san ngan han", "current assets"],
    "liabilities": ["no phai tra", "liabilities", "total liabilities"],
    "current_liabilities": ["no ngan han", "short term liabilities", "current liabilities"],
    "equity": ["von chu so huu", "owners equity", "owner s equity"],
    "cash": ["tien va cac khoan tuong duong tien", "tien va tuong duong tien", "cash and cash equivalents"],
    "receivables": ["phai thu ngan han cua khach hang", "phai thu khach hang", "short term trade accounts receivable"],
    "inventory": ["hang ton kho", "inventories"],
    "gross_profit": ["loi nhuan gop ve ban hang va cung cap dich vu", "loi nhuan gop", "gross profit from sales of goods and rendering of services", "gross profit from sale of goods and rendering of services"],
    "interest_expense": ["chi phi lai vay", "interest expense", "interest expenses"],
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
    normalized = re.sub(r"^(?:including|in which)\s+", "", normalized)
    matches = {metric for metric, labels in LABELS.items() if normalized in labels}
    return next(iter(matches)) if len(matches) == 1 else None


def period_columns(row, rows, context, height):
    """Resolve relative/date headings only against an explicit annual period."""
    try:
        start, end = date.fromisoformat(context["period_start"]), date.fromisoformat(context["period_end"])
        year = context["report_year"]
        if type(year) is not int or end.year not in {year, year + 1} or not 364 <= (end - start).days <= 366:
            return [], None
    except (KeyError, TypeError, ValueError):
        return [], None
    words = row["words"]
    dated = []
    for w in words:
        match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", w[4])
        if match:
            try:
                actual = date(*map(int, [match[3], match[2], match[1]]))
            except ValueError:
                continue
            if actual in {start, end}:
                dated.append((*w[:4], str(year if actual == end else year - 1), *w[5:]))
    if len(dated) == 2 and {w[4] for w in dated} == {str(year), str(year - 1)}:
        return dated, "period_date"
    months = "january february march april may june july august september october november december".split()
    above = clean(" ".join(w[4] for r in rows if row["y"] - height * 12 <= r["y"] < row["y"] for w in r["words"]))
    endings = [f"{end.day} {months[end.month - 1]} {end.year}", f"{end.day:02d} {end.month:02d} {end.year}",
               f"{end.year} {end.month:02d} {end.day:02d}"]
    if not any(ending in above for ending in endings):
        return [], None
    relative = []
    names = {"current year": year, "ending balance": year, "previous year": year - 1, "beginning balance": year - 1}
    for left, right in zip(words, words[1:]):
        label = clean(left[4] + " " + right[4])
        if label in names and right[0] - left[2] < height:
            relative.append((left[0], min(left[1], right[1]), right[2], max(left[3], right[3]), str(names[label]), *right[5:]))
    return (relative, "document_fiscal_period") if len(relative) == 2 and len({w[4] for w in relative}) == 2 else ([], None)


def page_regions(words, width, page_height):
    """Split a spread only when both halves have matching currency/year pairs."""
    if width <= page_height * 1.2 or any(w[0] < width / 2 < w[2] for w in words):
        return [(0, width)]
    pairs = [[], []]
    height = statistics.median(w[3] - w[1] for w in words)
    for row in rows_from_words(words):
        if row["y"] > page_height * .4:
            continue
        for half in (0, 1):
            cols = [w for w in row["words"] if (w[0] >= width / 2) == bool(half)
                    and re.fullmatch(r"(?:19|20)\d{2}|\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}", w[4])]
            if len(cols) == 2 and len({w[4] for w in cols}) == 2 and all(
                any(clean(w[4]) in {"vnd", "usd", "eur"} and abs(w[2] - col[2]) < height
                    and 0 < (w[1] + w[3]) / 2 - row["y"] < height * 2 for w in words) for col in cols):
                pairs[half].append(tuple(w[4] for w in cols))
    return [(0, width / 2), (width / 2, width)] if set(pairs[0]) & set(pairs[1]) else [(0, width)]


def extract_page(page, document_id, page_number, *, context=None):
    if page.rotation:
        return {"candidates": [], "warnings": ["Trang bị xoay; hãy nhập chỉ tiêu thủ công sau khi kiểm tra PDF."], "notice": NOTICE}
    words = page.get_text("words")
    if len(words) > 12000:
        return {"candidates": [], "warnings": ["Trang có quá nhiều từ để ghép dòng an toàn; hãy đối chiếu và nhập thủ công."], "notice": NOTICE}
    if not words:
        return {"candidates": [], "warnings": ["Trang chưa có văn bản để đọc số liệu; có thể cần OCR."], "notice": NOTICE}
    candidates, warnings = [], []
    for start, end in page_regions(words, page.rect.width, page.rect.height):
        region = [(w[0] - start, w[1], w[2] - start, *w[3:]) for w in words if start <= w[0] and w[2] <= end]
        found, notes = extract_region(region, end - start, page.rect.height, document_id, page_number, context or {})
        for candidate in found:
            candidate["bbox"][0] = round(candidate["bbox"][0] + start, 2)
            candidate["bbox"][2] = round(candidate["bbox"][2] + start, 2)
        candidates.extend(found)
        warnings.extend(notes)
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
    return {"candidates": unique[:80], "warnings": list(dict.fromkeys(warnings)), "notice": NOTICE}


def extract_region(words, width, page_height, document_id, page_number, context):
    if not words:
        return [], []
    height = statistics.median(w[3] - w[1] for w in words)
    rows = rows_from_words(words)
    headers = []
    for row in rows:
        columns = [w for w in row["words"] if w[0] > width * 0.35 and re.fullmatch(r"(?:19|20)\d{2}", w[4])]
        basis = "explicit_year"
        if not columns:
            columns, basis = period_columns(row, rows, context, height)
        units = currency_unit(rows, row, height) if columns else None
        currency_below = any(clean(w[4]) in {"vnd", "usd", "eur"}
                             and height * 0.25 < (w[1] + w[3]) / 2 - row["y"] < height * 2
                             and any(abs(w[2] - c[2]) < height for c in columns) for w in words) if columns else False
        left_words = [w[4] for w in row["words"] if columns and w[2] < columns[0][0] and w[0] > width * 0.2]
        bare_year_row = len(columns) >= 2 and clean(" ".join(left_words)) in {"", "year", "nam", "chi tieu", "code note"}
        if columns and units and (currency_below or bare_year_row or basis == "document_fiscal_period") and len({w[4] for w in columns}) == len(columns):
            headers.append({**row, "columns": columns, "context": units, "year_basis": basis})
    grouping = number_format(words)
    candidates, warnings = [], []
    for row_index, row in enumerate(rows):
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
        start = width * 0.2 if width > page_height * 1.2 else 0
        left = min(cell[1][0] for cell in cells)
        labels = [w for w in row["words"] if start <= w[0] and w[2] < left - height * 0.2 and not re.fullmatch(r"\d+|\d+\([a-z]\)", w[4])]
        label = " ".join(w[4] for w in labels)
        metric = identify_metric(label)
        # Some reports wrap the row label while keeping amounts on its first
        # or last line. Only join adjacent label-only lines in this table.
        for direction in (1, -1):
            if metric:
                break
            extended = list(labels)
            previous_y = row["y"]
            for step in (1, 2):
                adjacent = row_index + direction * step
                if not 0 <= adjacent < len(rows):
                    break
                other = rows[adjacent]
                if abs(other["y"] - previous_y) > height * 1.6 or other["y"] <= header["y"] + height:
                    break
                if any(abs(w[2] - c[2]) <= max(5, height * .7) for w in other["words"] for c in header["columns"]):
                    break
                continuation = [w for w in other["words"] if start <= w[0] and w[2] < left - height * .2 and not re.fullmatch(r"\d+", w[4])]
                extended = extended + continuation if direction == 1 else continuation + extended
                combined = " ".join(w[4] for w in extended)
                metric = identify_metric(combined)
                if metric:
                    labels, label = extended, combined
                    break
                previous_y = other["y"]
        if not metric:
            continue
        for column, word, value in cells:
            candidates.append({"document_id": document_id, "page": page_number, "metric": metric,
                               "label": METRICS[metric], "source_label": label, "year": int(column[4]),
                               "value": value, "raw_value": word[4], "unit": unit, "currency": currency,
                               "verified": False, "method": "aligned_pdf_columns",
                               "year_basis": header["year_basis"],
                               "row_excerpt": label + " | " + " | ".join(cell[1][4] for cell in cells),
                               "bbox": [round(min(w[0] for w in labels), 2), round(min(w[1] for w in labels + [word]), 2), round(word[2], 2), round(max(w[3] for w in labels + [word]), 2)]})
    return candidates, warnings


def suggest_page(path, document_id, page_number, *, context=None):
    try:
        with pymupdf.open(path) as pdf:
            if not 1 <= page_number <= len(pdf):
                raise ValueError("Không tìm thấy trang nguồn.")
            return extract_page(pdf[page_number - 1], document_id, page_number, context=context)
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise ValueError("Không đọc được PDF để đề xuất số liệu. Hãy mở tệp gốc để kiểm tra.") from exc
