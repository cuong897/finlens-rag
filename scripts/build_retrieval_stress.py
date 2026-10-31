"""Build 80 paired wording stress cases on existing development targets.

Run: python -m scripts.build_retrieval_stress
These cases test query robustness, not independent generalization.
No retrieval code or retrieval results are used to generate questions."""
import copy
import hashlib
import json
from pathlib import Path

root=Path('artifacts/overfitting-audit')
root.mkdir(parents=True, exist_ok=True)
original=json.loads(Path('finlens/fixtures/benchmark_development_v3_200.json').read_text(encoding='utf-8'))
ledger=json.loads(Path('finlens/fixtures/benchmark_targets_v3.json').read_text(encoding='utf-8'))
# Common abbreviations and alternate wording. Same scalar target and 2024
# column; no causal/comparison questions or changed numeric reference answers.
alternate={
 'current_assets':'TSNH',
 'noncurrent_assets':'TSDH',
 'cash':'số dư tiền và các khoản tương đương tiền',
 'inventory':'giá trị tồn kho thuần',
 'total_assets':'quy mô tổng tài sản',
 'liabilities':'tổng các khoản nợ phải trả',
 'equity':'VCSH',
 'revenue':'DTT',
 'pat':'LNST',
 'cfo':'LCTT thuần từ hoạt động kinh doanh',
}
by_id={c['id']:c for c in original['cases']}
cases=[]
paired=[]
for company,rows in ledger['targets'].items():
 lookup={r['id']:r for r in rows}
 for metric,wording in alternate.items():
  row=lookup[metric]
  parent=by_id[f'{company.lower()}-v3-{metric}']
  # Balance stock versus annual flow remain unambiguous; company/scope are
  # still provided as metadata, exactly as in the existing benchmark.
  period='cuối năm 2024' if row['kind']=='balance' else 'năm 2024'
  for variant,label in [('short',row['question_label_vi']),('alternate',wording)]:
   item=copy.deepcopy(parent)
   item['id']=parent['id']+'-audit-'+variant
   item['question']=f'Cho tôi {label} {period}, kèm nguồn.'
   item['category']='query_stress_'+variant
   item['annotation']['method']='Paired transformation of an existing development scalar question; same source and answer column; no new visual annotation. Alternate wording assistant-authored, human review pending.'
   cases.append(item)
   paired.append({'id':item['id'],'parent_id':parent['id'],'variant':variant,'metric':metric,'question':item['question']})
dataset={'version':1,'name':'finlens-overfitting-query-stress-80','split':'development',
 'label_origin':'Assistant-authored paired query stress on existing development PDFs and scalar targets. No new independent sources/labels; not a blind test. Short variants remove table and template cues; alternate variants use wording/abbreviations. Frozen before scoring; no tuning in this audit.',
 'protocol':{'evaluation_commit':'1a5b393','parent_dataset':'benchmark_development_v3_200.json','cases':80,'unique_scalar_targets':40,'query_semantics_review':'assistant only; no independent reviewer','not_generation_accuracy':True},
 'reports':original['reports'],'cases':cases}
raw=json.dumps(dataset,ensure_ascii=False,indent=2)+'\n'
(root/'query-stress-80.json').write_text(raw,encoding='utf-8')
(root/'query-pairs.json').write_text(json.dumps(paired,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'cases':len(cases),'sha256':hashlib.sha256(raw.encode('utf-8')).hexdigest()}))
