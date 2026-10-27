"""BM25 baseline, with optional dense retrieval and reciprocal rank fusion."""

from collections import Counter
import math

from finlens.text import tokens
from finlens.text import normalize

STOPWORDS = set("la va cua co cac cho trong mot nhung voi tu den thi nay bao cao nam hay nhu nao bao nhieu ve toi gi vi sao tai khi the".split())

FINANCIAL_TERMS = {
    "doanh thu thuan": "net revenue", "doanh thu": "revenue",
    "loi nhuan sau thue": "profit after tax", "loi nhuan gop": "gross profit",
    "tong tai san": "total assets", "no phai tra": "liabilities",
    "von chu so huu": "owners equity", "phai thu khach hang": "trade receivables",
    "hang ton kho": "inventories", "dong tien kinh doanh": "cash flows operating activities",
    "bao cao ket qua kinh doanh": "consolidated income statement",
    "bang can doi ke toan": "consolidated balance sheet",
}


def financial_query(query: str):
    """Deterministic bilingual aliases for lexical retrieval; no LLM call."""
    normalized = normalize(query)
    extra = [english for vietnamese, english in FINANCIAL_TERMS.items() if vietnamese in normalized]
    return query + (" " + " ".join(extra) if extra else "")


def content_terms(text: str):
    return {t for t in tokens(text) if not (t.isdigit() and 1990 <= int(t) <= 2100)} - STOPWORDS


def bm25(query: str, chunks: list[dict], limit: int = 5):
    query_tokens = content_terms(query)
    if not chunks or not query_tokens:
        return []
    documents = [tokens(c["text"]) for c in chunks]
    average = sum(map(len, documents)) / len(documents) or 1
    frequencies = Counter(t for doc in documents for t in set(doc))
    scored = []
    for chunk, document in zip(chunks, documents):
        counts = Counter(document)
        score = 0.0
        for token in query_tokens:
            frequency = counts[token]
            if frequency:
                idf = math.log(1 + (len(documents) - frequencies[token] + 0.5) / (frequencies[token] + 0.5))
                score += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * len(document) / average))
        if score > 0:
            scored.append({**chunk, "score": round(score, 6)})
    return sorted(scored, key=lambda c: (-c["score"], c["id"]))[:limit]


def cosine(left, right):
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0


def rrf(rankings: list[list[dict]], limit: int = 5):
    scores, items = {}, {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            key = item["id"]
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            items[key] = item
    ordered = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [{**items[key], "score": round(scores[key], 6)} for key in ordered]
