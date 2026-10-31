"""Local XLSX/CSV table views with immutable cell provenance; no formula evaluation."""

import csv
from decimal import Decimal
import hashlib
from io import BytesIO, StringIO
import json
import math
from pathlib import Path
import re
import uuid
import zipfile

from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from finlens.ingest import IngestError
from finlens.models import METRICS, UNIT_LABELS, TableOptions
from finlens.suggestions import identify_metric

MAX_ROWS, MAX_COLS = 5000, 64
MAX_TABLE_BYTES = 20 * 1024 * 1024


def table_amount(raw, style):
    raw = raw.strip().replace("−", "-")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
        if raw.startswith("-"):
            return None
    grouping, decimal = (".", ",") if style == "vi" else (",", ".")
    if not re.fullmatch(r"-?(?:\d+|\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+)(?:" + re.escape(decimal) + r"\d{1,8})?", raw):
        return None
    value = raw.replace(grouping, "").replace(decimal, ".")
    if negative:
        value = "-" + value
    return format(Decimal(value), "f") if re.fullmatch(r"-?\d{1,24}(?:\.\d{1,8})?", value) else None


def read_table(content, filename, *, sheet="", delimiter="auto", encoding="utf-8-sig"):
    if not content or len(content) > MAX_TABLE_BYTES:
        raise IngestError("Tệp bảng phải không rỗng và tối đa 20 MB.")
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".csv":
            text = content.decode(encoding)
            if "\x00" in text:
                raise ValueError
            separator = {"tab": "\t"}.get(delimiter, delimiter)
            if delimiter == "auto":
                try:
                    separator = csv.Sniffer().sniff(text[:8192], delimiters=",;\t").delimiter
                except csv.Error:
                    raise IngestError("Chưa nhận diện được dấu phân cách CSV. Hãy chọn dấu phẩy, chấm phẩy hoặc Tab.")
            rows = []
            for row in csv.reader(StringIO(text, newline=""), delimiter=separator, strict=True):
                if len(rows) >= MAX_ROWS or len(row) > MAX_COLS:
                    raise IngestError("Bảng vượt giới hạn 5000 dòng hoặc 64 cột. Hãy tách vùng dữ liệu cần nhập.")
                rows.append([{"value": cell, "numeric": False, "formula": cell.startswith(("=", "+", "@"))} for cell in row])
            return rows, "CSV", ["CSV"]
        if extension != ".xlsx":
            raise IngestError("Chỉ hỗ trợ Excel .xlsx và CSV. Hãy chuyển .xls/.xlsm sang .xlsx không có macro.")
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 60 * 1024**2 or any("vbaproject" in e.filename.lower() for e in entries):
                raise IngestError("Excel quá lớn khi giải nén hoặc chứa macro; hãy xuất vùng dữ liệu sang tệp .xlsx mới.")
            for entry in entries:
                if entry.filename.startswith("xl/worksheets/") and entry.filename.endswith(".xml"):
                    with archive.open(entry) as xml:
                        count = 0
                        for _, element in iterparse(xml, events=("end",)):
                            if element.tag.endswith("}c"):
                                count += 1
                                address = element.get("r", "")
                                match = re.fullmatch(r"([A-Z]+)(\d+)", address)
                                if not match or int(match[2]) > MAX_ROWS or column_index_from_string(match[1]) > MAX_COLS or count > MAX_ROWS * MAX_COLS:
                                    raise IngestError("Excel vượt giới hạn 5000 dòng hoặc 64 cột; hãy xuất vùng dữ liệu cần nhập.")
                            element.clear()
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            names = [ws.title for ws in workbook.worksheets if ws.sheet_state == "visible"]
            if not names or len(names) > 30:
                raise IngestError("Excel cần từ 1 đến 30 sheet hiển thị.")
            selected = sheet or names[0]
            if selected not in names:
                raise IngestError("Sheet không tồn tại hoặc đang ẩn.")
            ws = workbook[selected]
            ws.reset_dimensions()  # Real cell bounds were checked above, not trusted from XML dimension hints.
            rows = []
            for row in ws.iter_rows():
                cells = []
                for cell in row:
                    value = cell.value
                    if isinstance(value, float) and not math.isfinite(value):
                        raise IngestError("Excel có giá trị số không hữu hạn.")
                    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                    rendered = "" if value is None else str(value)
                    cells.append({"value": rendered, "numeric": numeric, "formula": cell.data_type == "f"})
                rows.append(cells)
            return rows, selected, names
        finally:
            workbook.close()
    except IngestError:
        raise
    except Exception as exc:
        raise IngestError("Không đọc được bảng. Kiểm tra định dạng, mã hóa CSV hoặc xuất Excel thành .xlsx mới.") from exc


def prepare_table(content, filename, *, sheet="", header_row=1, delimiter="auto", encoding="utf-8-sig"):
    rows, selected, names = read_table(content, filename, sheet=sheet, delimiter=delimiter, encoding=encoding)
    if not 1 <= header_row < len(rows):
        raise IngestError("Dòng tiêu đề cần nằm trước ít nhất một dòng dữ liệu.")
    columns = max(map(len, rows), default=0)
    if not columns or any(len(c["value"]) > 4000 for row in rows for c in row):
        raise IngestError("Bảng rỗng hoặc có ô quá dài (tối đa 4000 ký tự/ô).")
    return rows, selected, names, columns


