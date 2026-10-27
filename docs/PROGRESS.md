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
