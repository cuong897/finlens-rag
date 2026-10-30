'use strict';

const imports = { items: [], busy: false, stop: false };

function importHints(filename) {
  const plain = filename.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/gi, 'd');
  const years = [...new Set(plain.match(/\b(?:19|20)\d{2}\b/g) || plain.match(/(?:19|20)\d{2}/g) || [])];
  const known = state.documents.map(d => d.company).find(company => new RegExp(`(?:^|[^a-z0-9])${company.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:[^a-z0-9]|$)`, 'i').test(plain));
  return {
    company: $('#upload-company').value.trim() || known || '',
    year: $('#upload-year').value || (years.length === 1 ? years[0] : ''),
    scope: /standalone|separate|rieng.?le/i.test(plain) ? 'standalone' : $('#upload-scope').value,
  };
}

function addImport(source) {
  if (imports.items.length >= 10) { toast('Danh sách tối đa 10 báo cáo. Hãy nhập hoặc bỏ bớt trước khi thêm.', true); return; }
  const duplicate = imports.items.some(item => source.file
    ? item.file && item.file.name === source.file.name && item.file.size === source.file.size && item.file.lastModified === source.file.lastModified
    : item.url === source.url);
  if (duplicate) { toast('Mục này đã có trong danh sách kiểm tra.'); return; }
  imports.items.push({ ...source, ...importHints(source.name), status: 'pending', message: 'Chờ kiểm tra thông tin' });
}

