"""Vietnamese normalization shared by indexing and querying."""

import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def chunk_page(text: str, size: int = 1600, overlap: int = 200) -> list[str]:
    """Compact layout padding, retain rows and repeat the local statement header."""
    sections = re.split(r"(?m)^\[Vùng trang \d+\]\s*$", text)
    if len(sections) > 1:
        return [chunk for section in sections if section.strip() for chunk in chunk_page(section, size, overlap)]
    text = "\n".join(re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text] if text else []
    lines = [line for line in text.splitlines() if line.strip()]
    header = next((line[:240] for line in lines[:12] if re.search(
        r"consolidated (?:balance|income|statement|cash)|notes to|b[aả]ng c[aâ]n đ[oố]i|b[aá]o c[aá]o .*h[oợ]p nh[aấ]t", line, re.I)), "")
    prefix = header + "\n" if header else ""
    budget = size - len(prefix)
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + budget, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + budget // 2, end)
            if boundary != -1:
                end = boundary
        content = text[start:end].strip()
        if content:
            chunks.append(prefix + content)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks
