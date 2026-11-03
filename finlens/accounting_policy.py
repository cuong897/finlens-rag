"""Accounting-policy intent and bilingual note context; never reference labels.

These lexical features organize evidence. They do not certify that a model's
claim is entailed by a cited page, and never supply an accounting answer.
"""
import re

from finlens.query_language import canonical_query
from finlens.text import normalize


TOPICS = {
    'inventory': ('hang ton kho','inventories','inventory'),
    'cash_equivalents': ('tuong duong tien','cash equivalents'),
    'tangible_assets': ('tai san co dinh huu hinh','tangible fixed assets'),
    'depreciation': ('khau hao','depreciation'),
    'foreign_currency': ('ngoai te','ty gia','foreign currency','foreign currencies','currencies other than vnd','exchange rate'),
    'sales': ('doanh thu','revenue','sales recognition'),
}
CUES = ('chinh sach','nguyen tac','phuong phap','dieu kien','dinh nghia','duoc phan loai',
        'duoc trinh bay','cach quy doi','cach danh gia','cach ghi nhan','ghi nhan doanh thu',
        'accounting policy','accounting policies','recognition policy','recognition criteria',
        'measurement basis','measured at','depreciation method','recognised','recognized',
        'classified','translated','depreciated','valued')
QUANTITATIVE = r'\b(?:bao nhieu|so sanh|chenh lech|muc tang|muc giam|ty le|phan tram|how much|compare|difference)\b|%'
EXPANSIONS = {
    'inventory': 'inventories lower cost net realisable realizable value weighted average specific identification',
    'cash_equivalents': 'cash equivalents short term highly liquid known amounts insignificant risk maturity',
    'tangible_assets': 'tangible fixed assets historical cost accumulated depreciation directly attributable costs',
    'depreciation': 'depreciation straight line estimated useful lives',
    'foreign_currency': 'foreign currency transactions monetary assets liabilities exchange rates buying selling transfer',
    'sales': 'revenue recognition sale goods risks rewards benefits ownership',
}
RULES = {
    'inventory': ('lower of cost','net realisable value','net realizable value','gia thap hon','gia tri thuan'),
    'inventory_cost': ('weighted average','specific identification','binh quan gia quyen','thuc te dich danh'),
    'cash_equivalents': ('highly liquid','known amounts','insignificant risk','thanh khoan cao','luong tien xac dinh','it rui ro','khong co nhieu rui ro'),
    'tangible_assets': ('cost less accumulated','historical costs less','directly attributable','working condition','nguyen gia tru','chi phi lien quan truc tiep'),
    'depreciation': ('straight line','duong thang','useful lives','thoi gian huu dung'),
    'foreign_currency': ('transaction dates','actual exchange rate','ngay giao dich','ngay phat sinh','ty gia giao dich thuc te'),
    'foreign_currency_closing': ('monetary assets and liabilities','monetary items','buying rate','selling rate','buying and selling','tai san va no phai tra','ty gia mua','ty gia ban','khoan muc tien te'),
    'sales': ('risks and rewards','risks and benefits','rui ro va loi ich','rui ro trong yeu','ownership','quyen so huu'),
}


def policy_intent(question):
    q=canonical_query(question)
    if re.search(QUANTITATIVE,q):return None
    if not any(re.search(r'(?<!\w)'+re.escape(c)+r'(?!\w)',q) for c in CUES):return None
    topics=[key for key,aliases in TOPICS.items() if any(alias in q for alias in aliases)]
    if not topics:return None
    if 'depreciation' in topics and 'tangible_assets' in topics:topics.remove('tangible_assets')
    variant=None
    if 'foreign_currency' in topics and any(c in q for c in ('danh gia lai','cuoi ky','ket thuc','lap bao cao','year end','reporting date','closing')):
        variant='foreign_currency_closing'
    elif 'inventory' in topics and ('gia goc' in q or 'cost' in q) and not any(c in q for c in ('gia tri','lower','net realis')):
        variant='inventory_cost'
    return {'topics':topics,'variant':variant}


def policy_expansion(question):
    intent=policy_intent(question)
    return ' '.join(EXPANSIONS[t] for t in intent['topics']) if intent else ''


def source_has(text,phrase):
    # MuPDF OCR sometimes omits spaces between Vietnamese syllables. Match a
    # phrase across such boundaries, while keeping the original source intact.
    return re.sub(r'[^a-z0-9]','',normalize(phrase)) in re.sub(r'[^a-z0-9]','',normalize(text))


def rank_policy_chunks(question,hits,page_texts,limit=5):
    intent=policy_intent(question)
    if not intent:return hits[:limit]
    ranked=[]
    for rank,hit in enumerate(hits):
        body=hit['text'];page=page_texts.get((hit['document_id'],hit['page']),'')
        topic_count=sum(any(source_has(body,phrase) for phrase in TOPICS[t]) for t in intent['topics'])
        method_count=0
        for topic in intent['topics']:
            rules=RULES.get(intent['variant'] if intent['variant'] and intent['variant'].startswith(topic) else topic,())
            method_count+=min(3,sum(source_has(body,r) for r in rules))
        # Both sides of a buy/sell rule outrank receivables-only snippets for a
        # question about all monetary items. No rates or claims are fabricated.
        contrast=0
        if intent['variant']=='foreign_currency_closing':
            buy=any(source_has(body,p) for p in ('buying rate','transfer buying rate','ty gia mua'))
            sell=any(source_has(body,p) for p in ('selling rate','transfer selling rate','ty gia ban'))
            contrast=6*int(buy and sell)
        notes=any(source_has(page[:1800],p) for p in ('notes to','thuyet minh','significant accounting policies'))
        priority=8*topic_count+2*method_count+contrast+int(notes and method_count>0)
        ranked.append((priority,rank,{**hit,'policy_rank':priority}))
    return [h for _,_,h in sorted(ranked,key=lambda r:(-r[0],r[1]))[:limit]]
