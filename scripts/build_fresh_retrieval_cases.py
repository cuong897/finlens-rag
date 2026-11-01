"""Build preregistered, source-disjoint labels without reading retrieval results.

Printed values were transcribed and checked against rendered original PDFs.
Neither OCR output nor retrieval rankings supply numeric reference values.
This is same-assistant annotation, not independent human ground truth.
"""
import json
from pathlib import Path

from finlens.benchmark import reference_integer
from finlens.retrieval_dataset import check_split, fingerprint, validate_cases


KEYS = ['current_assets','cash','short_invest','inventory','noncurrent_assets',
        'total_assets','liabilities','current_liabilities','equity','revenue',
        'gross_profit','pretax','net_income','cfo','cfi','cff']
LABELS = ['tài sản ngắn hạn','tiền và các khoản tương đương tiền','đầu tư tài chính ngắn hạn',
          'hàng tồn kho thuần','tài sản dài hạn','tổng tài sản','nợ phải trả','nợ ngắn hạn',
          'vốn chủ sở hữu','doanh thu thuần','lợi nhuận gộp','lợi nhuận trước thuế',
          'lợi nhuận sau thuế toàn tập đoàn','lưu chuyển tiền thuần từ hoạt động kinh doanh',
          'lưu chuyển tiền thuần từ hoạt động đầu tư','lưu chuyển tiền thuần từ hoạt động tài chính']
# page | printed current-year amount | printed comparative amount. Unit: VND.
TABLES = {
 'SAB': '''6|25,066,732,804,106|26,553,405,591,714
6|4,477,501,667,326|5,039,908,030,600
6|16,566,239,177,101|17,741,052,417,665
6|1,978,543,927,286|2,317,504,988,808
7|8,372,673,560,545|7,503,219,247,991
7|33,439,406,364,651|34,056,624,839,705
8|9,002,313,181,979|8,571,466,944,838
8|8,657,140,531,800|8,224,608,450,196
9|24,437,093,182,672|25,485,157,894,867
10|31,872,417,486,656|30,461,367,282,215
10|9,318,196,568,266|9,091,362,504,235
10|5,647,358,628,166|5,370,410,404,295
11|4,494,773,426,912|4,255,052,995,709
12|4,362,509,568,540|2,104,718,257,779
13|18,884,385,322|2,550,929,318,308
13|(4,943,450,227,964)|(3,685,025,044,212)''',
 'MWG': '''7|65.836.422.620.040|51.950.337.976.047
7|4.897.331.540.730|5.365.704.857.172
7|29.324.441.756.320|18.937.008.073.823
7|22.244.676.326.705|21.824.235.626.176
7|4.601.353.101.564|8.160.899.236.677
7|70.437.775.721.604|60.111.237.212.724
8|42.316.198.897.749|36.751.679.245.386
8|42.316.198.897.749|30.765.260.553.972
8|28.121.576.823.855|23.359.557.967.338
9|134.341.152.848.802|118.279.788.455.442
9|27.499.210.077.021|22.520.613.163.730
9|4.825.763.013.078|689.692.367.896
9|3.733.288.228.720|167.829.154.939
10|8.517.332.706.742|3.435.827.176.070
10|(11.743.285.670.418)|(10.831.294.696.942)
11|2.757.475.513.692|7.699.783.102.825''',
 'BCM': '''9|31.703.966.346.820|28.440.868.532.001
9|2.205.138.245.025|1.332.254.152.561
9|299.912.603.095|87.314.044.988
9|21.054.729.381.948|19.833.611.109.149
10|27.076.791.421.454|24.983.033.405.257
10|58.780.757.768.275|53.423.901.937.257
11|38.101.557.432.910|33.951.011.068.428
11|21.929.549.417.474|22.997.575.585.298
12|20.679.200.335.365|19.472.890.868.829
13|5.239.236.560.627|7.882.566.359.226
13|3.498.372.370.288|4.247.833.336.459
13|2.559.347.304.331|2.697.158.161.525
13|2.395.046.491.307|2.280.087.767.209
14|(645.572.001.259)|(3.150.640.566.616)
14|(1.289.019.599.291)|472.431.552.595
15|2.807.475.693.014|2.939.816.492.742''',
 'VNM': '''7|37.553.650.065.098|35.935.879.621.477
7|2.225.943.732.075|2.912.027.359.925
7|23.260.088.671.767|20.137.243.800.460
7|5.686.840.161.996|6.128.081.805.088
8|17.495.411.471.963|16.737.491.482.983
8|55.049.061.537.061|52.673.371.104.460
9|18.874.658.707.398|17.647.627.338.990
9|18.459.546.837.640|17.138.689.974.862
9|36.174.402.829.663|35.025.743.765.470
10|61.782.609.528.445|60.368.915.511.505
10|25.590.176.323.124|24.544.731.615.410
10|11.599.653.741.335|10.967.899.391.486
10|9.452.892.989.948|9.019.354.165.051
12|9.685.937.539.346|7.887.423.562.363
13|(3.739.092.638.543)|(2.988.548.510.501)
13|(6.641.260.238.228)|(4.292.773.661.270)'''
}

