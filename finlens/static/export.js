'use strict';

// All exports use a detached snapshot of the request that produced the answer.
const FinLensExport = (() => {
  const statuses = { answered: 'Đã trả lời', partial: 'Trả lời một phần', evidence_found: 'Tìm thấy nguồn liên quan', insufficient_evidence: 'Chưa đủ bằng chứng' };
  const modes = { calculated: 'Tính từ chỉ tiêu xác nhận', generated: 'Diễn giải bằng AI', extractive: 'Trích xuất nguồn' };
  const escape = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/([\\`*_{}\[\]()#+.!|~-])/g, '\\$1');
  const freeze = value => {
    if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); }
    return value;
  };

  function snapshot(request, response, exportedAt = new Date().toISOString()) {
    const packet = JSON.parse(JSON.stringify({ schema_version: 1, captured_at: exportedAt, request, response }));
    const demo = packet.request.dataset === 'demo' || packet.response.citations?.some(c => c.is_demo) || packet.response.comparisons?.some(c => c.is_demo);
    packet.notices = [
      demo ? 'Có dữ liệu giả lập; không dùng làm kết luận về doanh nghiệp thực.' : 'Bản xuất giữ kết quả tại thời điểm phân tích; cần đối chiếu tài liệu gốc.',
      packet.response.review_required ? 'Cần xác nhận số liệu trước khi tính toán; nguồn gợi ý chưa phải số liệu đã duyệt.' : 'Chỉ tiêu xác nhận do người dùng đối chiếu; không phải chứng nhận kiểm toán độc lập.',
      'Trích dẫn hiện diện không tự bảo đảm nội dung diễn giải được nguồn hỗ trợ. Liên kết nguồn cục bộ cần cùng kho FinLens để mở.'
    ];
    return freeze(packet);
  }

  function markdown(packet) {
    const { request: q, response: r } = packet;
    const lines = ['# Kết quả phân tích FinLens', '', `Thời điểm: ${escape(packet.captured_at)}`, '',
      `Doanh nghiệp: ${escape(q.company)}`, `Năm đã chọn: ${escape(q.years.join(', '))}`,
      `Phạm vi: ${q.scope === 'consolidated' ? 'Hợp nhất' : 'Riêng lẻ'}`,
      `Dữ liệu: ${q.dataset === 'demo' ? 'Giả lập' : q.dataset === 'real' ? 'Tài liệu thật' : 'Tất cả'}`,
      `Trạng thái: ${escape(statuses[r.status] || r.status)}`,
      `Cách trả lời: ${escape(modes[r.mode] || r.mode)}${r.generation_model ? `; mô hình: ${escape(r.generation_model)}` : ''}`,
      '', '## Câu hỏi', '', escape(q.question), '', '## Câu trả lời', '', escape(r.answer), '', '## Lưu ý', '', ...packet.notices.map(n => `- ${escape(n)}`)];
    for (const c of r.comparisons || []) {
      lines.push('', `## ${escape(c.label)}`, '', '| Năm gốc | Năm so sánh | Giá trị gốc | Giá trị so sánh | Chênh lệch | Đơn vị |', '| --- | --- | --- | --- | --- | --- |',
        `| ${escape(c.from_year)} | ${escape(c.to_year)} | ${escape(c.from_value)} | ${escape(c.to_value)} | ${escape(c.delta)} | ${escape(`${c.unit_label} ${c.currency}`)} |`,
        '', `Tăng trưởng: ${c.growth_percent == null ? 'Không tính' : `${escape(c.growth_percent)}%`}`,
        `Công thức: ${escape(c.formula || c.note)}`, `Nguồn: ${escape((c.citations || []).map(s => s.id).join(', '))}`);
    }
    for (const ratio of r.ratios || []) {
      lines.push('', `## ${escape(ratio.label)}`, '', `Công thức: ${escape(ratio.formula)}`, '', '| Năm | Kết quả | Tử số | Mẫu số | Tiền tệ đầu vào | Nguồn |', '| --- | --- | --- | --- | --- | --- |');
      for (const v of ratio.values) lines.push(`| ${escape(v.year)} | ${escape(v.value)} ${escape(ratio.unit)} | ${escape(v.numerator)} | ${escape(v.denominator)} | ${escape(v.currency)} | ${escape((v.sources || []).join(', '))} |`);
    }
    if (r.missing_inputs?.length) lines.push('', '## Dữ liệu còn thiếu', '', ...r.missing_inputs.map(n => `- ${escape(n)}`));
    if (r.warnings?.length) lines.push('', '## Cảnh báo của hệ thống', '', ...r.warnings.map(n => `- ${escape(n)}`));
    lines.push('', '## Nguồn đối chiếu');
    if (!r.citations?.length) lines.push('', 'Không có trích dẫn trong kết quả này.');
    for (const c of r.citations || []) {
      lines.push('', `### ${escape(c.id)} — ${escape(c.filename)}`, '',
        `${escape(c.company)}; năm báo cáo ${escape(c.report_year)}; ${escape(c.source_label || `trang ${c.page}`)}${c.period_label ? `; kỳ ${escape(c.period_label)}` : ''}${c.is_demo ? '; GIẢ LẬP' : ''}`,
        `Mã tài liệu: ${escape(c.document_id)}`, `Đường dẫn trong FinLens: ${escape(c.url || `/api/documents/${encodeURIComponent(c.document_id)}/pages/${c.page}`)}`,
        '', 'Đoạn trích:', '', ...String(c.excerpt || '').split(/\r?\n/).map(line => `> ${escape(line)}`));
    }
    lines.push('', '## Thông tin xử lý', '', `Tổng thời gian: ${escape(r.latency_ms)} ms`, `Truy xuất: ${escape(r.retrieval)}`);
    if (r.timings_ms) lines.push(`Truy xuất / sinh đáp án: ${escape(r.timings_ms.retrieval)} / ${escape(r.timings_ms.generation)} ms`);
    lines.push(`Dùng mô hình dự phòng: ${r.fallback_used ? 'Có' : 'Không'}`, '', 'Có thể tải thêm bản JSON để lưu toàn bộ kết quả, lượt gọi mô hình và token được nhà cung cấp báo cáo.');
    return lines.join('\n') + '\n';
  }

  function file(packet, format) {
    if (!['json', 'md'].includes(format)) throw new Error('Định dạng xuất không hợp lệ.');
    const slug = String(packet.request.company).normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-zA-Z0-9_-]+/g, '-').slice(0, 60) || 'analysis';
    return { name: `finlens-${slug}-${packet.captured_at.replace(/[^0-9]/g, '').slice(0, 14)}.${format}`,
      type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8',
      text: format === 'json' ? JSON.stringify(packet, null, 2) + '\n' : markdown(packet) };
  }
  return Object.freeze({ snapshot, markdown, file });
})();

if (typeof module !== 'undefined' && module.exports) module.exports = FinLensExport;
