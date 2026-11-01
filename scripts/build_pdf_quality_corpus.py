"""Author controlled PDF test pages. Requires reportlab, a Unicode font and no API.

Run with a Python that has reportlab; then rasterize with
python -m scripts.evaluate_pdf_quality --assemble --source-dir OUTPUT.
This is synthetic development data, not independent issuer-report evaluation.
"""
import argparse
import hashlib
import json
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


LABELS = {
    'current_assets': ('Tài sản ngắn hạn', 'Current assets'),
    'cash': ('Tiền và tương đương tiền', 'Cash and cash equivalents'),
    'total_assets': ('Tổng tài sản', 'Total assets'),
    'net_income': ('Lợi nhuận sau thuế', 'Net profit after tax'),
}
MODES = ['native', 'scan_200', 'scan_120', 'mixed', 'scan_with_footer']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--font', required=True, help='Unicode TrueType font; its SHA is recorded')
    args = parser.parse_args(); out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=False)
    font = Path(args.font); pdfmetrics.registerFont(TTFont('Corpus', str(font)))
    canvas = Canvas(str(out / 'native-source.pdf'), pagesize=(595, 842), invariant=1)
    pages = []
    for index in range(100):
        language = 'vi' if index % 2 == 0 else 'en'
        mode = MODES[index // 20]; vi = language == 'vi'
        size = [10, 11, 12][index % 3]; unit = ['one', 'thousand', 'million', 'billion'][index % 4]
        currency = ['VND', 'USD', 'EUR'][index % 3]
        scale = {'one': '', 'thousand': 'nghìn' if vi else 'thousand', 'million': 'triệu' if vi else 'million', 'billion': 'tỷ' if vi else 'billion'}[unit]
        years = [2024 - index % 4, 2023 - index % 4]
        if index % 3 == 0: years.reverse()
        title = 'BÁO CÁO TÀI CHÍNH HỢP NHẤT' if vi else 'CONSOLIDATED BALANCE SHEET'
        canvas.setFont('Corpus', 14); canvas.drawString(35, 802, title)
        canvas.setFont('Corpus', 10); canvas.drawString(35, 775, 'SYNTHETIC QUALITY CORPUS - NOT AN ISSUER REPORT')
        unit_label = f"{'Đơn vị' if vi else 'Unit'}: {scale + ' ' if scale else ''}{currency}"
        canvas.drawString(35, 750, unit_label)
        canvas.setFont('Corpus', size)
        for year, end in zip(years, [435, 560]):
            canvas.drawRightString(end, 721, str(year)); canvas.drawRightString(end, 700, currency)
        fields = []
        for row, (metric, labels) in enumerate(LABELS.items()):
            top = 175 + row * 42; label = labels[0 if vi else 1]
            canvas.drawString(35, 842 - top, label)
            for col, (year, end) in enumerate(zip(years, [435, 560])):
                value = 1_200_000 + index * 12_307 + row * 173_021 + col * 123_457
                negative = metric == 'net_income' and index % 5 == 0
                grouping = ',' if index % 4 < 2 else '.'
                raw = f'{value:,}'.replace(',', grouping)
                raw = f'({raw})' if negative else raw
                canvas.drawRightString(end, 842 - top, raw)
                fields.append({'metric': metric, 'source_label': label, 'year': year,
                    'value': str(-value if negative else value), 'raw_value': raw,
                    'unit': unit, 'currency': currency})
        canvas.setFont('Corpus', 9); canvas.drawString(35, 38, f'Synthetic test page {index + 1:03d} - controlled text and table layout')
        pages.append({'id': f'pdf-quality-{index + 1:03d}', 'page': index + 1,
            'language': language, 'mode': mode, 'font_size': size, 'unit_label': unit_label,
            'expected_ocr': mode != 'native', 'fields': fields})
        canvas.showPage()
    canvas.save()
    manifest = {'version': 1, 'split': 'synthetic_development', 'label_origin': 'Known text used to draw controlled test pages; no independent human annotation.',
        'limitations': 'Five layout/image modes, two languages, 3 font sizes, 4 scales and 3 currencies. Does not represent the prevalence, noise or accuracy of real issuer PDFs.',
        'font_sha256': hashlib.sha256(font.read_bytes()).hexdigest(), 'pages': pages}
    (out / 'labels.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pages': len(pages), 'fields': sum(len(p['fields']) for p in pages), 'output_dir': str(out)}))


if __name__ == '__main__': main()
