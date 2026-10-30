'use strict';

const $ = (selector) => document.querySelector(selector);
const state = { documents: [], facts: [], catalog: {}, source: null, view: 'analysis', busy: false, revision: 0, suggestions: [], suggestionRevision: 0 };
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
  renderPeriodNotice();
}

function renderPeriodNotice() {
  const years = new Set([...document.querySelectorAll('#year-options input:checked')].map(el => Number(el.value)));
  const documents = state.documents.filter(d => d.company === $('#company').value && d.scope === $('#scope').value && d.is_demo === ($('#dataset').value === 'demo') && (!years.size || years.has(d.report_year)));
  const labels = [...new Set(documents.filter(d => d.allowed_pages || (d.period_end && d.period_end !== `${d.report_year}-12-31`)).map(d => d.period_label).filter(Boolean))];
  $('#period-notice').hidden = !labels.length;
  $('#period-notice').textContent = labels.join(' · ');
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
  const factDocument = state.documents.find(d => d.id === $('#fact-document').value);
  if (factDocument) {
    $('#fact-page').max = factDocument.page_count;
    if (!selectedDocument || (factDocument.allowed_pages && !factDocument.allowed_pages.includes(Number($('#fact-page').value)))) {
      $('#fact-page').value = factDocument.allowed_pages?.[0] || 1;
      $('#fact-year').value = factDocument.report_year;
    }
  }
  $('#fact-button').disabled = !$('#fact-document').value;
  $('#fact-preview').disabled = !$('#fact-document').value;
  const suggestionDocument = $('#suggestion-document').value;
  $('#suggestion-document').replaceChildren(...state.documents.filter(d => !d.is_demo && d.has_pdf).map(d => option(d.id, `${d.company} · ${d.report_year}`)));
  if ([...$('#suggestion-document').options].some(o => o.value === suggestionDocument)) $('#suggestion-document').value = suggestionDocument;
  if (!$('#suggestion-document').options.length) $('#suggestion-document').append(option('', 'Hãy nhập PDF trước'));
  $('#suggestion-button').disabled = !$('#suggestion-document').value;
  const suggestionSource = state.documents.find(d => d.id === $('#suggestion-document').value);
  $('#suggestion-page').max = suggestionSource?.page_count || 250;
  if (suggestionSource?.allowed_pages && !suggestionSource.allowed_pages.includes(Number($('#suggestion-page').value))) $('#suggestion-page').value = suggestionSource.allowed_pages[0];
  renderSuggestions();
}

function clearSuggestions() {
  state.suggestionRevision += 1;
  state.suggestions = [];
  $('#suggestion-list').replaceChildren(); $('#suggestion-warnings').replaceChildren();
  $('#suggestion-status').textContent = 'Chọn trang bảng số liệu rồi bấm Tìm số liệu trên trang.';
  $('#suggestion-button').disabled = !$('#suggestion-document').value;
  $('#suggestion-button').textContent = 'Tìm số liệu trên trang ↗';
  $('#draft-proof').hidden = true;
}