function addFiles(files) {
  if (imports.busy) return;
  for (const file of files) {
    if (!file.name.toLowerCase().endsWith('.pdf') || file.size > 20 * 1024 * 1024 || !file.size) {
      toast(`${file.name}: cần PDF không rỗng, tối đa 20 MB.`, true); continue;
    }
    addImport({ file, name: file.name });
  }
  renderImports();
  if (imports.items.length) $('#import-review').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderImports() {
  $('#import-review').hidden = !imports.items.length;
  $('#import-items').replaceChildren();
  imports.items.forEach((item, index) => {
    const card = node('article', `import-item ${item.status}`);
    const title = node('div', 'import-item-title');
    const remove = node('button', 'text-link', 'Bỏ'); remove.type = 'button';
    remove.setAttribute('aria-label', `Bỏ ${item.name}`); remove.disabled = imports.busy;
    remove.addEventListener('click', () => { imports.items.splice(index, 1); renderImports(); });
    title.append(node('strong', '', `${index + 1}. ${item.name}`), remove); card.append(title);
    if (item.url) card.append(node('p', 'import-source', item.url));
    const fields = node('div', 'import-fields');
    for (const [key, labelText] of [['company', 'Doanh nghiệp'], ['year', 'Năm báo cáo'], ['scope', 'Phạm vi']]) {
      const label = node('label', '', labelText);
      const input = node(key === 'scope' ? 'select' : 'input');
      if (key === 'scope') input.append(option('consolidated', 'Hợp nhất'), option('standalone', 'Riêng lẻ'));
      else if (key === 'year') { input.type = 'number'; input.min = 1990; input.max = 2100; }
      else { input.maxLength = 120; input.placeholder = 'Tên doanh nghiệp'; }
      input.value = item[key]; input.required = true;
      input.disabled = imports.busy || item.status === 'done';
      input.setAttribute('aria-label', `${labelText} báo cáo ${index + 1}`);
      input.addEventListener('input', () => { item[key] = input.value; });
      label.append(input); fields.append(label);
    }
    card.append(fields, node('p', 'import-item-status', item.message));
    $('#import-items').append(card);
  });
  const done = imports.items.filter(item => item.status === 'done').length;
  const failed = imports.items.filter(item => item.status === 'failed').length;
  $('#import-progress').textContent = `${imports.items.length} báo cáo · ${done} hoàn tất${failed ? ` · ${failed} cần thử lại` : ''}`;
  $('#import-start').disabled = imports.busy || done === imports.items.length;
  $('#import-start').textContent = imports.busy ? 'Đang nhập báo cáo…' : `Nhập ${imports.items.length - done} báo cáo ↗`;
  $('#import-clear').disabled = imports.busy;
  $('#import-stop').hidden = !imports.busy;
  $('#upload-file').disabled = imports.busy;
  $('#url-form button').disabled = imports.busy;
}

$('#upload-file').addEventListener('change', event => { addFiles(event.target.files); event.target.value = ''; });
for (const type of ['dragenter', 'dragover']) $('#drop-zone').addEventListener(type, event => {
  event.preventDefault(); $('#drop-zone').classList.add('drag-over');
});
for (const type of ['dragleave', 'drop']) $('#drop-zone').addEventListener(type, event => {
  event.preventDefault(); $('#drop-zone').classList.remove('drag-over');
  if (type === 'drop') addFiles(event.dataTransfer.files);
});
$('#url-form').addEventListener('submit', event => {
  event.preventDefault(); if (imports.busy) return;
  let invalid = false;
  for (const value of $('#import-urls').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean)) {
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443') || value.length > 2048) throw new Error();
      const name = decodeURIComponent(url.pathname.split('/').pop() || url.hostname);
      addImport({ url: url.href, name });
    } catch { invalid = true; }
  }
  renderImports();
  if (invalid) toast('Một số đường dẫn không hợp lệ. Dùng HTTPS tới PDF, mỗi dòng một đường dẫn.', true);
  else $('#import-urls').value = '';
  if (imports.items.length) $('#import-review').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
$('#import-clear').addEventListener('click', () => { imports.items = []; renderImports(); });
$('#import-stop').addEventListener('click', () => { imports.stop = true; $('#import-stop').disabled = true; $('#import-progress').textContent = 'Sẽ dừng sau báo cáo đang xử lý. Các mục còn lại được giữ để nhập tiếp.'; });
$('#import-form').addEventListener('submit', async event => {
  event.preventDefault(); if (imports.busy) return;
  if (imports.items.some(item => item.status !== 'done' && (!item.company.trim() || !Number.isInteger(Number(item.year)) || Number(item.year) < 1990 || Number(item.year) > 2100))) {
    toast('Hãy kiểm tra doanh nghiệp và năm của từng báo cáo.', true); return;
  }
  imports.busy = true; imports.stop = false; $('#import-stop').disabled = false;
  let selected = null;
  renderImports();
  try {
    for (const item of imports.items) {
      if (imports.stop) break;
      if (item.status === 'done') continue;
      item.status = 'working'; item.message = item.url ? 'Đang tải và đọc PDF…' : 'Đang đọc PDF…'; renderImports();
      try {
        const metadata = { company: item.company.trim(), year: Number(item.year), scope: item.scope };
        const result = item.url ? await post('/api/import-url', { ...metadata, url: item.url })
          : await api(`/api/documents?${new URLSearchParams({ ...metadata, filename: item.file.name })}`, {
            method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: item.file,
          });
        selected = result.document;
        item.status = 'done';
        item.message = result.created ? 'Đã nhập PDF.' : 'PDF đã có trong kho, không tạo bản trùng.';
        if (state.embeddingModel) {
          item.message += ' Đang chuẩn bị tìm kiếm…'; renderImports();
          try {
            await post(`/api/documents/${encodeURIComponent(result.document.id)}/index`, {});
            item.message = `${result.created ? 'Đã nhập PDF.' : 'Đã có trong kho.'} Tìm kiếm đã sẵn sàng.`;
          } catch { item.message = 'PDF đã lưu. Tìm kiếm từ khóa đã sẵn sàng; tìm kiếm ngữ nghĩa chưa sẵn sàng.'; }
        } else item.message += ' Có thể tìm kiếm từ khóa.';
      } catch (error) { item.status = 'failed'; item.message = error.message; }
      renderImports();
    }
    if (selected) {
      $('#dataset').value = 'real'; $('#scope').value = selected.scope; resetAnalysis();
      await refresh(); $('#company').value = selected.company; updateFilters(false);
    }
  } catch (error) { toast(`Danh sách đã xử lý được giữ lại. ${error.message}`, true); }
  finally { imports.busy = false; renderImports(); }
});
