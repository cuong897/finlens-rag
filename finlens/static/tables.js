'use strict';

let tableDraft = null;
let tablePreviewRevision = 0;

async function configureTable(item) {
  imports.editing = true; renderImports();
  tableDraft = { item, preview: null };
  $('#table-config').hidden = false;
  $('#table-config-title').textContent = item.name;
  const saved = item.tableOptions;
  $('#table-sheet').replaceChildren(option(saved?.sheet || '', saved?.sheet || 'Đang đọc danh sách sheet…'));
  $('#table-header').value = saved?.header_row || 1;
  $('#table-delimiter').value = saved?.delimiter || 'auto';
  $('#table-encoding').value = saved?.encoding || 'utf-8-sig';
  $('#table-unit').value = saved?.unit || 'one';
  $('#table-currency').value = saved?.currency || 'VND';
  $('#table-style').value = saved?.number_style || 'vi';
  $('#table-config').scrollIntoView({ behavior: 'smooth', block: 'start' });
  await refreshTablePreview(saved);
}

async function refreshTablePreview(saved = null) {
  if (!tableDraft) return;
  const draft = tableDraft, revision = ++tablePreviewRevision;
  $('#table-save').disabled = true; $('#table-status').textContent = 'Đang đọc bảng trên máy…';
  $('#table-preview').replaceChildren(); $('#table-years').replaceChildren();
  try {
    const settings = { filename: draft.item.name, sheet: $('#table-sheet').value, header_row: $('#table-header').value,
      delimiter: $('#table-delimiter').value, encoding: $('#table-encoding').value };
    const preview = await api(`/api/tables/preview?${new URLSearchParams(settings)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: draft.item.file,
    });
    if (revision !== tablePreviewRevision || tableDraft !== draft) return;
    draft.preview = preview;
    $('#table-sheet').replaceChildren(...preview.sheets.map(sheet => option(sheet, sheet)));
    $('#table-sheet').value = preview.sheet;
    $('#table-label').replaceChildren(...preview.columns.map(c => option(c.column, `${c.column} · ${c.label || '(không có tiêu đề)'}`)));
    $('#table-label').value = saved?.label_column || 'A';
    for (const col of preview.columns) {
      const label = node('label', '', `${col.column} · ${col.label || '(không có tiêu đề)'}`);
      const year = node('input'); year.type = 'number'; year.min = 1990; year.max = 2100;
      year.placeholder = 'Không dùng'; year.dataset.column = col.column;
      const years = [...new Set(col.label.match(/(?:19|20)\d{2}/g) || [])];
      year.value = saved?.year_columns?.[col.column] || (years.length === 1 ? years[0] : '');
      year.setAttribute('aria-label', `Năm của cột ${col.column}`); label.append(year); $('#table-years').append(label);
    }
    const table = node('table'), head = node('thead'), header = node('tr');
    header.append(node('th', '', 'Dòng'));
    preview.columns.forEach(c => header.append(node('th', '', c.column)));
    head.append(header); const body = node('tbody');
    preview.rows.forEach(row => {
      const tr = node('tr'); tr.append(node('th', '', row.row_number));
      row.cells.forEach(cell => tr.append(node('td', '', cell.value + (cell.formula ? ' [công thức]' : '')))); body.append(tr);
    });
    table.append(head, body); $('#table-preview').append(table);
    $('#table-status').textContent = `${preview.sheet} · ${preview.row_count} dòng trong sheet. ${preview.notice}`;
    $('#table-save').disabled = false;
  } catch (error) { if (revision === tablePreviewRevision) { draft.preview = null; $('#table-status').textContent = error.message; } }
}

for (const id of ['table-sheet', 'table-header', 'table-delimiter', 'table-encoding']) {
  $(`#${id}`).addEventListener('change', () => refreshTablePreview());
}
$('#table-cancel').addEventListener('click', () => {
  tableDraft = null; tablePreviewRevision += 1; imports.editing = false;
  $('#table-config').hidden = true; renderImports();
});
$('#table-config-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!tableDraft?.preview || !imports.items.includes(tableDraft.item)) return;
  const columns = {};
  $('#table-years').querySelectorAll('input').forEach(input => { if (input.value) columns[input.dataset.column] = Number(input.value); });
  if (!Object.keys(columns).length || Object.keys(columns).length > 10 || new Set(Object.values(columns)).size !== Object.keys(columns).length || columns[$('#table-label').value]) {
    toast('Chọn từ 1 đến 10 cột năm, mỗi năm một cột; không dùng cột chỉ tiêu làm cột năm.', true); return;
  }
  tableDraft.item.tableOptions = { sheet: $('#table-sheet').value, header_row: Number($('#table-header').value), label_column: $('#table-label').value,
    year_columns: columns, unit: $('#table-unit').value, currency: $('#table-currency').value, number_style: $('#table-style').value,
    delimiter: $('#table-delimiter').value, encoding: $('#table-encoding').value };
  tableDraft.item.message = 'Đã chọn cách đọc bảng. Kiểm tra thông tin doanh nghiệp trước khi nhập.';
  tableDraft = null; imports.editing = false; $('#table-config').hidden = true; renderImports();
  $('#import-review').scrollIntoView({ behavior: 'smooth', block: 'start' });
});
