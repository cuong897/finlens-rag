import unittest

from finlens.text import chunk_page_rows


class RowChunkTests(unittest.TestCase):
    def test_rows_remain_intact_with_year_and_unit_context(self):
        rows = [f"Unique item {i} | {i + 1},123,456 | {i + 2},456,789" for i in range(120)]
        text = 'CONSOLIDATED BALANCE SHEET\nUnit: million VND\n2024 | 2023\n' + '\n'.join(rows)
        chunks = chunk_page_rows(text)
        for row in rows:
            self.assertTrue(any(row in chunk.splitlines() for chunk in chunks), row)
        self.assertTrue(all(len(chunk) <= 1600 for chunk in chunks))
        self.assertTrue(all('2024 | 2023' in c and 'Unit: million VND' in c for c in chunks))
        self.assertTrue(all(line in set(rows + ['CONSOLIDATED BALANCE SHEET','Unit: million VND','2024 | 2023'])
                            for c in chunks for line in c.splitlines()))

    def test_ambiguous_units_are_not_promoted_to_global_context(self):
        text = 'Unit: VND\n' + ('first row\n' * 200) + 'Unit: USD\n' + ('second row\n' * 200)
        chunks = chunk_page_rows(text)
        self.assertFalse(any('Unit: VND' in c for c in chunks if 'second row' in c and 'first row' not in c))

    def test_regions_long_lines_and_validation(self):
        chunks = chunk_page_rows('[Vùng trang 1]\n' + 'L' * 4000 + '\n[Vùng trang 2]\nRIGHT')
        self.assertEqual(''.join(c for c in chunks if 'RIGHT' not in c), 'L' * 4000)
        self.assertTrue(all(len(c) <= 1600 for c in chunks))
        self.assertEqual(chunks[-1], 'RIGHT')
        self.assertEqual(chunk_page_rows(' \n '), [])
        for size, overlap in [(0,0),(100,-1),(100,90)]:
            with self.assertRaises(ValueError): chunk_page_rows('text',size,overlap)
