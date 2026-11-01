from pathlib import Path
import tempfile
import unittest

import pymupdf

from finlens.suggestions import extract_page, suggest_page

CONTEXT={'report_year':2024,'period_start':'2024-01-01','period_end':'2024-12-31'}


def right(page,text,x,y):
    page.insert_text((x-pymupdf.get_text_length(text,fontsize=11),y),text,fontsize=11)


def table(pdf, *, title='CONSOLIDATED BALANCE SHEET',unit='VND',relative=False):
    page=pdf.new_page(width=600,height=800)
    page.insert_text((35,40),title);page.insert_text((35,60),'As at 31 December 2024')
    if unit:page.insert_text((35,80),'Unit: '+unit)
    page.insert_text((260,110),'Code');page.insert_text((300,110),'Note')
    for label,end in zip(['Ending balance','Beginning balance'] if relative else ['2024','2023'],[435,560]):right(page,label,end,110)
    return page


class DraftGeometryTests(unittest.TestCase):
    def test_shifted_label_baselines_keep_cash_total_separate_from_detail(self):
        with pymupdf.open() as pdf:
            page=table(pdf)
            page.insert_text((35,163),'I. Cash and cash equivalents')
            page.insert_text((300,160),'V.1')
            for value,end in [('1000000',435),('900000',560)]:right(page,value,end,160)
            page.insert_text((35,183),'1. Cash')
            for value,end in [('200000',435),('100000',560)]:right(page,value,end,180)
            result=extract_page(page,'doc',1,context=CONTEXT)
        self.assertEqual({c['value'] for c in result['candidates'] if c['metric']=='cash'},{'1000000','900000'})

    def test_net_inventory_section_is_not_replaced_by_gross_detail(self):
        with pymupdf.open() as pdf:
            page=table(pdf)
            for label,y,value in [('IV. Inventories',160,'1000000'),('1. Inventories',185,'1200000')]:
                page.insert_text((35,y),label);page.insert_text((300,y),'V.1')
                for end in [435,560]:right(page,value,end,y)
            result=extract_page(page,'doc',1,context=CONTEXT)
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['value']=='1000000' and not c['verified'] for c in result['candidates']))

    def test_wrapped_total_liabilities_and_equity_cannot_become_equity(self):
        with pymupdf.open() as pdf:
            page=table(pdf)
            page.insert_text((35,155),'TOTAL LIABILITIES AND');page.insert_text((35,175),'OWNERS EQUITY')
            for end in [435,560]:right(page,'5000000',end,165)
            page.insert_text((35,215),'D. Owners equity')
            for end in [435,560]:right(page,'1000000',end,215)
            result=extract_page(page,'doc',1,context=CONTEXT)
        self.assertEqual({c['value'] for c in result['candidates'] if c['metric']=='equity'},{'1000000'})

    def test_wrapped_tax_label_stops_at_new_disclosure_heading(self):
        with pymupdf.open() as pdf:
            page=table(pdf)
            page.insert_text((35,160),'Net profit after corporate income')
            page.insert_text((35,175),'tax')
            for end in [435,560]:right(page,'1000000',end,160)
            page.insert_text((35,190),'In which:')
            result=extract_page(page,'doc',1,context=CONTEXT)
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(all(c['metric']=='net_income' for c in result['candidates']))

    def test_unit_inheritance_requires_same_continued_statement_and_explicit_source(self):
        root=Path(__file__).resolve().parents[1]/'data/test-runs';root.mkdir(parents=True,exist_ok=True)
        for variant in ['valid','no_marker','different_statement','unsupported_unit','wrong_period','source_outside_scope']:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=root) as work, pymupdf.open() as pdf:
                page=table(pdf,title='CONSOLIDATED INCOME STATEMENT' if variant=='different_statement' else 'CONSOLIDATED BALANCE SHEET')
                page.insert_text((35,160),'Total assets')
                for end in [435,560]:right(page,'3000000',end,160)
                title='CONSOLIDATED BALANCE SHEET'+(' (continued)' if variant!='no_marker' else '')
                page=table(pdf,title=title,unit='JPY' if variant=='unsupported_unit' else None,relative=True)
                page.insert_text((35,160),'Current liabilities')
                for end in [435,560]:right(page,'1000000',end,160)
                path=Path(work)/'source.pdf';pdf.save(path)
                context=CONTEXT if variant!='wrong_period' else {**CONTEXT,'report_year':2034}
                if variant=='source_outside_scope':context={**CONTEXT,'allowed_pages':[2]}
                result=suggest_page(path,'doc',2,context=context)
                if variant=='valid':
                    self.assertEqual(len(result['candidates']),2)
                    self.assertTrue(all(c['unit_source_page']==1 and c['currency']=='VND' and not c['verified'] for c in result['candidates']))
                    self.assertTrue(any('trang 1' in w for w in result['warnings']))
                else:self.assertEqual(result['candidates'],[])
