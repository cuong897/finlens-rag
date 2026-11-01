"""Create a self-contained, local answer-review worksheet from saved responses."""
import json
from pathlib import Path

from finlens.retrieval_dataset import fingerprint


def render_review_page(results_file,cases_file,output_file):
    report=json.loads(Path(results_file).read_text(encoding='utf-8'))
    dataset=json.loads(Path(cases_file).read_text(encoding='utf-8'))
    if report.get('summary',report).get('dataset_sha256')!=fingerprint(dataset):raise ValueError('Dataset checksum mismatch.')
    cases={c['id']:c for c in dataset['cases']}
    rows=[{**r,'expected_status':cases[r['id']]['expected_status']} for r in report['results'] if r['execution']=='completed']
    key=fingerprint(rows)
    for row in rows:
        case=cases[row['id']]
        row['request_context']={'company':case.get('company',''),'years':case.get('years',[]),'scope':case.get('scope','consolidated')}
        row['display_reference']=row.get('reference') or case.get('exact_text') or ('Mức thay đổi tham chiếu: '+', '.join(case['growth'])+'%.' if 'growth' in case else 'Đối chiếu nguồn, kỳ và phạm vi đã chọn; chưa có đáp án được xác nhận trong phạm vi này.')
    packet=json.dumps({'key':key,'rows':rows},ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=HTML.replace('__PACKET__',packet)
    target=Path(output_file);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(html,encoding='utf-8')
    return {'file':str(target),'questions':len(rows)}


HTML='''<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FinLens · Chấm câu trả lời</title>
<style>body{font:17px/1.6 system-ui;background:#f4f6f2;color:#20392c;margin:auto;max-width:1000px;padding:28px}header,article{background:white;border:1px solid #d9e2d7;border-radius:14px;padding:24px;margin:20px 0}h1{font-size:30px}h2{font-size:21px}pre{white-space:pre-wrap;font:inherit;background:#f4f6f2;padding:16px;border-radius:8px}label{display:block;margin:12px 0}select,input,textarea,button{font:inherit;padding:10px;max-width:100%;box-sizing:border-box}input,textarea{width:100%}button{background:#2d563f;color:white;border:0;border-radius:8px;cursor:pointer}button:disabled{opacity:.45;cursor:default}.scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}details{border-top:1px solid #ddd;padding:12px 0}.status{position:sticky;bottom:0;background:#e7eee4;padding:16px;border:1px solid #cad7c5;border-radius:10px}small{color:#536655}</style>
<header><h1>Chấm câu trả lời FinLens</h1><p>Đối chiếu câu trả lời với nguồn bên dưới. Bộ này dùng dữ liệu doanh nghiệp giả lập; tên model và kết quả kiểm tra tự động không quyết định điểm nội dung.</p>
<p><strong>Điểm 0:</strong> sai/không hỗ trợ; <strong>1:</strong> đúng hoặc đủ một phần; <strong>2:</strong> đúng/đủ hoàn toàn. Kiểm tra từng số, dấu, năm và đơn vị. N/A ở điểm nguồn chỉ dành cho câu từ chối không lỗi API.</p>
<label>Người chấm<input id="reviewer" maxlength="120" autocomplete="off" placeholder="Tên hoặc mã người chấm"></label>
<small>Điểm được lưu trong trình duyệt trên máy này. Phiếu CSV chỉ tải về máy; trang không gửi điểm lên dịch vụ bên ngoài. Nếu trình duyệt chặn lưu cục bộ, hãy giữ trang mở đến khi tải phiếu.</small></header>
<main id="questions"></main><div class="status"><span id="progress" role="status" aria-live="polite"></span> <button id="download" disabled>Tải phiếu chấm CSV</button><p id="message" role="status"></p></div>
<script type="application/json" id="packet">__PACKET__</script><script>
'use strict';
const packet=JSON.parse(document.querySelector('#packet').textContent), key='finlens-review-'+packet.key;
let saved={};try{const parsed=JSON.parse(localStorage.getItem(key)||'{}');saved=parsed&&typeof parsed==='object'?parsed:{};}catch{}
const fields=['correctness','completeness','source_support','abstention_correct'];
const el=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
const controls=new Map();document.querySelector('#reviewer').value=saved.reviewer||'';
function refresh(){let complete=0;const draft={reviewer:document.querySelector('#reviewer').value,rows:{}};
 for(const row of packet.rows){const c=controls.get(row.id),v={};for(const f of [...fields,'notes'])v[f]=c[f].value;draft.rows[row.id]=v;
 if(fields.every(f=>v[f]))complete++;}
 document.querySelector('#progress').textContent=`Đã chấm đủ ${complete}/${packet.rows.length} câu.`;
 document.querySelector('#download').disabled=!packet.rows.length||complete!==packet.rows.length||!draft.reviewer.trim();
 try{localStorage.setItem(key,JSON.stringify(draft));}catch{document.querySelector('#message').textContent='Không lưu được bản nháp trong trình duyệt. Giữ trang mở đến khi tải phiếu.';}
}
for(const [i,row] of packet.rows.entries()){
 const article=el('article');article.append(el('h2',`${i+1}. ${row.question}`),el('small',row.id));
 article.append(el('p',`Doanh nghiệp: ${row.request_context.company} · Năm đã chọn: ${row.request_context.years.join(', ')} · Phạm vi: ${row.request_context.scope==='standalone'?'Riêng lẻ':'Hợp nhất'}`));
 article.append(el('h3','Câu trả lời thực tế'),el('pre',row.response.answer));
 article.append(el('h3','Đáp án/điều kiện tham chiếu'),el('p',row.display_reference));
 article.append(el('small',`Trạng thái: ${row.response.status} · Model: ${row.response.generation_model||'Tính toán/xử lý cục bộ'}${row.response.generation_error?' · Có lỗi nhà cung cấp':''}`));
 for(const source of row.response.citations){const d=el('details'),summary=el('summary',`${source.id} · ${source.company} · ${source.filename} · ${source.source_label||'Trang '+source.page}`);d.append(summary,el('pre',source.excerpt));article.append(d);}
 if(!row.response.citations.length)article.append(el('p','Không có nguồn dẫn trong câu trả lời. Đối chiếu đáp án tham chiếu để đánh giá việc từ chối.'));
 const grid=el('div');grid.className='scores';const c={};
 const abstentionNeeded=row.expected_status==='insufficient_evidence'||row.response.status==='insufficient_evidence';
 for(const [field,label] of [['correctness','Độ đúng'],['completeness','Độ đầy đủ'],['source_support','Nguồn hỗ trợ'],['abstention_correct','Từ chối đúng?']]){
  const wrap=el('label',label),select=el('select');select.setAttribute('aria-label',`${label} · Câu ${i+1}`);
  const options=field==='abstention_correct'?(abstentionNeeded?[['','Chưa chấm'],['yes','Có'],['no','Không']]:[['N/A','Không áp dụng']]):[['','Chưa chấm'],['0','0 — Không đạt'],['1','1 — Một phần'],['2','2 — Hoàn toàn']];
  if(field==='source_support'&&row.response.status==='insufficient_evidence'&&!row.response.generation_error)options.push(['N/A','Không áp dụng']);
  for(const [value,text] of options){const option=el('option',text);option.value=value;select.append(option);}
  const prior=saved.rows?.[row.id]?.[field];if(prior!==undefined)select.value=prior;
  c[field]=select;select.addEventListener('change',refresh);wrap.append(select);grid.append(wrap);
 }
 const note=el('label','Ghi chú / lỗi phát hiện'),input=el('textarea');input.rows=2;input.value=saved.rows?.[row.id]?.notes||'';input.addEventListener('input',refresh);note.append(input);c.notes=input;
 article.append(grid,note);controls.set(row.id,c);document.querySelector('#questions').append(article);
}
document.querySelector('#reviewer').addEventListener('input',refresh);
document.querySelector('#download').addEventListener('click',()=>{
 refresh();if(document.querySelector('#download').disabled)return;
 const headers=['id','question','reference','answer','sources','model',...fields,'reviewer','notes'];
 const quote=v=>{let text=String(v??'');if(/^[\\s]*[=+\\-@]|^[\\t\\r\\n]/.test(text))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
 const rows=packet.rows.map(row=>{const c=controls.get(row.id);return {id:row.id,question:row.question,reference:row.reference||'',answer:row.response.answer,sources:JSON.stringify(row.response.citations),model:row.response.generation_model||'',...Object.fromEntries([...fields,'notes'].map(f=>[f,c[f].value])),reviewer:document.querySelector('#reviewer').value.trim()};});
 const csv='\\ufeff'+[headers,...rows.map(row=>headers.map(h=>row[h]))].map(row=>row.map(quote).join(',')).join('\\r\\n');
 const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=el('a');link.href=url;link.download='finlens-human-review.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.querySelector('#message').textContent='Đã tạo phiếu CSV. Giữ tệp này để nhập vào công cụ tính kết quả chấm.';
});refresh();
</script></html>'''
