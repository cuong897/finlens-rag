import unittest

from finlens.excerpts import source_passages


class SourcePassageTests(unittest.TestCase):
    def test_wrapped_rule_keeps_its_condition_and_exception(self):
        text='Depreciation\n\nDepreciation is computed on a straight-line basis over the\nestimated useful lives of the assets. No depreciation is charged\nuntil the asset is ready for use.'
        passages=source_passages(text)
        self.assertEqual(len(passages),1)
        self.assertIn('over the estimated useful lives',passages[0])
        self.assertTrue(passages[0].endswith('until the asset is ready for use.'))
        self.assertEqual(passages[0],' '.join(text.split('\n\n')[1].split()))

    def test_distinct_policies_remain_separate_and_amounts_are_not_changed(self):
        text='Hàng tồn kho được ghi nhận theo giá thấp hơn giữa giá gốc\nvà giá trị thuần có thể thực hiện được.\n\nDoanh thu năm nay được ghi nhận là 1.234.567.890 đồng.\n\nForeign currency transactions'
        passages=source_passages(text)
        self.assertEqual(len(passages),2)
        self.assertNotIn('Doanh thu',passages[0])
        self.assertIn('1.234.567.890',passages[1])

    def test_duplicate_paragraphs_and_short_headings_are_not_repeated(self):
        text='The group records sales revenue when significant risks and rewards are transferred.'
        self.assertEqual(source_passages('Goods sold\n\n'+text+'\n\n'+text),[text])
