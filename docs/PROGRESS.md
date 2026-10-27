# Nhật ký triển khai

## 27/10/2026 — M0

- Khởi tạo repository, ghi phạm vi MVP và lộ trình đồ án 12–16 tuần.
- Commit: `cc2a22f`.

## 27/10/2026 — M1

- Có SQLite lưu tài liệu/trang/chunk/chỉ tiêu, parser PDF có văn bản và bảng, bộ lọc doanh nghiệp/năm/phạm vi/dữ liệu demo.
- Có BM25 xử lý truy vấn tiếng Việt có dấu hoặc không dấu.
- Bộ mẫu: 6 báo cáo văn bản giả lập, 2 doanh nghiệp hư cấu, 30 chỉ tiêu; nạp lại không tạo trùng.
- Kiểm tra: `python -m unittest discover -s tests -v` — 5/5 đạt.
- Thư mục tạm của test nằm trong `data/test-runs/` để tương thích quyền ghi trên máy hiện tại.

## 27/10/2026 — M2

- API nhập PDF, truy vấn nguồn, xác nhận/thu hồi chỉ tiêu, so sánh và hỏi đáp.
- Decimal xử lý số học; chuẩn hóa đơn vị tương thích, từ chối khác tiền tệ/xung đột/thiếu dữ liệu, không tính tỷ lệ trên kỳ gốc bằng 0 hoặc âm.
- Chế độ trích xuất hoạt động không cần mô hình; adapter Ollama hỗ trợ embedding, cache, hybrid RRF và sinh câu trả lời có kiểm tra ID nguồn.
- Tự động quay về trích xuất khi mô hình lỗi hoặc trả citation không hợp lệ.
- Kiểm tra: 24/24 test đạt, bao gồm luồng PDF → xác nhận → tính toán → thu hồi qua API.
- Adapter AI được kiểm tra bằng phản hồi giả lập có kiểm soát; chưa chạy mô hình Ollama thật.
- Trên môi trường hiện tại, test API cần quyền chạy kết nối cục bộ; test thuần dữ liệu chạy được với quyền mặc định.

## 27/10/2026 — M3

- Giao diện web tiếng Việt có ba màn hình: phân tích, kho tài liệu, chỉ tiêu đã xác nhận; hỗ trợ màn hình hẹp.
- Có so sánh nhiều chỉ tiêu, công thức tính, dẫn nguồn mở trang, chuyển trang nguồn, liên kết PDF gốc, tải tệp và biểu mẫu xác nhận/thu hồi chỉ tiêu.
- Đã kiểm tra bằng trình duyệt: so sánh doanh thu +25% và lợi nhuận −10%, mở đúng nguồn năm 2024, chuyển sang thuyết minh trang 2, xem kho tài liệu và 30 chỉ tiêu mẫu.
- Khi đổi bộ lọc, kết quả cũ được xóa để tránh hiển thị nguồn của phạm vi trước.
- Phát hiện và sửa: năm trong truy vấn không được tính là bằng chứng nội dung; câu hỏi nguyên nhân tăng phải thu không bị chuyển nhầm sang luồng bắt buộc so sánh hai năm.
