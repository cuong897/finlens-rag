import json
import unittest

from finlens.grounding import lost_classification_scope
from finlens.providers import render_grounded_output,ProviderError


class ClassificationScopeTests(unittest.TestCase):
    def test_classification_condition_is_not_an_unconditional_property(self):
        source='Các khoản phải thu được phân loại là tài sản ngắn hạn được trình bày riêng trong báo cáo.'
        self.assertTrue(lost_classification_scope('Các khoản phải thu là tài sản ngắn hạn.',[source]))
        for good in ['Các khoản phải thu được phân loại là tài sản ngắn hạn được trình bày riêng.',
                     'Nếu các khoản phải thu được phân loại là tài sản ngắn hạn thì được trình bày riêng.',
                     'Nếu khoản phải thu là tài sản ngắn hạn thì được trình bày riêng.',
                     'Các khoản phải thu ngắn hạn được trình bày riêng.',
                     'Các khoản phải thu không phải là tài sản ngắn hạn.']:
            self.assertFalse(lost_classification_scope(good,[source]),good)

    def test_english_relative_classification_and_other_financial_categories(self):
        source='Investments that are classified as financial assets are measured at fair value.'
        self.assertTrue(lost_classification_scope('Investments are financial assets.',[source]))
        self.assertFalse(lost_classification_scope('Investments classified as financial assets are measured at fair value.',[source]))
        self.assertFalse(lost_classification_scope('Cash equivalents are financial assets.',[source]))

    def test_ocr_missing_spaces_and_narrowly_qualified_fx_subjects(self):
        source='Các khoản đầu tư nắm giữđến ngày đáo hạn được phân loại là các khoản mục tiền tệcó gốc ngoại tệđược đánh giá lại theo tỷgiá mua chuyển khoản tại ngày báo cáo.'
        bad='Các khoản đầu tư nắm giữ đến ngày đáo hạn là các khoản mục tiền tệ có gốc ngoại tệ được đánh giá lại theo tỷ giá mua chuyển khoản.'
        self.assertTrue(lost_classification_scope(bad,[source]))
        self.assertFalse(lost_classification_scope('Các khoản đầu tư có gốc ngoại tệ được đánh giá lại theo tỷ giá mua chuyển khoản.',[source]))

    def test_only_cited_sources_can_trigger_scope_failure(self):
        evidence=[{'id':'S1','excerpt':'Các khoản phải thu là tài sản ngắn hạn.'},
                  {'id':'S2','excerpt':'Các khoản phải thu được phân loại là tài sản ngắn hạn được trình bày riêng.'}]
        text=lambda source:json.dumps({'abstain':False,'statements':[{'text':'Các khoản phải thu là tài sản ngắn hạn.','sources':[source]}]})
        self.assertIn('[S1]',render_grounded_output(text('S1'),evidence))
        with self.assertRaises(ProviderError) as error:render_grounded_output(text('S2'),evidence)
        self.assertEqual(error.exception.code,'unsupported_scope')
