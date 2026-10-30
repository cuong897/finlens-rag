"""BM25 baseline, with optional dense retrieval and reciprocal rank fusion."""

from collections import Counter
import math
import re

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
    "tai san ngan han": "current assets", "no ngan han": "short-term liabilities",
    "kha nang tra no": "current assets short-term liabilities cash flows operating activities interest expense",
    "kha nang thanh toan": "current assets short-term liabilities cash flows operating activities",
    "chi phi lai vay": "interest expense borrowing costs",
    "doanh so thuan": "net revenue",
}


def financial_query(query: str):
    """Deterministic bilingual aliases for lexical retrieval; no LLM call."""
    normalized = normalize(query)
    extra = [english for vietnamese, english in FINANCIAL_TERMS.items() if vietnamese in normalized]
    if net_sales_intent(query):
        extra.append("net revenue sales deductions")
    return query + (" " + " ".join(extra) if extra else "")


def net_sales_intent(query):
    normalized = " ".join(tokens(query))
    if re.search(r"\b(?:truoc|gross)\b", normalized):
        return False
    return "doanh so thuan" in normalized or "net sales" in normalized or (
        "doanh so" in normalized and "giam tru" in normalized and bool(re.search(r"\bsau\b", normalized)))


# Deterministic table context, not a learned relevance score or proof of a claim.
# Page titles supplement chunks that lost their header at a page split.
STATEMENTS = {
    "income": ("consolidated income statement", "bao cao ket qua kinh doanh", "bao cao ket qua hoat dong kinh doanh"),
    "balance": ("consolidated balance sheet", "bang can doi ke toan"),
    "cashflow": ("consolidated cash flow statement", "bao cao luu chuyen tien te"),
}
TABLE_HINTS = (
    ("income", ("doanh thu", "net revenue", "doanh so thuan"), ("net revenue", "doanh thu thuan")),
    ("income", ("loi nhuan sau thue", "net income", "profit after tax"), ("profit after tax", "loi nhuan sau thue")),
    ("income", ("loi nhuan gop", "gross profit"), ("gross profit", "loi nhuan gop")),
    ("income", ("chi phi lai vay", "interest expense"), ("interest expense", "chi phi lai vay")),
    ("balance", ("tong tai san", "total assets"), ("total assets", "tong tai san")),
    ("balance", ("tai san ngan han", "current assets"), ("current assets", "tai san ngan han")),
    ("balance", ("no ngan han", "current liabilities"), ("current liabilities", "short term liabilities", "no ngan han")),
    ("balance", ("no phai tra", "liabilities"), ("liabilities", "no phai tra")),
    ("balance", ("von chu so huu", "equity"), ("equity", "von chu so huu")),
    ("cashflow", ("dong tien kinh doanh", "operating cash flow"), ("operating activities", "hoat dong kinh doanh")),
)


def financial_table_hints(query):
    normalized = " ".join(tokens(query))
    if any(re.search(r"\b" + phrase + r"\b", normalized) for phrase in
           ("vi sao", "tai sao", "nguyen nhan", "giai thich", "nho dau", "yeu to", "dong luc", "why", "reason", "explain", "drivers")):
        return []
    expanded = " ".join(tokens(financial_query(query)))
    return [(kind, labels) for kind, phrases, labels in TABLE_HINTS if any(phrase in expanded for phrase in phrases)]


def financial_context_rank(query, hits, page_texts, limit=5):
    """Prioritize a matching statement and row label, retaining base rank ties.

    Never uses gold pages, confirmed values or model output. Causal questions
    retain their original ranking because their evidence is usually in notes.
    """
    hints = financial_table_hints(query)
    if not hints:
        return hits[:limit]
    ranked = []
    for rank, hit in enumerate(hits):
        header = " ".join(tokens(page_texts.get((hit["document_id"], hit["page"]), "")[:2000]))
        content = " ".join(tokens(hit["text"]))
        priority = max((1 + int(any(label in content for label in labels)) for kind, labels in hints
                        if any(title in header for title in STATEMENTS[kind])), default=0)
        ranked.append((priority, rank, {**hit, "context_rank": priority}))
    return [item for _, _, item in sorted(ranked, key=lambda row: (-row[0], row[1]))[:limit]]


def contextual_fusion(query, sparse, dense, page_lookup, limit=5):
    pool = rrf([sparse, dense], 40)
    if not financial_table_hints(query):
        return pool[:limit]
    contexts = {}
    for hit in pool:
        key = (hit["document_id"], hit["page"])
        if key not in contexts:
            page = page_lookup(*key)
            contexts[key] = page["text"] if page else ""
    return financial_context_rank(query, pool, contexts, limit)


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
