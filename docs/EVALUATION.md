# Đánh giá smoke trên dữ liệu giả lập

Dữ liệu giả lập nhỏ, dùng kiểm tra cơ chế. Không đại diện hiệu quả trên báo cáo tài chính thật.

- Cấu hình: BM25 + trích xuất nguồn / tính toán Decimal, không gọi mô hình.
- Kiểm tra đạt: 24/24.
- Recall@5: 1.0 trên 7 câu có trang nguồn chuẩn.
- MRR@5: 1.0.
- Độ trễ p50/p95: 1.1/3.0 ms (trong tiến trình, không tính mạng/giao diện).

| Câu | Kết quả |
| --- | --- |
| calc-01 | Đạt |
| calc-02 | Đạt |
| calc-03 | Đạt |
| calc-04 | Đạt |
| calc-05 | Đạt |
| calc-06 | Đạt |
| calc-07 | Đạt |
| calc-08 | Đạt |
| lookup-01 | Đạt |
| lookup-02 | Đạt |
| lookup-03 | Đạt |
| retrieve-01 | Đạt |
| retrieve-02 | Đạt |
| retrieve-03 | Đạt |
| retrieve-04 | Đạt |
| retrieve-05 | Đạt |
| retrieve-06 | Đạt |
| retrieve-07 | Đạt |
| abstain-01 | Đạt |
| abstain-02 | Đạt |
| abstain-03 | Đạt |
| abstain-04 | Đạt |
| abstain-05 | Đạt |
| abstain-06 | Đạt |