# Short printed policy anchors. Canonical pages, not an exhaustive set of relevance labels.
POLICIES = {
 'SAB': [(23,'lower of cost and net realisable value'),(22,'short-term highly liquid'),
         (23,'cost less accumulated depreciation'),(21,'transaction dates'),
         (21,'account transfer buying rate'),(27,'risks and rewards')],
 'MWG': [(15,'giá thấp hơn'),(15,'thanh khoản cao'),(16,'nguyên giá'),
         (19,'ngày phát sinh nghiệp vụ'),(19,'tỷ giá mua'),(20,'rủi ro trọng yếu')],
 'BCM': [(25,'net realizable value'),(22,'do not exceed 3 months'),
         (26,'historical costs less accumulated depreciation'),(21,'actual exchange rates'),
         (22,'buying rate'),(31,'risks and benefits')],
 'VNM': [(26,'giá thấp hơn'),(24,'thanh khoản cao'),(26,'nguyên giá'),
         (24,'tại ngày giao dịch'),(24,'tỷ giá mua'),(31,'rủi ro và lợi ích')]
}
SUFFIXES=['inventory-value','cash-definition','asset-cost','fx-initial','fx-closing','sales-recognition']
PAIRS=[(5,6),(0,7),(1,3),(9,10),(11,12),(13,14),(14,15),(5,8)]


def build():
    base=Path('finlens/fixtures')
    registry=json.loads((base/'holdout_20261101_reports.json').read_text(encoding='utf-8'))
    reports=registry if isinstance(registry,list) else registry['reports']
    policies=json.loads((base/'holdout_answers_20261101.json').read_text(encoding='utf-8'))
    answer_cases={c['id']:c for c in policies['cases']}
    cases=[];targets=[]
    for company,table in TABLES.items():
        source=next(r for r in reports if r['company']==company)
        rows=[r.split('|') for r in table.splitlines()]
        if len(rows)!=16:raise ValueError('Each company needs 16 rows')
        def numeric(parts):
            evidence=[];refs=[];ids=[]
            for i,year in parts:
                page,cur,prior=rows[i];raw=cur if year==2024 else prior
                ident=f'{company.lower()}-{KEYS[i]}-{year}'
                evidence.append({'page':int(page),'quotes':[raw]})
                refs.append({'target_id':ident,'year':year,'value_vnd':reference_integer(raw),'printed':raw})
                ids.append(ident)
            return evidence,refs,ids
        def add(ident,question,category,parts):
            evidence,refs,ids=numeric(parts)
            cases.append({'id':ident,'company':company,'filename':source['filename'],'report_year':2024,
                'scope':'consolidated','source_sha256':source['sha256'],'question':question,'category':category,
                'gold_pages':sorted({a['page'] for a in evidence}),'evidence':evidence,
                'evidence_origin':'rendered_pdf','annotation':{'reviewer':'Codex same-assistant visual transcription; not independent',
                'reviewed_on':'2026-11-01'},'target_ids':ids,'reference_answers':refs})
        for year in (2024,2023):
            for i in range(16):
                q=f'{company}: {LABELS[i].capitalize()} năm {year} là bao nhiêu theo báo cáo hợp nhất 2024?'
                add(f'{company.lower()}-{KEYS[i]}-{year}',q,'statement_lookup' if year==2024 else 'prior_column',[(i,year)])
        for k,(i,j) in enumerate(PAIRS,1):
            add(f'{company.lower()}-multi-{k}',f'{company}: Cho biết {LABELS[i]} và {LABELS[j]} năm 2024.',
                'multi_value',[(i,2024),(j,2024)])
        for i in (5,9,12,13):
            add(f'{company.lower()}-compare-{KEYS[i]}',f'{company}: So sánh {LABELS[i]} năm 2023 và 2024 theo số liệu trong báo cáo hợp nhất.',
                'year_comparison',[(i,2023),(i,2024)])
        for suffix,(page,quote) in zip(SUFFIXES,POLICIES[company]):
            c=answer_cases[f'{company.lower()}-{suffix}']
            cases.append({'id':c['id']+'-retrieval','company':company,'filename':source['filename'],
                'report_year':2024,'scope':'consolidated','source_sha256':source['sha256'],'question':c['question'],
                'category':'policy','gold_pages':[page],'evidence':[{'page':page,'quotes':[quote]}],
                'evidence_origin':'rendered_pdf','annotation':{'reviewer':'Codex same-assistant source review; not independent',
                'reviewed_on':'2026-11-01'},'target_ids':[company.lower()+'-'+suffix]})
        targets.extend({'company':company,'target':KEYS[i],'page':int(p),'current':cur,'prior':prior}
                       for i,(p,cur,prior) in enumerate(rows))
    validate_cases(cases,strict=True);check_split(cases,'test')
    assert len(cases)==200
    return {'version':1,'name':'fresh-real-retrieval-20261101','split':'test',
        'label_origin':'Same-assistant source-disjoint labels on frozen product; not independent human annotation. 64 numeric rows reused across two years, multi-target and comparison questions; six policy families reused per issuer. Labels locked before first retrieval scoring. All four full PDFs retained, including BCM ingestion failure. Printed values are visual references, never repaired from component arithmetic; BCM noncurrent-assets printed total differs one VND from component sum.',
        'cases':cases},targets


if __name__=='__main__':
    dataset,targets=build()
    base=Path('finlens/fixtures')
    (base/'holdout_retrieval_20261101.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    (base/'holdout_targets_20261101.json').write_text(json.dumps(targets,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(dataset['cases']),'sha256':fingerprint(dataset)}))
