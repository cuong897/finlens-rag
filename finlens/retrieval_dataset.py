"""Local evaluation labels, evidence matching and reproducibility metadata."""

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from typing import get_args

from finlens.models import Scope
from finlens.public_pilot import manifest


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def quote_text(text):
    # Keep punctuation, accents, signs and number separators: 1,234 != 1.234.
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate_cases(cases, *, strict=False):
    if not isinstance(cases, list) or not cases:
        raise ValueError("Bộ câu hỏi phải là danh sách không rỗng.")
    ids = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Mỗi câu hỏi phải là một đối tượng JSON.")
        for key in ("id", "company", "filename", "question"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"Câu hỏi thiếu trường {key}.")
        if case["id"] in ids:
            raise ValueError("Bộ câu hỏi có mã câu bị trùng.")
        ids.add(case["id"])
        if type(case.get("report_year")) is not int or not 1990 <= case["report_year"] <= 2100:
            raise ValueError("Năm báo cáo không hợp lệ.")
        if case.get("scope") not in get_args(Scope):
            raise ValueError("Phạm vi báo cáo không hợp lệ.")
        pages = case.get("gold_pages")
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages) or len(set(pages)) != len(pages):
            raise ValueError("Trang chuẩn phải là các số nguyên dương không trùng.")
        sha = case.get("source_sha256")
        if (strict or sha is not None) and (not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)):
            raise ValueError("Bộ câu hỏi riêng cần source_sha256 gồm 64 ký tự hex chữ thường.")
        evidence = case.get("evidence")
        if evidence is None and not strict:
            continue
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Cần gán đoạn bằng chứng cho bộ câu hỏi riêng.")
        evidence_pages = set()
        for anchor in evidence:
            if not isinstance(anchor, dict) or type(anchor.get("page")) is not int or anchor["page"] not in pages:
                raise ValueError("Bằng chứng phải thuộc trang chuẩn.")
            quotes = anchor.get("quotes")
            if not isinstance(quotes, list) or not quotes or any(not isinstance(q, str) or not quote_text(q) for q in quotes):
                raise ValueError("Mỗi bằng chứng cần các trích đoạn không rỗng.")
            evidence_pages.add(anchor["page"])
        if evidence_pages != set(pages):
            raise ValueError("Mỗi trang chuẩn cần ít nhất một đoạn bằng chứng.")


def load_dataset(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Không đọc được tệp bộ câu hỏi JSON.") from exc
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("Bộ câu hỏi cần version: 1.")
    for key in ("name", "label_origin"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Bộ câu hỏi thiếu {key}.")
    if data.get("split") not in {"development", "test"}:
        raise ValueError("split phải là development hoặc test.")
    validate_cases(data.get("cases"), strict=True)
    check_split(data["cases"], data["split"])
    return data


def check_split(cases, split):
    if split not in {"development", "test"}:
        raise ValueError("split phải là development hoặc test.")
    if split == "test":
        validate_cases(cases, strict=True)
        development_sources = {r["sha256"] for r in manifest()}
        if any(c["source_sha256"] in development_sources for c in cases):
            raise ValueError("Tập test trùng PDF đã dùng phát triển; hãy tách theo tài liệu.")


def score_evidence(hits, document_id, evidence):
    """All quotes for one anchor must occur in the same retrieved chunk.

    Multiple anchors on a page are separate requirements, not alternatives.
    This tests literal evidence coverage, not semantic entailment of an answer.
    """
    if not evidence:
        return {"evidence_recall_at_5": None, "evidence_complete_at_5": None,
                "evidence_reciprocal_rank": None, "evidence_ranks": []}
    ranks = []
    for anchor in evidence:
        rank = next((i for i, hit in enumerate(hits[:5], 1)
                     if hit["document_id"] == document_id and hit["page"] == anchor["page"]
                     and all(quote_text(q) in quote_text(hit["text"]) for q in anchor["quotes"])), None)
        ranks.append(rank)
    found = [r for r in ranks if r is not None]
    complete = len(found) == len(ranks)
    return {"evidence_recall_at_5": len(found) / len(ranks), "evidence_complete_at_5": complete,
            "evidence_reciprocal_rank": 1 / max(found) if complete else 0, "evidence_ranks": ranks}
