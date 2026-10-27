# Kiến trúc FinLens MVP

## Thành phần

| Thành phần | Tệp | Trách nhiệm |
| --- | --- | --- |
| API | `finlens/api.py` | HTTP, upload, nguồn trang, chỉ tiêu và web |
| Lưu trữ | `finlens/store.py` | SQLite, giao dịch, metadata, cache embedding |
| Nhập liệu | `finlens/ingest.py` | Kiểm tra PDF, văn bản/bảng, chống trùng |
| Truy xuất | `finlens/retrieval.py` | BM25, cosine, RRF |
| Hỏi đáp | `finlens/engine.py` | Phân luồng, bằng chứng, fallback, citation |
| Số học | `finlens/finance.py` | Decimal, xung đột, đơn vị |
| Mô hình | `finlens/providers.py` | Ollama tùy chọn, timeout, kiểm tra phản hồi |
| UI | `finlens/static/` | HTML/CSS/JavaScript, không cần build |
| Đánh giá | `finlens/evaluate.py` | Smoke giả lập và kết quả JSON/Markdown |

## Nhập tài liệu

Trình duyệt gửi PDF nhị phân và metadata trong query string. Không dùng multipart. Giới hạn 20 MB, 250 trang, 2 triệu ký tự; từ chối PDF hỏng, có mật khẩu hoặc không đủ văn bản.

PyMuPDF đọc văn bản và bổ sung bảng. Bảng được đánh dấu cần đối chiếu PDF, chưa lưu bounding box hay ghép bảng nhiều trang. Chunk tối đa 1600 ký tự, overlap 200 ký tự, không vượt ranh giới trang. Số trang tính theo vị trí trong PDF từ 1, có thể khác số trang in trong tài liệu.

PDF lưu theo ID ngẫu nhiên thay vì tên tải lên. SHA-256 cùng doanh nghiệp/năm/phạm vi dùng chống trùng. Trang và chunk được lưu trong một giao dịch trước khi tài liệu được truy vấn.

## Truy xuất và trả lời

Lọc doanh nghiệp, năm báo cáo, hợp nhất/riêng lẻ, demo/thật trước khi xếp hạng. Chuẩn hóa tiếng Việt cho BM25; bỏ từ dừng và năm để tránh khớp chỉ vì năm.

Lệnh `index` gửi chunk đến Ollama được cấu hình, cache theo ID chunk và tên mô hình. Đủ cache thì kết hợp query embedding và BM25 bằng RRF; thiếu cache/mô hình lỗi thì trả cảnh báo, dùng BM25. Corpus hiện được đọc vào bộ nhớ; phù hợp MVP nhỏ, chưa có ANN index.

Câu hỏi số học đi qua bộ tính xác định. Câu hỏi nội dung lấy tối đa 5 chunk mặc định. Trích xuất trả `evidence_found`, không coi việc tìm được đoạn liên quan là giải đáp đầy đủ. LLM phải dẫn nguồn cuối mỗi đoạn; validator kiểm tra ID trong tập bằng chứng. Lỗi thì quay về trích xuất. Mô hình không được cấp công cụ thực thi mã.

## Chỉ tiêu và phiên bản

Giá trị lưu bằng chuỗi Decimal, có năm số liệu, đơn vị, tiền tệ, trang, ghi chú xác nhận. Năm số liệu tách với năm báo cáo vì tài liệu có thể có cột năm trước.

Trước khi tính, kiểm tra tất cả giá trị xác nhận cho cùng doanh nghiệp/phạm vi/năm. Chuẩn hóa nghìn/triệu/tỷ; nếu vẫn khác giá trị hoặc tiền tệ thì dừng. Nguồn thống nhất thì ưu tiên báo cáo mới hơn. Chưa có lựa chọn phiên bản restatement hay nguồn ưu tiên thủ công.

Chênh lệch = kỳ mới − kỳ gốc; tăng trưởng = chênh lệch / kỳ gốc × 100, làm tròn 2 chữ số. Kỳ gốc bằng 0/âm thì không tính %. Không chuyển đổi tỷ giá, không trộn dữ liệu demo/thật.

## Ranh giới

- PDF là dữ liệu không đáng tin; prompt giảm nguy cơ làm theo chỉ dẫn trong tài liệu, không chứng minh chống mọi prompt injection.
- Citation validator kiểm tra định dạng/nguồn, chưa kiểm tra entailment từng mệnh đề.
- `verified` là xác nhận của người dùng, không phải chứng nhận kiểm toán.
- UI dùng `textContent` cho nội dung tài liệu/mô hình, không render HTML tùy ý.
- Localhost, Host và Origin được kiểm tra; chưa có xác thực/phân quyền.
- Parser chạy ngoài event loop, chưa có worker tách riêng, antivirus, resource sandbox hay rate limit. Cần bổ sung trước khi nhận tài liệu từ người dùng không tin cậy.
- SQLite và quét BM25 phù hợp bộ nhỏ. Chuyển PostgreSQL/pgvector hoặc công cụ chuyên dụng theo số đo quy mô/độ trễ.

## Tham khảo

- [PyMuPDF: văn bản](https://pymupdf.readthedocs.io/en/latest/recipes-text.html), [bảng](https://pymupdf.readthedocs.io/en/latest/page.html)
- [Ollama: embedding](https://docs.ollama.com/api/embed), [chat](https://docs.ollama.com/api/chat)
- [FinQA: thiết kế câu hỏi suy luận số học](https://github.com/czyssrs/FinQA)
