'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const exporter = require('../finlens/static/export.js');

const request = () => ({ question: 'So sánh doanh thu 2023 và 2024', company: 'FPT', years: [2023, 2024], scope: 'consolidated', dataset: 'real' });
const response = () => ({ status: 'partial', mode: 'calculated', answer: 'Tăng 2.50% [F1]', warnings: ['Chưa đủ dữ liệu trả nợ'], missing_inputs: ['Nợ ngắn hạn'], review_required: true,
  comparisons: [{ label: 'Doanh thu', from_year: 2023, to_year: 2024, from_value: '100.00', to_value: '102.50', delta: '2.50', growth_percent: '2.50', unit_label: 'Tỷ', currency: 'VND', formula: '(102.50 − 100.00) / 100.00 × 100', citations: [{ id: 'F1' }] }],
  ratios: [{ label: 'Thanh khoản', formula: 'Tài sản / Nợ', unit: 'lần', values: [{ year: 2024, value: '1.20', numerator: '120000000000', denominator: '100000000000', currency: 'VND', sources: ['F1'] }] }],
  citations: [{ id: 'F1', filename: 'report.pdf', document_id: 'doc-1', page: 5, company: 'FPT', report_year: 2024, excerpt: 'Doanh thu 102,50', is_demo: false }],
  latency_ms: 10, timings_ms: { retrieval: 7, generation: 0 }, generation_attempts: [{ model: 'model-x', usage: null }], generation_usage: null });

test('snapshot preserves the original filters and decimals after caller edits', () => {
  const q = request(), r = response(), p = exporter.snapshot(q, r, '2026-11-01T00:00:00Z');
  q.years.push(2025); q.company = 'changed'; r.citations[0].excerpt = 'changed';
  assert.equal(p.request.company, 'FPT'); assert.deepEqual(p.request.years, [2023, 2024]);
  assert.equal(p.response.citations[0].excerpt, 'Doanh thu 102,50');
  assert.throws(() => p.request.years.push(2025), TypeError);
  const raw = JSON.parse(exporter.file(p, 'json').text);
  assert.equal(raw.response.comparisons[0].from_value, '100.00');
  assert.equal(raw.response.generation_usage, null);
  assert.deepEqual(raw.response.generation_attempts, r.generation_attempts);
});

test('readable export retains source, missing inputs, warnings and calculation inputs', () => {
  const text = exporter.markdown(exporter.snapshot(request(), response()));
  for (const phrase of ['Năm đã chọn: 2023, 2024', 'Trả lời một phần', 'Cần xác nhận số liệu', 'Nợ ngắn hạn', 'Chưa đủ dữ liệu trả nợ', 'Tài sản / Nợ', '120000000000', '100000000000', 'Mã tài liệu: doc', 'trang 5', 'Doanh thu 102,50']) assert.ok(text.includes(phrase), phrase);
  assert.match(text, /\| --- \|[^\n]*\n\| 2023/);
  assert.ok(text.includes('102\\.50')); // Decimal text stays exact when Markdown is rendered.
});

test('report text cannot inject active HTML, links or a filename path', () => {
  const q = request(), r = response();
  q.company = '../../FPT:bad\\path';
  q.question = '<img src=x onerror=alert(1)> [open](https://evil.example)';
  r.answer = '<script>alert(1)</script>\n# forged heading';
  r.citations[0].excerpt = '</blockquote><script>alert(2)</script>\n![x](javascript:evil)';
  const p = exporter.snapshot(q, r), out = exporter.file(p, 'md');
  assert.ok(!out.text.includes('<script>')); assert.ok(!out.text.includes('<img'));
  assert.ok(out.text.includes('&lt;script&gt;')); assert.ok(out.text.includes('\\[open\\]\\('));
  assert.match(out.name, /^finlens-[A-Za-z0-9_-]+-\d{14}\.md$/);
  assert.equal(JSON.parse(exporter.file(p, 'json').text).response.answer, r.answer);
  assert.throws(() => exporter.file(p, 'html'));
});

test('demo and abstentions stay visibly labelled without inventing evidence', () => {
  const q = request(); q.dataset = 'demo';
  const p = exporter.snapshot(q, { status: 'insufficient_evidence', mode: 'extractive', answer: 'Không đủ dữ liệu', citations: [] });
  const text = exporter.markdown(p);
  assert.ok(text.includes('Có dữ liệu giả lập')); assert.ok(text.includes('Chưa đủ bằng chứng'));
  assert.ok(text.includes('Không có trích dẫn')); assert.equal(p.response.generation_usage, undefined);
});
