# Kế hoạch xây dựng FinLens

Ngày bắt đầu: 27/10/2026. Chủ dự án: sinh viên năm 4 ngành AI.

## 1. Mục tiêu sản phẩm

FinLens hỗ trợ đọc báo cáo tài chính tiếng Việt, tìm bằng chứng theo doanh nghiệp/năm/loại báo cáo, trả lời có dẫn nguồn và so sánh chỉ tiêu bằng phép tính xác định. Người dùng có thể mở đúng trang nguồn để kiểm chứng. Phạm vi là phân tích tài liệu, không sinh khuyến nghị mua bán chứng khoán.

## 2. Phạm vi triển khai hiện tại

Xây một MVP chạy xuyên suốt trên máy cá nhân: API Python, giao diện web tiếng Việt, SQLite, xử lý PDF có văn bản, tìm kiếm BM25, câu trả lời trích xuất có nguồn, chỉ tiêu đã xác nhận và so sánh bằng Decimal. Tích hợp tùy chọn Ollama cho embedding ngữ nghĩa, hợp nhất xếp hạng RRF và sinh câu trả lời. Khi chưa có mô hình, giao diện thông báo rõ chế độ trích xuất.

Chọn SQLite để cài đặt và chạy thử đơn giản. PostgreSQL/pgvector, hàng đợi xử lý, xác thực và triển khai nhiều người dùng là phần phát triển tiếp theo sau khi có bộ dữ liệu thật đủ lớn. Không gọi chế độ BM25 là tìm kiếm ngữ nghĩa.

Dữ liệu mẫu là dữ liệu giả lập của doanh nghiệp hư cấu, có nhãn ở tài liệu, API và giao diện. Số liệu từ PDF mới nhập phải được người dùng đối chiếu và xác nhận trước khi dùng để tính toán. Không tự động coi số đọc từ PDF là chính xác.

## 3. Các mốc và commit

| Mốc | Sản phẩm bàn giao | Điều kiện hoàn thành |
| --- | --- | --- |
| M0 — Kế hoạch | Kế hoạch, cấu trúc repo, quy tắc dữ liệu | Khởi tạo Git và commit tài liệu |
| M1 — Dữ liệu và truy xuất | Lưu tài liệu/trang/chunk/chỉ tiêu, nhập PDF, BM25, bộ mẫu | Kiểm tra trùng dữ liệu, lọc metadata, nguồn trang và từ chối PDF lỗi |
| M2 — Hỏi đáp và tính toán | Câu trả lời có nguồn, so sánh Decimal, Ollama tùy chọn, API | Kiểm tra thiếu dữ liệu, khác đơn vị, xung đột, lỗi mô hình và citation |
| M3 — Giao diện | Dashboard tiếng Việt, hỏi đáp, tải PDF, xem nguồn, xác nhận chỉ tiêu | Chạy thử các luồng chính bằng trình duyệt |
| M4 — Đánh giá và bàn giao | Bộ câu hỏi, báo cáo đánh giá, README, hướng dẫn chạy, CI | Test toàn bộ, đánh giá có thể tái lập, trạng thái Git sạch |

Mỗi mốc được commit sau khi kiểm tra. Các lỗi phát hiện ở mốc sau có commit sửa riêng hoặc được ghi rõ trong commit của mốc đó. Không push lên dịch vụ bên ngoài nếu chưa có yêu cầu.

## 4. Thiết kế dữ liệu và kiểm chứng

- Document: mã nội bộ, tên doanh nghiệp, năm báo cáo, phạm vi hợp nhất/riêng lẻ, tên tệp an toàn, hash, nhãn dữ liệu giả lập.
- Page/chunk: số trang PDF bắt đầu từ 1, văn bản và bảng trích xuất, nguồn thuộc một document.
- Fact: chỉ tiêu, năm số liệu, Decimal, đơn vị, tiền tệ, tài liệu/trang, trạng thái xác nhận. Năm số liệu có thể khác năm phát hành báo cáo.
- Truy xuất luôn áp dụng bộ lọc trước khi xếp hạng. Nguồn trả lời phải thuộc tập tài liệu được chọn.
- Không so sánh khi thiếu năm, đơn vị không tương thích, loại báo cáo khác nhau hoặc có nhiều giá trị xác nhận mâu thuẫn. Mẫu số bằng 0 trả về tỷ lệ không xác định.
- Chỉ tiêu được xác nhận bằng đối chiếu trực tiếp với trang nguồn; vẫn ghi rõ đây là xác nhận của người dùng, không phải chứng nhận kiểm toán.
- Tài liệu là dữ liệu không đáng tin: không thi hành hướng dẫn trong PDF; mô hình không được tự chạy mã hay tự tạo phép tính.

