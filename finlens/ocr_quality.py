"""Conservative, local OCR cleanup; never infer or substitute a digit.

The original PDF remains authoritative. Syntactic quality is not accuracy:
two well-formed OCR amounts can still disagree and require source review.
"""
from collections import Counter
from collections import defaultdict
import math
import re
import statistics

import pymupdf

from finlens.ocr import MODELS
import hashlib
from pathlib import Path


# At least four groups: do not reinterpret dates, percentages or ordinary
# decimal prices. Do not cross lines, signs, parentheses or table columns.
AMOUNT = re.compile(r'(?<![\w.,])\d{1,3}(?:[.,][ \t]*\d{3}){3,}(?![\d.,])')
NOISY_AMOUNT = re.compile(r'(?<![\w.,])\d{1,3}(?:[.,]{1,2}[ \t]*\d{3}){3,}(?![\d.,])')


def tidy_amounts(text):
    """Normalize grouping only when a numeric table establishes its style.

    Punctuation needs VND, eight clean amounts and 80% style agreement.
    Spaces inside a long grouped number can be closed on continuation pages.
    All digits, signs, parentheses and line boundaries remain unchanged.
    """
    spacing_changes = 0
    def close_group_space(match):
        nonlocal spacing_changes
        cleaned = re.sub(r'[ \t]', '', match[0])
        spacing_changes += int(cleaned != match[0])
        return cleaned
    text = AMOUNT.sub(close_group_space, text)
    if not re.search(r'\bVND\b', text, re.I):
        return text, spacing_changes
    votes = Counter()
    for match in AMOUNT.finditer(text):
        marks = set(re.findall(r'[.,]', match[0]))
        if len(marks) == 1:
            votes[next(iter(marks))] += 1
    if not votes:
        return text, spacing_changes
    mark, count = votes.most_common(1)[0]
    if count < 8 or count / sum(votes.values()) < .8:
        return text, spacing_changes
    changed = 0

    def replace(match):
        nonlocal changed
        raw = match[0]
        # A clean opposite-style number may be an intentional decimal amount.
        # Normalize mixed/noisy grouping, but leave clean punctuation alone.
        clean = re.sub(r'[ \t]', '', raw)
        marks = set(re.findall(r'[.,]', raw))
        if len(marks) > 1 or re.search(r'[.,]{2}', raw):
            clean = re.sub(r'[.,]+', mark, clean)
        changed += int(clean != raw)
        return clean

    result = NOISY_AMOUNT.sub(replace, text)
    return result, changed + spacing_changes


def amount_digits(text):
    return Counter(re.sub(r'\D', '', m[0]) for m in AMOUNT.finditer(tidy_amounts(text)[0]))


def suspicious_currency_glyphs(text):
    # A stray currency/section glyph embedded in a VND-only numeric table is
    # suspect. This does NOT turn '$' into '5'; a second image read must supply
    # any replacement. Mixed-currency pages are deliberately excluded.
    if not re.search(r'\bVND\b', text) or re.search(r'\b(?:USD|EUR|dollar)\b', text, re.I):
        return 0
    return len(re.findall(r'[$§]\d{1,3}(?:[.,]\d{3}){3,}', text))


def ordered_ocr_rows(words):
    """Keep Tesseract line words together before joining adjacent table cells.

    A tall noisy glyph/stamp must not interleave words inside a recognized line.
    Median baselines resist one oversized bounding box; overlapping columns
    and distinct baselines are never joined. No word or number is substituted.
    """
    groups=defaultdict(list)
    for word in words:
        groups[(word[5],word[6])].append(word)
    lines=[]
    for group in groups.values():
        group.sort(key=lambda w:w[7])
        lines.append({'x0':min(w[0] for w in group),'x1':max(w[2] for w in group),
            'y':statistics.median(w[3] for w in group),
            'height':statistics.median(w[3]-w[1] for w in group),
            'text':' '.join(w[4] for w in group)})
    rows=[]
    for line in sorted(lines,key=lambda l:(l['y'],l['x0'])):
        if rows and abs(line['y']-rows[-1][0]['y'])<=min(3,line['height']*.25) and all(
                line['x0']>=p['x1']-1 or line['x1']<=p['x0']+1 for p in rows[-1]):
            rows[-1].append(line)
        else:rows.append([line])
    return '\n'.join(' '.join(p['text'] for p in sorted(row,key=lambda l:l['x0'])) for row in rows)


def improve_table_ocr(page, text, ocr_dir):
    """One bounded second reading for numeric tables; never use gold labels.

    Rasterize into a separate PDF so rotated source coordinates cannot corrupt
    the OCR reading order. Choose only on structural recovery, not target values.
    """
    before = amount_digits(text)
    if sum(before.values()) < 8 or not ocr_dir:
        return text, []
    root = Path(ocr_dir)
    languages = [lang for lang in ('eng', 'vie') if (root / f'{lang}.traineddata').is_file()
                 and hashlib.sha256((root / f'{lang}.traineddata').read_bytes()).hexdigest() == MODELS[lang]]
    if 'eng' not in languages:
        return text, []
    dpi = min(400, int(72 * math.sqrt(20_000_000 / page.rect.get_area())))
    if dpi < 300:
        return text, ['Trang quá lớn cho lượt đọc kiểm tra OCR; cần đối chiếu bảng gốc.']
    try:
        pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
        content = pixmap.pdfocr_tobytes(language='+'.join(languages), tessdata=str(root.resolve()))
        with pymupdf.open(stream=content, filetype='pdf') as rendered:
            candidate = ordered_ocr_rows(rendered[0].get_text('words'))
        after = amount_digits(candidate)
        # Do not replace a full page with a truncated or badly scrambled read.
        length_ok = .75 <= len(''.join(candidate.split())) / max(1, len(''.join(text.split()))) <= 1.35
        warnings = []
        if before - after and after - before:
            warnings.append('Hai lượt OCR có số khác nhau; số liệu vẫn chưa được xác nhận, cần đối chiếu PDF gốc.')
        better = sum(after.values()) > sum(before.values()) or (
            sum(after.values()) == sum(before.values())
            and suspicious_currency_glyphs(candidate) < suspicious_currency_glyphs(text))
        if length_ok and better:
            warnings.append(f'Đã đọc kiểm tra bảng ảnh ở {dpi} dpi để khôi phục các nhóm số bị lỗi.')
            return candidate, warnings
        return text, warnings
    except Exception:
        return text, ['Chưa đọc được lượt kiểm tra OCR; giữ bản đọc ban đầu, cần đối chiếu PDF gốc.']
