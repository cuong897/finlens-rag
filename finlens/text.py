"""Vietnamese normalization shared by indexing and querying."""

import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def chunk_page(text: str, size: int = 1600, overlap: int = 200) -> list[str]:
    """Keep page provenance and use bounded chunks with contextual overlap."""
    text = text.strip()
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary != -1:
                end = boundary
        content = text[start:end].strip()
        if content:
            chunks.append(content)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks
