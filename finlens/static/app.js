'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { documents: [], facts: [], catalog: {}, source: null, view: 'analysis', busy: false, revision: 0 };
const statusLabels = { answered: 'Kết quả có nguồn dẫn', evidence_found: 'Đã tìm thấy đoạn nguồn liên quan', partial: 'Đã trả lời một phần câu hỏi', insufficient_evidence: 'Cần thêm bằng chứng' };
const modeLabels = { calculated: 'Tính từ chỉ tiêu xác nhận', extractive: 'Trích xuất nguồn', generated: 'Diễn giải bằng AI' };
let toastTimer;

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').className = error ? 'error' : '';
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch { throw new Error('Máy chủ trả về dữ liệu không hợp lệ.'); }
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(e => `${e.loc?.slice(-1)[0] || 'Dữ liệu'}: ${e.msg}`).join('; ') : body.detail;
    throw new Error(detail || 'Không thực hiện được yêu cầu.');
  }
  return body;
}

function post(path, body) {
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

function setView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(el => { el.hidden = el.id !== `view-${view}`; });
  document.querySelectorAll('[data-view]').forEach(button => {
    button.classList.toggle('active', button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#breadcrumb').textContent = { analysis: 'Phân tích báo cáo', library: 'Kho tài liệu', facts: 'Chỉ tiêu xác nhận' }[view];
}

function option(value, label) {
  const result = node('option', '', label);
  result.value = value;
  return result;
}

function resetAnalysis() {
  state.revision += 1;
  state.source = null;
  $('#result-empty').hidden = false;
  $('#result-content').hidden = true;
  $('#result-title').textContent = 'Bắt đầu từ một câu hỏi hay.';
  $('#result-meta').textContent = '';
  $('#source-empty').hidden = false;
  $('#source-content').hidden = true;
}

function updateFilters(preserve = true) {
  const previousCompany = preserve ? $('#company').value : '';
  const previousYears = new Set([...document.querySelectorAll('#year-options input:checked')].map(el => Number(el.value)));
  const demo = $('#dataset').value === 'demo';
  const documents = state.documents.filter(d => d.is_demo === demo && d.scope === $('#scope').value);
  const companies = [...new Set(documents.map(d => d.company))].sort((a, b) => a.localeCompare(b, 'vi'));
  $('#company').replaceChildren(...companies.map(c => option(c, c)));
  if (companies.includes(previousCompany)) $('#company').value = previousCompany;
  if (!companies.length) $('#company').append(option('', 'Chưa có tài liệu phù hợp'));
  const years = [...new Set(documents.filter(d => d.company === $('#company').value).map(d => d.report_year))].sort();
  $('#year-options').replaceChildren();
  const defaults = years.length > 1 ? years.slice(0, 2) : years;
  for (const year of years) {
    const label = node('label', 'year-option');
    const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.value = year;
    checkbox.checked = preserve && previousYears.size ? previousYears.has(year) : defaults.includes(year);
    label.append(checkbox, document.createTextNode(String(year)));
    $('#year-options').append(label);
  }
  if (!years.length) $('#year-options').append(node('span', 'field-hint', 'Nhập PDF hoặc nạp bộ dữ liệu demo để bắt đầu.'));
  $('#dataset-notice').classList.toggle('real', !demo);
  $('#dataset-notice').lastElementChild.textContent = demo
    ? 'Dữ liệu demo là giả lập của doanh nghiệp hư cấu. Chọn “Tài liệu của tôi” để làm việc với PDF bạn tải lên.'
    : 'Đang dùng tài liệu bạn tải lên. Số liệu cần được xác nhận với trang nguồn trước khi tính toán.';
}

async function refresh() {
  [state.documents, state.facts] = await Promise.all([api('/api/documents'), api('/api/facts')]);
  $('#stat-documents').textContent = state.documents.length;
  $('#nav-count').textContent = state.documents.length;
  $('#stat-companies').textContent = new Set(state.documents.map(d => d.company)).size;
  $('#stat-facts').textContent = state.facts.filter(f => f.verified).length;
  updateFilters();
  renderDocuments();
  renderFacts();
  const selectedDocument = $('#fact-document').value;
  $('#fact-document').replaceChildren(...state.documents.filter(d => !d.is_demo && d.has_pdf).map(d => option(d.id, `${d.company} · ${d.report_year} · ${d.scope === 'consolidated' ? 'Hợp nhất' : 'Riêng lẻ'}`)));
  if ([...$('#fact-document').options].some(o => o.value === selectedDocument)) $('#fact-document').value = selectedDocument;
  if (!$('#fact-document').options.length) $('#fact-document').append(option('', 'Hãy nhập PDF của bạn trước'));
  $('#fact-button').disabled = !$('#fact-document').value;
  $('#fact-preview').disabled = !$('#fact-document').value;
}

function renderDocuments() {
  $('#document-list').replaceChildren();
  if (!state.documents.length) $('#document-list').append(node('p', 'empty-list', 'Kho tài liệu đang trống. Nạp dữ liệu demo để khám phá hoặc tải báo cáo PDF đầu tiên.'));
  for (const document of state.documents) {
    const row = node('article', 'document-row');
    const details = node('div', 'doc-details');
    const title = node('p', 'doc-title', `${document.company} · ${document.report_year}`);
    if (document.is_demo) title.append(node('span', 'demo-tag', 'GIẢ LẬP'));
    details.append(title, node('div', 'doc-meta', `${document.scope === 'consolidated' ? 'Hợp nhất' : 'Riêng lẻ'} · ${document.page_count} trang · ${document.chunk_count} đoạn nguồn`), node('div', 'doc-meta', document.filename));
    if (document.warnings.length) details.append(node('div', 'doc-meta', document.warnings.join(' ')));
    const button = node('button', '', 'Xem ↗');
    button.addEventListener('click', () => openSource(document.id, 1));
    row.append(node('div', 'doc-glyph', '▤'), details, button);
    $('#document-list').append(row);
  }
}

function formatNumber(value) {
  // Decimal strings remain exact; avoid converting financial amounts to JS floats.
  const [integer, fractional] = String(value).split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, '.') + (fractional ? ',' + fractional : '');
}

function renderFacts() {
  $('#facts-table-body').replaceChildren();
  for (const fact of state.facts) {
    const row = node('tr');
    const label = node('td');
    label.append(node('span', 'company-small', fact.company), document.createTextNode(state.catalog.metrics[fact.metric] || fact.metric));
    if (fact.is_demo) label.append(node('span', 'demo-tag', 'GIẢ LẬP'));
    const source = node('td');
    const sourceButton = node('button', '', `Trang ${fact.page} ↗`);
    sourceButton.addEventListener('click', () => openSource(fact.document_id, fact.page));
    source.append(sourceButton);
    const actions = node('td');
    if (!fact.is_demo) {
      const revoke = node('button', 'revoke-button', 'Thu hồi');
      revoke.title = 'Thu hồi xác nhận; có thể nhập lại từ trang nguồn';
      revoke.addEventListener('click', async () => {
        revoke.disabled = true;
        try { await api(`/api/facts/${fact.id}`, { method: 'DELETE' }); resetAnalysis(); await refresh(); toast('Đã thu hồi chỉ tiêu. Bạn có thể xác nhận lại từ trang nguồn.'); }
        catch (error) { toast(error.message, true); revoke.disabled = false; }
      });
      actions.append(revoke);
    }
    row.append(label, node('td', '', fact.year), node('td', '', `${formatNumber(fact.value)} ${state.catalog.units[fact.unit]} ${fact.currency}`), source, actions);
    $('#facts-table-body').append(row);
  }
  if (!state.facts.length) {
    const row = node('tr'); const cell = node('td', 'empty-list', 'Chưa có chỉ tiêu. Nạp dữ liệu demo hoặc xác nhận số liệu từ PDF của bạn.'); cell.colSpan = 5; row.append(cell); $('#facts-table-body').append(row);
  }
}

async function openSource(documentId, page) {
  const revision = state.revision;
  try {
    const source = await api(`/api/documents/${encodeURIComponent(documentId)}/pages/${page}`);
    if (revision !== state.revision) return;
    state.source = { documentId, page };
    setView('analysis');
    $('#source-empty').hidden = true; $('#source-content').hidden = false;
    $('#source-label').textContent = `${source.document.company} · ${source.document.report_year}${source.document.is_demo ? ' · GIẢ LẬP' : ''}`;
    $('#source-page').textContent = `Trang ${page}`;
    $('#source-text').textContent = source.text || '(Trang không có văn bản trích xuất)';
    const metadata = state.documents.find(d => d.id === documentId);
    $('#prev-page').disabled = page <= 1;
    $('#next-page').disabled = page >= (metadata?.page_count || page);
    $('#source-pdf').hidden = !source.document.has_pdf;
    $('#source-pdf').href = `/api/documents/${encodeURIComponent(documentId)}/pdf#page=${page}`;
    if (window.innerWidth < 1000) $('.source-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { toast(error.message, true); }
}

function renderAnswer(result) {
  $('#result-empty').hidden = true; $('#result-content').hidden = false;
  $('#result-title').textContent = statusLabels[result.status] || result.status;
  $('#result-meta').textContent = `${modeLabels[result.mode]} · ${result.latency_ms} ms`;
  $('#answer-text').textContent = result.answer;
  $('#comparison-tables').replaceChildren();
  for (const comparison of result.comparisons) {
    const card = node('article', 'comparison-card');
    const heading = node('div', 'comparison-top');
    heading.append(node('span', '', comparison.label));
    const percentage = comparison.growth_percent;
    if (percentage !== null) heading.append(node('span', `growth-badge${percentage.startsWith('-') ? ' negative' : ''}`, `${percentage.startsWith('-') ? '' : '+'}${formatNumber(percentage)}%`));
    const table = node('table');
    const head = node('thead'); const headers = node('tr');
    for (const text of [String(comparison.from_year), String(comparison.to_year), 'Chênh lệch']) headers.append(node('th', '', text));
    head.append(headers);
    const body = node('tbody'); const row = node('tr');
    for (const value of [comparison.from_value, comparison.to_value, comparison.delta]) row.append(node('td', '', `${formatNumber(value)} ${comparison.unit_label} ${comparison.currency}`));
    body.append(row); table.append(head, body);
    card.append(heading, table, node('div', 'formula', comparison.formula ? `Công thức: ${comparison.formula} = ${percentage}%` : comparison.note));
    $('#comparison-tables').append(card);
  }
  $('#answer-warnings').replaceChildren(...result.warnings.map(w => node('p', 'warning', w)));
  $('#citation-list').replaceChildren();
  for (const citation of result.citations) {
    const button = node('button', 'citation-button');
    button.type = 'button';
    button.append(node('span', 'citation-id', citation.id), node('span', '', `${citation.company} · ${citation.report_year} · Trang ${citation.page}${citation.is_demo ? ' · Giả lập' : ''}`), node('span', 'citation-arrow', '↗'));
    button.addEventListener('click', () => openSource(citation.document_id, citation.page));
    $('#citation-list').append(button);
  }
  if (result.citations.length) openSource(result.citations[0].document_id, result.citations[0].page);
}

$('#ask-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (state.busy) return;
  if (!$('#company').value) { toast('Hãy nạp dữ liệu demo hoặc nhập PDF trước.', true); return; }
  const years = [...document.querySelectorAll('#year-options input:checked')].map(el => Number(el.value));
  if (!years.length) { toast('Hãy chọn ít nhất một năm báo cáo.', true); return; }
  state.busy = true; $('#ask-button').disabled = true; $('#ask-button').textContent = 'Đang phân tích…'; $('#result-panel').setAttribute('aria-busy', 'true');
  const revision = state.revision;
  try {
    const result = await post('/api/ask', { question: $('#question').value, company: $('#company').value, years, dataset: $('#dataset').value, scope: $('#scope').value });
    if (state.revision === revision) renderAnswer(result);
    else toast('Bộ lọc đã thay đổi. Hãy phân tích lại với phạm vi mới.');
  } catch (error) { toast(error.message, true); }
  finally { state.busy = false; $('#ask-button').disabled = false; $('#ask-button').textContent = 'Phân tích ↗'; $('#result-panel').setAttribute('aria-busy', 'false'); }
});

$('#seed-button').addEventListener('click', async () => {
  $('#seed-button').disabled = true;
  try {
    const result = await post('/api/demo', {}); $('#dataset').value = 'demo'; $('#scope').value = 'consolidated'; resetAnalysis();
    await refresh(); updateFilters(false); toast(result.created ? `Đã nạp ${result.created} báo cáo giả lập.` : 'Bộ dữ liệu demo đã sẵn sàng.');
  } catch (error) { toast(error.message, true); }
  finally { $('#seed-button').disabled = false; }
});

$('#upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  const file = $('#upload-file').files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) { toast('PDF vượt giới hạn 20 MB.', true); return; }
  $('#upload-button').disabled = true; $('#upload-button').textContent = 'Đang đọc báo cáo…';
  try {
    const query = new URLSearchParams({ company: $('#upload-company').value.trim(), year: $('#upload-year').value, scope: $('#upload-scope').value, filename: file.name });
    const result = await api(`/api/documents?${query}`, { method: 'POST', headers: { 'Content-Type': 'application/pdf' }, body: file });
    $('#dataset').value = 'real'; $('#scope').value = $('#upload-scope').value;
    resetAnalysis();
    await refresh(); $('#company').value = result.document.company; updateFilters(false);
    $('#fact-document').value = result.document.id; $('#fact-year').value = result.document.report_year;
    toast(result.created ? 'Đã nhập PDF. Bạn có thể hỏi đáp hoặc xác nhận chỉ tiêu từ trang nguồn.' : 'PDF này đã có trong kho; đã mở tài liệu hiện có.');
    $('#upload-file').value = '';
  } catch (error) { toast(error.message, true); }
  finally { $('#upload-button').disabled = false; $('#upload-button').textContent = 'Tải lên và xử lý ↗'; }
});

$('#fact-preview').addEventListener('click', () => openSource($('#fact-document').value, Number($('#fact-page').value)));
$('#fact-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('#fact-button').disabled = true;
  try {
    await post('/api/facts', { document_id: $('#fact-document').value, page: Number($('#fact-page').value), metric: $('#fact-metric').value, year: Number($('#fact-year').value), value: $('#fact-value').value.trim(), unit: $('#fact-unit').value, currency: $('#fact-currency').value, confirmed: $('#fact-confirm').checked, note: $('#fact-note').value.trim() });
    resetAnalysis();
    await refresh(); $('#fact-value').value = ''; $('#fact-confirm').checked = false;
    toast('Đã lưu chỉ tiêu và trang nguồn. Bạn có thể dùng để tính toán.');
  } catch (error) { toast(error.message, true); }
  finally { $('#fact-button').disabled = !$('#fact-document').value; }
});

document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => setView(button.dataset.view)));
document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  $('#question').value = button.dataset.question;
  if ($('#dataset').value === 'demo') {
    const needed = new Set([...button.dataset.question.matchAll(/20\d{2}/g)].map(m => m[0]));
    document.querySelectorAll('#year-options input').forEach(input => { input.checked = needed.has(input.value); });
  }
  $('#question').focus();
}));
$('#dataset').addEventListener('change', () => { resetAnalysis(); updateFilters(false); });
$('#scope').addEventListener('change', () => { resetAnalysis(); updateFilters(false); });
$('#company').addEventListener('change', () => { resetAnalysis(); updateFilters(false); });
$('#year-options').addEventListener('change', resetAnalysis);
$('#prev-page').addEventListener('click', () => { if (state.source) openSource(state.source.documentId, state.source.page - 1); });
$('#next-page').addEventListener('click', () => { if (state.source) openSource(state.source.documentId, state.source.page + 1); });
$('#fact-document').addEventListener('change', () => {
  const document = state.documents.find(d => d.id === $('#fact-document').value);
  if (document) { $('#fact-year').value = document.report_year; $('#fact-page').value = 1; $('#fact-page').max = document.page_count; }
});

async function init() {
  try {
    const [health, catalog] = await Promise.all([api('/api/health'), api('/api/catalog')]);
    state.catalog = catalog;
    const providerLabel = health.chat_provider === 'gemini'
      ? (health.chat_ready ? `${health.chat_model} · API` : 'Gemini · cần API key')
      : (health.answer_mode === 'generated' ? 'AI đã cấu hình · có nguồn dẫn' : 'Trích xuất nguồn · cục bộ');
    $('#model-status').replaceChildren(node('span', 'status-dot'), document.createTextNode(providerLabel));
    $('#provider-notice').hidden = health.chat_provider !== 'gemini';
    $('#fact-metric').replaceChildren(...Object.entries(catalog.metrics).map(([key, label]) => option(key, label)));
    await refresh();
    if (state.documents.some(d => !d.is_demo)) { $('#dataset').value = 'real'; updateFilters(false); }
  } catch (error) { $('#model-status').textContent = 'Chưa kết nối được máy chủ'; toast(error.message, true); }
}
init();