## 5. Kiến trúc

`PDF → PyMuPDF → trang + bảng → chunk theo trang → SQLite → BM25 [+ embedding Ollama/RRF] → bằng chứng → trích xuất hoặc LLM → câu trả lời + citations`

`Chỉ tiêu đã xác nhận → lọc doanh nghiệp/phạm vi/năm → kiểm tra xung đột/đơn vị → Decimal → bảng so sánh + công thức + nguồn`

Ứng dụng mặc định chỉ lắng nghe localhost. Dữ liệu tải lên và cơ sở dữ liệu nằm trong thư mục `data/` được Git bỏ qua. API giới hạn kích thước tệp, kiểm tra PDF, kiểm soát đường dẫn và không trả lỗi chứa nội dung bí mật.

## 6. Lộ trình đồ án 12–16 tuần

1. Tuần 1–2: chốt người dùng, 5 doanh nghiệp cùng ngành, thu 15 báo cáo công khai, lưu URL/điều kiện sử dụng/ngày tải; kiểm tra 3–5 PDF khó.
2. Tuần 3–4: parser, chuẩn hóa đơn vị/phạm vi, chunk văn bản và bảng, đối chiếu thủ công mẫu.
3. Tuần 5–6: BM25, dense retrieval, hybrid RRF; tạo tập development có bằng chứng chuẩn.
4. Tuần 7–8: chỉ tiêu có cấu trúc, công cụ tính toán, citation và từ chối khi thiếu nguồn.
5. Tuần 9–10: reranker, thử nghiệm ablation; tối ưu dựa trên lỗi đo được.
6. Tuần 11–12: hoàn thiện UI, kiểm thử người dùng, triển khai môi trường trình diễn.
7. Tuần 13–16: bộ đánh giá độc lập 150–200 câu, báo cáo đồ án, demo và sửa lỗi.

## 7. Đánh giá

MVP đi kèm một bộ smoke evaluation trên dữ liệu giả lập để kiểm tra cơ chế; không dùng kết quả này làm bằng chứng hiệu quả trên tài chính thực tế. Test tự động kiểm tra tính đúng của số học, lọc metadata, dẫn nguồn và từ chối.

Bộ đánh giá đồ án cần 150–200 câu do người kiểm tra nguồn, chia train/development/test theo tài liệu hoặc doanh nghiệp để tránh rò rỉ. Có câu tra cứu, so sánh, nhiều nguồn, câu thiếu dữ liệu, nhầm đơn vị và chỉ dẫn độc hại trong tài liệu. Báo cáo Recall@5, MRR, độ đúng số liệu, độ đúng và đủ citation, precision/recall của từ chối, độ trễ p50/p95 và chi phí. Đối chiếu baseline vector-only, BM25, hybrid và hybrid+reranker trên cùng dữ liệu/mô hình.

## 8. Giới hạn cần công bố khi bàn giao MVP

- Không mặc định có OCR, hiểu biểu đồ, reranker hoặc mô hình đã tải sẵn.
- Không bảo đảm parser nhận diện đúng bảng tài chính đa trang; dùng xác nhận chỉ tiêu để bảo vệ luồng tính toán.
- Kiểm tra citation hợp lệ chỉ chứng minh nguồn tồn tại, không chứng minh câu văn được nguồn suy ra; phải đánh giá bằng người hoặc bộ chấm riêng.
- Chưa có dữ liệu doanh nghiệp thật hay kết quả benchmark thực tế cho đến khi thu thập và gán nhãn.
- Chưa sẵn sàng công khai nhiều người dùng trước khi bổ sung xác thực, phân quyền, giới hạn tài nguyên và đánh giá bảo mật.
