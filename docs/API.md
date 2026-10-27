# API và kịch bản demo

Mặc định: `http://127.0.0.1:8000`. Hợp đồng ở `/openapi.json`. Swagger/ReDoc từ CDN được tắt để ứng dụng không phụ thuộc tài nguyên bên ngoài.

| Phương thức | Đường dẫn | Ý nghĩa |
| --- | --- | --- |
| GET | `/api/health` | Phiên bản, chế độ |
| GET | `/api/catalog` | Chỉ tiêu, đơn vị |
| GET | `/api/documents` | Tài liệu và cảnh báo |
| POST | `/api/demo` | Nạp fixture, không tạo trùng |
| POST | `/api/documents?company=...&year=2024&scope=consolidated&filename=report.pdf` | Body PDF, `Content-Type: application/pdf` |
| GET | `/api/documents/{id}/pages/{page}` | Văn bản trang và metadata |
| GET | `/api/documents/{id}/pdf` | PDF gốc; demo không có PDF |
| GET | `/api/facts?company=...&scope=consolidated&dataset=real` | Chỉ tiêu đã lưu |
| POST | `/api/facts` | Xác nhận chỉ tiêu |
| DELETE | `/api/facts/{id}` | Thu hồi chỉ tiêu thật |
| POST | `/api/compare` | So sánh chỉ tiêu |
| POST | `/api/ask` | Hỏi đáp và nguồn |
| POST | `/api/index` | Lập embedding còn thiếu |

`scope`: `consolidated`/`standalone`. `dataset`: `real`/`demo`/`all`; UI không có chọn `all`. Hỏi đáp mặc định dùng `real`.

## Hỏi đáp

```json
{
  "question": "So sánh doanh thu và lợi nhuận sau thuế năm 2023 và 2024",
  "company": "An Phát Demo",
  "years": [2023, 2024],
  "scope": "consolidated",
  "dataset": "demo",
  "top_k": 5
}
```

Phản hồi gồm `status`, `mode`, `answer`, `citations`, `comparisons`, `warnings`, `retrieval`, `latency_ms`.

| Trường | Giá trị | Ý nghĩa |
| --- | --- | --- |
| status | answered | Đã tính được hoặc tạo câu trả lời |
| status | evidence_found | Chỉ trích đoạn liên quan để đối chiếu |
| status | partial | Trả lời được một phần |
| status | insufficient_evidence | Thiếu dữ liệu hoặc cần sửa bộ lọc |
| mode | calculated | Từ chỉ tiêu xác nhận, dùng Decimal |
| mode | extractive | Trích nguyên văn, không chạy LLM |
| mode | generated | LLM đã trả lời, citation hợp lệ về hình thức |

## Xác nhận chỉ tiêu

```json
{
  "document_id": "ID_PDF_DA_NHAP",
  "page": 8,
  "metric": "revenue",
  "year": 2024,
  "value": "1250.50",
  "unit": "billion",
  "currency": "VND",
  "confirmed": true,
  "note": "Đã đối chiếu dòng doanh thu thuần, cột 2024, đơn vị tỷ VND."
}
```

Giá trị là chuỗi dùng dấu chấm thập phân, không phân cách hàng nghìn; tối đa 24 chữ số nguyên và 8 chữ số thập phân. Đơn vị: `one`/`thousand`/`million`/`billion`. Tiền tệ VND/USD/EUR, không chuyển tỷ giá. Thu hồi ID cũ rồi nhập lại khi sửa; các số đã xác nhận khác nhau gây dừng so sánh.

## So sánh

```json
{
  "company": "An Phát Demo",
  "metric": "revenue",
  "from_year": 2023,
  "to_year": 2024,
  "scope": "consolidated",
  "dataset": "demo"
}
```

Compare trả 409 khi không tính được. Ask trả 200 với trạng thái từ chối để UI trình bày lý do. Số tiền trong phản hồi là chuỗi để giữ độ chính xác.

## Kịch bản demo

1. Giới thiệu bộ giả lập và giới hạn MVP.
2. So sánh doanh thu/lợi nhuận 2023–2024.
3. Mở F2 và thuyết minh trang 2; hỏi nguyên nhân chi phí tăng.
4. Hỏi “CEO nhận lương bao nhiêu năm 2024?” để minh họa từ chối.
5. Nhập PDF, xác nhận chỉ tiêu/trang, tra cứu lại giá trị.
6. Chạy smoke; giải thích kết quả giả lập chưa chứng minh chất lượng thực tế.
