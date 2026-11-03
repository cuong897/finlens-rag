import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import pymupdf

from finlens.ocr_quality import tidy_amounts, improve_table_ocr, ordered_ocr_rows


class OCRQualityTests(TestCase):
    def table(self):
        return 'Unit: VND\n' + '\n'.join(f'Row {i} 1.234.567.{i:03d}' for i in range(12))

    def test_fix_group_spacing_and_mixed_marks_without_changing_digits_or_signs(self):
        raw = self.table() + '\nBalance (2.345,678.901) 3.,456.789.012 4.567.890. 123'
        clean, count = tidy_amounts(raw)
        self.assertEqual(count, 3)
        self.assertIn('(2.345.678.901) 3.456.789.012 4.567.890.123', clean)
        import re
        self.assertEqual(re.sub(r'\D', '', clean), re.sub(r'\D', '', raw))

    def test_never_guess_digits_decimals_dates_currency_or_join_lines(self):
        raw = self.table() + '\n23.26(.088.671.767 $5.049.061.537.061\n2.50 3,14 31.12.2024 1.234.567.\n890'
        self.assertEqual(tidy_amounts(raw), (raw, 0))
        foreign = raw.replace('VND', 'USD') + '\n2.345,678.901'
        self.assertEqual(tidy_amounts(foreign), (foreign, 0))

    def test_ambiguous_style_and_small_samples_are_untouched(self):
        small = 'VND\n1.234.567.890\n2.345,678.901'
        self.assertEqual(tidy_amounts(small), (small, 0))
        mixed = self.table() + self.table().replace('.', ',') + '\n2.345,678.901'
        self.assertEqual(tidy_amounts(mixed), (mixed, 0))

    def test_continuation_page_closes_group_space_without_inferring_currency(self):
        self.assertEqual(tidy_amounts('Cash flow (2.345.678. 901)\n3.456.789.\n012'),
                         ('Cash flow (2.345.678.901)\n3.456.789.\n012', 1))

    def test_second_read_recovers_structural_damage_but_reports_disagreement(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            (Path(tmp)/'eng.traineddata').write_bytes(b'model')
            import hashlib
            model={'eng':hashlib.sha256(b'model').hexdigest(),'vie':'missing'}
            before=self.table()+'\nBroken 3.45(.789.012'
            after=before.replace('3.45(', '3.456').replace('1.234.567.001','9.234.567.001')
            page=Mock();page.rect=pymupdf.Rect(0,0,595,842)
            doc=Mock();doc.__enter__=Mock(return_value=doc);doc.__exit__=Mock(return_value=None)
            doc.__getitem__=Mock(return_value=Mock());doc[0].get_text.return_value=after
            with patch('finlens.ocr_quality.MODELS',model),patch('finlens.ocr_quality.pymupdf.open',return_value=doc),patch('finlens.ocr_quality.ordered_ocr_rows',side_effect=lambda _:doc[0].get_text.return_value):
                text,warnings=improve_table_ocr(page,before,tmp)
                self.assertEqual(text,after)
                self.assertTrue(any('số khác nhau' in w for w in warnings))
                page.get_pixmap.assert_called_once()
                doc[0].get_text.return_value=after[:30]
                self.assertEqual(improve_table_ocr(page,before,tmp)[0],before)

    def test_tall_glyph_does_not_split_a_label_and_cells_keep_their_rows(self):
        words=[(10,80,30,100,'TOTAL',0,0,0),(35,94,60,104,'ASSETS',0,0,1),
               (65,94,75,104,'NET',0,0,2),(120,94,145,104,'270',0,1,0),
               (160,94,210,104,'1.234.567.890',0,2,0),(240,94,290,104,'2.345.678.901',0,3,0),
               (45,85,55,95,'stamp',1,0,0),(10,110,60,120,'OTHER',2,0,0),
               (160,110,210,120,'3.456.789.012',2,1,0)]
        text=ordered_ocr_rows(words)
        self.assertIn('TOTAL ASSETS NET 270 1.234.567.890 2.345.678.901',text.splitlines())
        self.assertIn('OTHER 3.456.789.012',text.splitlines())
        self.assertEqual(text.split().count('stamp'),1)

    def test_non_numeric_pages_never_trigger_an_extra_read(self):
        page=Mock()
        self.assertEqual(improve_table_ocr(page,'A policy with no table','unused'),('A policy with no table',[]))
        page.get_pixmap.assert_not_called()
