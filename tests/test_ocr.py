import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import pymupdf

from finlens.ocr import needs_ocr, ocr_requirements, prepare_ocr, textpage_ocr
from finlens.pdf_text import read_page
from finlens.ingest import ingest_pdf, IngestError
from finlens.store import Store
from finlens.retrieval_check import evaluate_retrieval


class LocalOCRTests(unittest.TestCase):
    def test_readable_cover_cannot_mask_an_unreadable_report(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp, pymupdf.open() as pdf:
            pdf.new_page().insert_text((35,80),'Public report cover with searchable signature text')
            for _ in range(9):pdf.new_page()
            store=Store(tmp)
            with self.assertRaisesRegex(IngestError,'1/10'):
                ingest_pdf(store,pdf.tobytes(),'scan.pdf','EXAMPLE',2024,'consolidated')
            self.assertEqual(store.documents(),[])
            self.assertEqual(list((Path(tmp)/'uploads').iterdir()),[])

    def test_tiled_scan_is_recognized_without_a_native_financial_heading(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            tile=source.new_page(width=500,height=160)
            tile.insert_text((20,70),'Inventory valuation policy and financial statement rows')
            page=target.new_page(width=595,height=842)
            for y in [40,210,380,550]:
                page.insert_image(pymupdf.Rect(40,y,555,y+160),stream=tile.get_pixmap().tobytes('png'))
            self.assertEqual(ocr_requirements(page),(True,True))
            with patch('finlens.pdf_text.textpage_ocr',return_value=(None,'missing OCR')) as ocr:
                read_page(page)
                self.assertTrue(ocr.call_args.kwargs['full'])

    def test_overlapping_logos_do_not_count_as_a_full_page_scan(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            logo=source.new_page(width=120,height=180);logo.insert_text((20,70),'Logo')
            page=target.new_page(width=595,height=842)
            for _ in range(15):
                page.insert_image(pymupdf.Rect(30,30,150,210),stream=logo.get_pixmap().tobytes('png'))
            self.assertEqual(ocr_requirements(page),(False,False))

    def test_searchable_tiled_images_do_not_need_duplicate_ocr(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            tile=source.new_page(width=500,height=160);tile.insert_text((20,70),'Background')
            page=target.new_page(width=595,height=842)
            for y in [40,210,380,550]:
                page.insert_image(pymupdf.Rect(40,y,555,y+160),stream=tile.get_pixmap().tobytes('png'))
                page.insert_text((60,y+70),'Searchable native text already covers this image region')
            self.assertEqual(ocr_requirements(page),(False,False))

    def test_native_footer_does_not_hide_a_scanned_body(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            table=source.new_page(width=595,height=780)
            table.insert_text((35,100),'Net revenue 1,234,567')
            page=target.new_page(width=595,height=842)
            page.insert_image(pymupdf.Rect(0,0,595,780),stream=table.get_pixmap().tobytes('png'))
            page.insert_text((35,815),'Electronic copy - consult the original document')
            self.assertTrue(needs_ocr(page))

    def test_small_logo_on_a_native_page_does_not_trigger_ocr(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            logo=source.new_page(width=40,height=40);logo.insert_text((5,20),'L')
            page=target.new_page();page.insert_text((35,100),'Native financial report text with enough searchable words')
            page.insert_image(pymupdf.Rect(35,20,75,60),stream=logo.get_pixmap().tobytes('png'))
            self.assertFalse(needs_ocr(page))

    def test_scan_without_native_text_uses_both_verified_languages_or_warns(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        models={lang:hashlib.sha256(lang.encode()).hexdigest() for lang in ['eng','vie']}
        with tempfile.TemporaryDirectory(dir=root) as tmp, patch('finlens.ocr.MODELS',models):
            for lang in models:(Path(tmp)/f'{lang}.traineddata').write_bytes(lang.encode())
            page=Mock();page.rect=pymupdf.Rect(0,0,595,842);page.get_text.return_value=''
            result,notice=textpage_ocr(page,tmp)
            self.assertIsNotNone(result)
            self.assertEqual(page.get_textpage_ocr.call_args.kwargs['language'],'eng+vie')
            (Path(tmp)/'vie.traineddata').write_bytes(b'corrupted')
            _,notice=textpage_ocr(page,tmp)
            self.assertEqual(page.get_textpage_ocr.call_args.kwargs['language'],'eng')
            self.assertIn('Chưa có bộ tiếng Việt hợp lệ',notice)

    def test_outlined_table_characters_also_trigger_ocr_without_an_image(self):
        page = Mock()
        page.get_text.return_value = "CONSOLIDATED INCOME\nSTATEMENT"
        page.get_drawings.return_value = [{"fill": (0, 0, 0), "items": [None] * 4000}]
        page.get_image_info.return_value = []
        self.assertTrue(needs_ocr(page))
    def test_raster_table_is_detected_despite_existing_native_heading(self):
        with pymupdf.open() as source, pymupdf.open() as target:
            table = source.new_page(width=500, height=200)
            table.insert_text((30, 50), "Net revenue       123,456,789")
            page = target.new_page()
            page.insert_text((30, 40), "CONSOLIDATED INCOME STATEMENT")
            page.insert_image(pymupdf.Rect(30, 80, 530, 280), stream=table.get_pixmap().tobytes("png"))
            self.assertTrue(needs_ocr(page))
            text, warnings = read_page(page)
            self.assertIn("prepare-ocr", warnings[0])
            self.assertNotIn("123,456,789", text)

    def test_native_numeric_pages_do_not_request_ocr(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((30, 40), "CONSOLIDATED INCOME STATEMENT\nNet revenue 123,456,789")
            self.assertFalse(needs_ocr(page))

    def test_visual_gold_missing_after_extraction_is_scored_as_failure_not_dropped(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp, pymupdf.open() as source, pymupdf.open() as target:
            image = source.new_page(width=500, height=200)
            image.insert_text((30, 60), "Net revenue 123,456,789")
            page = target.new_page()
            page.insert_text((30, 40), "CONSOLIDATED INCOME STATEMENT")
            page.insert_image(pymupdf.Rect(30, 80, 530, 280), stream=image.get_pixmap().tobytes("png"))
            store = Store(tmp)
            doc = ingest_pdf(store, target.tobytes(), "test.pdf", "UNSEEN", 2024, "consolidated")["document"]
            store.save_embeddings("test", [(c["id"], [1, 0]) for c in store.chunks()])
            case = {"id": "visual", "company": "UNSEEN", "report_year": 2024, "scope": "consolidated", "filename": "test.pdf",
                    "question": "Doanh thu thuần", "source_sha256": doc["sha256"], "gold_pages": [1],
                    "evidence": [{"page": 1, "quotes": ["Net revenue", "123,456,789"]}],
                    "evidence_origin": "rendered_pdf", "annotation": {"reviewed_on": "2026-10-31"}}
            embedding = Mock(); embedding.embed.return_value = [[1, 0]]
            result = evaluate_retrieval(store, Path(tmp) / "out", cases=[case], embedder=embedding, embed_model="test",
                                        dataset_info={"name": "visual fixture", "split": "test", "label_origin": "synthetic unit test"})
            self.assertEqual(result["cases"], 1)
            self.assertEqual(len(result["visual_labels_missing_from_extracted_text"]), 1)
            self.assertEqual(result["summary"]["hybrid_context"]["evidence_complete_questions"], 0)

    def test_missing_or_corrupt_models_fail_closed_with_source_review_notice(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp, pymupdf.open() as pdf:
            page = pdf.new_page()
            self.assertIsNone(textpage_ocr(page, tmp)[0])
            (Path(tmp) / "eng.traineddata").write_bytes(b"corrupt")
            textpage, notice = textpage_ocr(page, tmp)
            self.assertIsNone(textpage)
            self.assertIn("Checksum", notice)

    def test_model_download_is_pinned_and_reused_without_network_when_verified(self):
        root = Path(__file__).resolve().parents[1] / "data/test-runs"
        root.mkdir(parents=True, exist_ok=True)
        content = b"fixture language data"
        response = Mock(content=content)
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory(dir=root) as tmp, patch("finlens.ocr.MODELS", {"eng": digest}):
            with patch("finlens.ocr.httpx.get", return_value=response) as get:
                self.assertTrue(prepare_ocr(tmp)["ready"])
                self.assertTrue(prepare_ocr(tmp)["ready"])
                get.assert_called_once()
        with tempfile.TemporaryDirectory(dir=root) as tmp, patch("finlens.ocr.MODELS", {"eng": "0" * 64}), patch("finlens.ocr.httpx.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "Checksum"):
                prepare_ocr(tmp)
            self.assertFalse((Path(tmp) / "ocr/tessdata/eng.traineddata").exists())
