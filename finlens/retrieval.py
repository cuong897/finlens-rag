"""BM25 baseline, with optional dense retrieval and reciprocal rank fusion."""

from collections import Counter
import math

from finlens.text import tokens

STOPWORDS = set("la va cua co cac cho trong mot nhung voi tu den thi nay bao cao nam hay nhu nao bao nhieu ve toi gi".split())


def bm25(query: str, chunks: list[dict], limit: int = 5):
    query_tokens = set(tokens(query)) - STOPWORDS
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
