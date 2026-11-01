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


def chunk_page_rows(text: str, size: int = 1600, overlap: int = 200) -> list[str]:
    """Experimental whole-line windows; never join page regions or invent headers.

    A line longer than the body budget is split explicitly. Repeated context is
    limited to short headings and unambiguous year/unit declarations on the page.
    This remains an experiment until retrieval regression gates are satisfied.
    """
    if size < 64 or not 0 <= overlap < size // 2:
        raise ValueError("Kích thước đoạn hoặc độ chồng lấn không hợp lệ.")
    sections = re.split(r"(?m)^\[Vùng trang \d+\]\s*$", text)
    if len(sections) > 1:
        return [c for section in sections if section.strip() for c in chunk_page_rows(section, size, overlap)]
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    compact = "\n".join(lines)
    if len(compact) <= size:
        return [compact]
    headings = [line for line in lines[:12] if len(line) <= 240 and re.search(
        r"(?:consolidated )?(?:balance sheet|income statement|statement of|cash flow statement)|notes to|bảng cân đối|báo cáo .*hợp nhất", line, re.I)]
    units = list(dict.fromkeys(line for line in lines if len(line) <= 100 and re.fullmatch(
        r"(?:(?:unit|currency|đơn vị tính|don vi tinh)\s*[:：]?\s*)?(?:(?:million|thousand|billion|triệu|nghìn|tỷ)\s+)?(?:VND|USD|EUR)(?:\s*['’]000)?", line, re.I)))
    years = list(dict.fromkeys(line for line in lines if len(line) <= 100 and re.fullmatch(
        r"(?:\b(?:19|20)\d{2}\b[ |/–—-]*){2,}", line)))
    headings = list(dict.fromkeys(headings))
    context = (headings if len(headings) == 1 else []) + (units if len(units) == 1 else []) + (years if len(years) == 1 else [])
    # Keep complete declarations; omit context that cannot fit the context budget.
    prefix_lines = []
    for line in context:
        if len("\n".join(prefix_lines + [line])) + 1 <= min(360, size // 3):
            prefix_lines.append(line)
    prefix = "\n".join(prefix_lines)
    prefix = prefix + "\n" if prefix else ""
    budget = size - len(prefix)
    units_of_text = [part for line in lines for part in
                     ([line] if len(line) <= budget else [line[i:i + budget] for i in range(0, len(line), budget)])]
    chunks, start = [], 0
    while start < len(units_of_text):
        end, length = start, 0
        while end < len(units_of_text):
            extra = len(units_of_text[end]) + (1 if end > start else 0)
            if length + extra > budget:
                break
            length += extra
            end += 1
        chunks.append(prefix + "\n".join(units_of_text[start:end]))
        if end == len(units_of_text):
            break
        next_start, carried = end, 0
        while next_start > start + 1:
            extra = len(units_of_text[next_start - 1]) + (1 if carried else 0)
            if carried + extra > overlap:
                break
            carried += extra
            next_start -= 1
        start = next_start
    return chunks