function renderSuggestions() {
  $('#suggestion-list').replaceChildren();
  for (const candidate of state.suggestions) {
    const card = node('article', 'suggestion-card');
    const content = node('div');
    const confirmed = state.facts.some(f => f.verified && f.document_id === candidate.document_id && f.page === candidate.page && f.metric === candidate.metric && f.year === candidate.year && f.value === candidate.value && f.unit === candidate.unit && f.currency === candidate.currency);
    content.append(node('strong', '', `${candidate.label} · ${candidate.year}`),
      node('div', 'doc-meta', `${formatNumber(candidate.value)} ${state.catalog.units[candidate.unit]} ${candidate.currency}`),
      node('div', 'doc-meta', confirmed ? 'Đã có bản xác nhận trên trang này' : 'Bản nháp · chưa dùng để tính toán'));
    const choose = node('button', 'button secondary', 'Đối chiếu ↗'); choose.type = 'button';
    choose.addEventListener('click', () => {
      $('#fact-document').value = candidate.document_id; $('#fact-page').value = candidate.page;
      $('#fact-page').max = state.documents.find(d => d.id === candidate.document_id)?.page_count || 250;
      $('#fact-metric').value = candidate.metric; $('#fact-year').value = candidate.year;
      $('#fact-value').value = candidate.value; $('#fact-unit').value = candidate.unit; $('#fact-currency').value = candidate.currency;
      $('#fact-note').value = `Đối chiếu dòng ${candidate.source_label}, cột ${candidate.year}, trang PDF ${candidate.page}; đơn vị ${state.catalog.units[candidate.unit]} ${candidate.currency}.`.slice(0, 600);
      $('#fact-confirm').checked = false;
      const pdf = node('a', 'text-link', 'Mở PDF gốc để kiểm tra ↗');
      pdf.href = `/api/documents/${encodeURIComponent(candidate.document_id)}/pdf#page=${candidate.page}`;
      pdf.target = '_blank'; pdf.rel = 'noopener';
      const yearNote = candidate.year_basis === 'document_fiscal_period' ? ' · Năm được ghép theo kỳ tài chính của tài liệu' : candidate.year_basis === 'period_date' ? ' · Năm được đối chiếu từ ngày đầu/cuối kỳ' : '';
      $('#draft-proof').replaceChildren(node('strong', '', 'Đang đối chiếu bản nháp'), node('p', '', `Giá trị gốc: ${candidate.raw_value} · Cột năm ${candidate.year} · Trang ${candidate.page}${yearNote}`), node('p', 'draft-row', candidate.row_excerpt), pdf);
      $('#draft-proof').hidden = false;
      $('.fact-entry').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    card.append(content, choose); $('#suggestion-list').append(card);
  }
}

async function loadSuggestions() {
  const documentId = $('#suggestion-document').value;
  const page = Number($('#suggestion-page').value);
  if (!documentId) return;
  const revision = ++state.suggestionRevision;
  state.suggestions = []; renderSuggestions(); $('#suggestion-warnings').replaceChildren();
  $('#suggestion-button').disabled = true; $('#suggestion-button').textContent = 'Đang đọc trang…';
  $('#suggestion-status').textContent = 'Đang đọc bảng trên máy này…';
  try {
    const result = await api(`/api/documents/${encodeURIComponent(documentId)}/suggestions?page=${page}`);
    if (revision !== state.suggestionRevision) return;
    state.suggestions = result.candidates;
    $('#suggestion-status').textContent = `${result.candidates.length} đề xuất. ${result.notice}`;
    $('#suggestion-warnings').replaceChildren(...result.warnings.map(w => node('p', 'warning', w)));
    renderSuggestions();
  } catch (error) { if (revision === state.suggestionRevision) $('#suggestion-status').textContent = error.message; }
  finally { if (revision === state.suggestionRevision) { $('#suggestion-button').disabled = false; $('#suggestion-button').textContent = 'Tìm số liệu trên trang ↗'; } }
}

$('#suggestion-form').addEventListener('submit', event => { event.preventDefault(); loadSuggestions(); });
$('#suggestion-document').addEventListener('change', () => {
  clearSuggestions();
  const document = state.documents.find(d => d.id === $('#suggestion-document').value);
  $('#suggestion-page').value = document?.allowed_pages?.[0] || 1;
  $('#suggestion-page').max = document?.page_count || 250;
});
$('#suggestion-page').addEventListener('input', clearSuggestions);
$('#source-suggest').addEventListener('click', () => {
  if (!state.source) return;
  clearSuggestions(); setView('facts');
  $('#suggestion-document').value = state.source.documentId; $('#suggestion-page').value = state.source.page;
  $('#suggestion-page').max = state.documents.find(d => d.id === state.source.documentId)?.page_count || 250;
  loadSuggestions(); $('.suggestion-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

function renderDocuments() {
  $('#document-list').replaceChildren();
  if (!state.documents.length) $('#document-list').append(node('p', 'empty-list', 'Kho tài liệu đang trống. Nạp dữ liệu demo để khám phá hoặc tải báo cáo PDF đầu tiên.'));
  for (const document of state.documents) {
    const row = node('article', 'document-row');
    const details = node('div', 'doc-details');
    const title = node('p', 'doc-title', `${document.company} · ${document.report_year}`);
    if (document.is_demo) title.append(node('span', 'demo-tag', 'GIẢ LẬP'));
    details.append(title, node('div', 'doc-meta', `${document.scope === 'consolidated' ? 'Hợp nhất' : 'Riêng lẻ'} · ${document.page_count} trang · ${document.allowed_pages ? `${document.allowed_pages.length} trang trong phạm vi phân tích` : `${document.chunk_count} đoạn nguồn`}`), node('div', 'doc-meta', document.filename));
    if (document.period_label) details.append(node('div', 'doc-meta', document.period_label));
    if (document.source_url && /^https:\/\//.test(document.source_url)) {
      const source = node('a', 'doc-meta', `Nguồn: ${document.source_publisher || 'Website công bố'} ↗`);
      source.href = document.source_url; source.target = '_blank'; source.rel = 'noopener noreferrer';
      details.append(source);
    }
    if (document.warnings.length) details.append(node('div', 'doc-meta', document.warnings.join(' ')));
    const button = node('button', '', 'Xem ↗');
    button.addEventListener('click', () => openSource(document.id, document.allowed_pages?.[0] || 1));
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
    $('#source-label').textContent = `${source.document.company} · ${source.document.report_year}${source.document.is_demo ? ' · GIẢ LẬP' : ''}${source.document.period_label ? ` · ${source.document.period_label}` : ''}`;
    $('#source-page').textContent = `Trang ${page}`;
    $('#source-text').textContent = source.text || '(Trang không có văn bản trích xuất)';
    const metadata = state.documents.find(d => d.id === documentId);
    $('#prev-page').disabled = page <= 1;
    $('#next-page').disabled = page >= (metadata?.page_count || page);
    $('#source-pdf').hidden = !source.document.has_pdf;
    $('#source-suggest').hidden = !source.document.has_pdf || source.in_scope === false;
    $('.source-footnote').textContent = source.in_scope === false
      ? 'Trang này nằm ngoài phần báo cáo được chọn để phân tích; chỉ mở để tham khảo PDF gốc.'
      : 'Nội dung trích xuất có thể mất định dạng bảng. Hãy mở PDF gốc để kiểm tra trước khi xác nhận chỉ tiêu.';
    $('#source-pdf').href = `/api/documents/${encodeURIComponent(documentId)}/pdf#page=${page}`;
    if (window.innerWidth < 1000) $('.source-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) { toast(error.message, true); }
}

function renderAnswer(result) {
  $('#result-empty').hidden = true; $('#result-content').hidden = false;
  $('#result-title').textContent = statusLabels[result.status] || result.status;
  $('#result-meta').textContent = `${modeLabels[result.mode]}${result.generation_model && result.mode === 'generated' ? ` · ${result.generation_model}${result.fallback_used ? ' (dự phòng)' : ''}` : ''} · ${result.latency_ms} ms`;
  $('#answer-text').textContent = result.answer;
  $('#comparison-tables').replaceChildren();
  for (const ratio of result.ratios || []) {
    const card = node('article', 'comparison-card');
    card.append(node('div', 'comparison-top', ratio.label));
    const table = node('table');
    const head = node('thead'); const headers = node('tr');
    const body = node('tbody'); const row = node('tr');
    for (const observation of ratio.values) {
      headers.append(node('th', '', String(observation.year)));
      row.append(node('td', '', `${formatNumber(observation.value)} ${ratio.unit}`));
    }
    head.append(headers); body.append(row); table.append(head, body);
    card.append(table, node('div', 'formula', `Công thức: ${ratio.formula}`));
    $('#comparison-tables').append(card);
  }
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
    $('#draft-proof').hidden = true;
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
$('#year-options').addEventListener('change', () => { resetAnalysis(); renderPeriodNotice(); });
$('#prev-page').addEventListener('click', () => { if (state.source) openSource(state.source.documentId, state.source.page - 1); });
$('#next-page').addEventListener('click', () => { if (state.source) openSource(state.source.documentId, state.source.page + 1); });
$('#fact-document').addEventListener('change', () => {
  $('#fact-confirm').checked = false; $('#draft-proof').hidden = true;
  const document = state.documents.find(d => d.id === $('#fact-document').value);
  if (document) { $('#fact-year').value = document.report_year; $('#fact-page').value = document.allowed_pages?.[0] || 1; $('#fact-page').max = document.page_count; }
});

async function init() {
  try {
    const [health, catalog] = await Promise.all([api('/api/health'), api('/api/catalog')]);
    state.catalog = catalog;
    const providerLabel = health.chat_provider === 'gemini'
      ? (health.chat_ready ? `${health.chat_model} · ${health.fallback_models?.length || 0} dự phòng` : 'Gemini · cần API key')
      : 'Trích xuất nguồn · cục bộ';
    $('#model-status').replaceChildren(node('span', 'status-dot'), document.createTextNode(providerLabel));
    $('#provider-notice').hidden = health.chat_provider !== 'gemini';
    $('#fact-metric').replaceChildren(...Object.entries(catalog.metrics).map(([key, label]) => option(key, label)));
    await refresh();
    if (state.documents.some(d => !d.is_demo)) { $('#dataset').value = 'real'; updateFilters(false); }
  } catch (error) { $('#model-status').textContent = 'Chưa kết nối được máy chủ'; toast(error.message, true); }
}
init();