def preview_table(content, filename, **settings):
    rows, sheet, names, width = prepare_table(content, filename, **settings)
    header = settings.get("header_row", 1)
    columns = [{"column": get_column_letter(i + 1), "label": rows[header - 1][i]["value"] if i < len(rows[header - 1]) else ""} for i in range(width)]
    return {"sheet": sheet, "sheets": names, "row_count": len(rows), "columns": columns,
            "rows": [{"row_number": i + 1, "cells": [{"address": f"{get_column_letter(j + 1)}{i + 1}", **cell} for j, cell in enumerate(row)]} for i, row in enumerate(rows[:25])],
            "notice": "Chọn dòng tiêu đề, cột chỉ tiêu và cột năm. Ô công thức không được tính hoặc dùng làm số liệu đề xuất. Chỉ nhập sheet đang chọn."}


def ingest_table(store, content, filename, company, year, scope, options: TableOptions):
    rows, sheet, _, width = prepare_table(content, filename, sheet=options.sheet, header_row=options.header_row,
                                         delimiter=options.delimiter, encoding=options.encoding)
    label_column = column_index_from_string(options.label_column) - 1
    if label_column >= width or options.label_column in options.year_columns or any(column_index_from_string(c) > width for c in options.year_columns):
        raise IngestError("Cột chỉ tiêu và các cột năm phải tồn tại và không trùng nhau.")
    config = {**options.model_dump(), "sheet": sheet}
    file_sha = hashlib.sha256(content).hexdigest()
    digest = hashlib.sha256(content + b"\0finlens-table-v1\0" + json.dumps(config, sort_keys=True).encode()).hexdigest()
    existing = store.find_document(company.strip(), year, scope, digest)
    if existing:
        return {"document": existing, "created": False}
    header = rows[options.header_row - 1]
    pages, sources = [], []
    for number, cells in enumerate(rows[options.header_row:], options.header_row + 1):
        if label_column >= len(cells) or not cells[label_column]["value"].strip():
            continue
        if not any(c["value"].strip() for c in cells):
            continue
        cell_range = f"A{number}:{get_column_letter(len(cells))}{number}"
        prefix = f"{filename} | Sheet {sheet} | {cell_range} | {options.currency} | {UNIT_LABELS[options.unit]}"
        lines = []
        for j, cell in enumerate(cells):
            col = get_column_letter(j + 1)
            heading = header[j]["value"] if j < len(header) else col
            if col in options.year_columns:
                heading = str(options.year_columns[col])
            lines.append(f"{col}{number} | {heading}: {cell['value']}{' [công thức, chưa tính]' if cell['formula'] else ''}")
        page_text = prefix + "\n" + "\n".join(lines)
        if len(page_text) > 12000:
            raise IngestError("Dòng dữ liệu quá dài. Hãy tách bảng trước khi nhập.")
        pages.append(page_text)
        sources.append({"sheet": sheet, "row_number": number, "cell_range": cell_range, "cells": cells})
    if not pages or sum(map(len, pages)) > 2_000_000:
        raise IngestError("Không có dòng có nhãn chỉ tiêu, hoặc nội dung bảng vượt giới hạn 2 triệu ký tự.")
    identifier = uuid.uuid4().hex
    extension = Path(filename).suffix.lower()
    relative = f"uploads/{identifier}{extension}"
    target = store.root / relative
    target.write_bytes(content)
    try:
        document, created = store.add_document({"id": identifier, "company": company.strip(), "report_year": year, "scope": scope,
            "filename": filename.replace("\\", "/").split("/")[-1][:180], "sha256": digest, "is_demo": False, "pdf_path": relative,
            "warnings": ["Nguồn bảng: số mục là dòng có nhãn được nhập, không phải trang PDF. Ô công thức không được tính; số liệu chưa được xác nhận."]},
            pages, table_source={"file_sha256": file_sha, "options": config}, table_rows=sources)
        if not created:
            target.unlink(missing_ok=True)
        return {"document": document, "created": created}
    except Exception:
        target.unlink(missing_ok=True)
        raise


def table_suggestions(source, document_id, page):
    options, cells = source["options"], source["cells"]
    label_index = column_index_from_string(options["label_column"]) - 1
    metric = identify_metric(cells[label_index]["value"])
    candidates = []
    if metric:
        for col, year in options["year_columns"].items():
            index = column_index_from_string(col) - 1
            if index >= len(cells) or cells[index]["formula"]:
                continue
            raw = cells[index]["value"].strip()
            if cells[index]["numeric"]:
                value = format(Decimal(raw), "f")
                if not re.fullmatch(r"-?\d{1,24}(?:\.\d{1,8})?", value):
                    value = None
            else:
                value = table_amount(raw, options["number_style"])
            if value is None:
                continue
            candidates.append({"document_id": document_id, "page": page, "metric": metric, "label": METRICS[metric],
                "source_label": source["source_label"], "source_cell": f"{col}{source['row_number']}", "year": year,
                "value": value, "raw_value": raw, "unit": options["unit"], "currency": options["currency"], "verified": False,
                "method": "reviewed_table_mapping", "row_excerpt": " | ".join(c["value"] for c in cells)})
    return {"candidates": candidates, "warnings": [] if candidates else ["Dòng chưa có chỉ tiêu hoặc số liệu được nhận diện chắc chắn; có thể nhập thủ công sau khi đối chiếu."],
            "notice": "Đề xuất chưa được xác nhận. Hãy đối chiếu sheet, ô gốc, cột năm, đơn vị và phạm vi với tệp gốc trước khi lưu. Không tự đưa vào phép tính."}
