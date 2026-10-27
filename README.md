# FinLens

**Đọc số liệu. Hiểu câu chuyện.** Trợ lý phân tích tài liệu tài chính tiếng Việt với bằng chứng có thể mở để kiểm chứng.

MVP đã chạy xuyên suốt: nhập PDF → tìm đoạn nguồn → hỏi đáp → xác nhận chỉ tiêu → so sánh bằng phép tính xác định. Giao diện web tiếng Việt, không cần Node để chạy ứng dụng.

- [Kế hoạch và lộ trình đồ án 12–16 tuần](docs/PLAN.md)
- [Nhật ký từng mốc và commit](docs/PROGRESS.md)
- [Kiến trúc, dữ liệu và giới hạn](docs/ARCHITECTURE.md)
- [API và kịch bản demo](docs/API.md)
- [Báo cáo kiểm tra đã thực hiện](docs/VALIDATION.md)

## Chạy nhanh

Yêu cầu Python 3.11 trở lên. Chạy từ thư mục dự án bằng PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-tested.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m finlens seed
.\.venv\Scripts\python.exe -m finlens serve
```

Mở **http://127.0.0.1:8000**. Trên môi trường hiện tại đã có thư viện, có thể dùng ngay `python -m finlens serve`.

Linux/macOS: thay `.\.venv\Scripts\python.exe` bằng `.venv/bin/python`. Cơ sở dữ liệu và PDF nằm trong `data/`, được loại khỏi Git. Dùng `--data-dir` để chọn thư mục lưu khác.

## Thử trong hai phút

1. Nạp demo bằng lệnh `seed` hoặc nút **Nạp dữ liệu demo**: 6 báo cáo văn bản giả lập, 2 doanh nghiệp hư cấu, 30 chỉ tiêu.
2. Chọn **An Phát Demo**, năm **2023 và 2024**, câu hỏi mẫu **Doanh thu & lợi nhuận**, bấm **Phân tích**.
3. Kết quả: doanh thu 1000 → 1250 tỷ VND (+25%), lợi nhuận 100 → 90 tỷ VND (−10%). Bấm nguồn **F2** để mở trang năm 2024; trang tiếp theo chứa thuyết minh.
4. Chọn **Giải thích chi phí**. Khi chưa cấu hình mô hình, hệ thống trích nguyên văn đoạn nguồn, có nhãn **Trích xuất nguồn**.
5. Vào **Kho tài liệu** để nhập PDF. Vào **Chỉ tiêu xác nhận** để đối chiếu và nhập số liệu. Chỉ tiêu xác nhận mới được dùng để tính toán; có thể thu hồi để sửa.

## Bật RAG với mô hình cục bộ (tùy chọn)

### Cấu hình Ollama đã chọn cho máy này

GPU RTX 4060 Laptop 8 GB, RAM khoảng 16 GB: dùng `qwen3:4b` để trả lời và `bge-m3:latest` cho embedding đa ngôn ngữ. Cài bản portable chính thức trong thư mục dữ liệu dự án, không thêm PATH hoặc tự khởi động cùng Windows:

```powershell
python scripts/setup_ollama.py
python -m finlens local
```

Hoặc sau khi setup, mở `run-local.cmd`. Lệnh `local` khởi động/reuse Ollama trên localhost, chỉ tải mô hình còn thiếu, lập embedding còn thiếu và mở FinLens. Mô hình đã có thì có thể khởi động lại không cần kết nối registry. Dữ liệu runtime/model ở `data/ollama/`, không được commit. Riêng lượt cài đầu cần tải khoảng 1,6 GB runtime và 3,7 GB mô hình.

Ollama do launcher khởi động có `OLLAMA_NO_CLOUD=1`; local profile từ chối địa chỉ máy chủ bên ngoài và tên mô hình `:cloud`. Thư mục model ở ổ D khi chạy từ repo này. Ollama có thể lưu khóa nhận diện cục bộ trong thư mục hồ sơ mặc định của tài khoản; launcher không thay HOME hoặc cài dịch vụ hệ thống.

`python -m finlens prepare-local` chỉ chuẩn bị server/mô hình, không mở ứng dụng. Trên Linux/macOS, cài và khởi động Ollama chính thức trước; `local` có thể reuse dịch vụ đó.

### Tùy chỉnh mô hình

Mặc định: **BM25 + trích xuất nguồn + tính toán Decimal**. Không tự tải mô hình, gọi dịch vụ AI hay gửi tài liệu ra ngoài. BM25 là baseline từ khóa, không phải dense retrieval.

Để dùng mô hình, cài Ollama từ nguồn chính thức và tải mô hình chat/embedding phù hợp với máy. Cấu hình tên các mô hình bạn đã cài:

```powershell
$env:OLLAMA_BASE_URL = "http://127.0.0.1:11434"
$env:FINLENS_CHAT_MODEL = "TEN_MO_HINH_CHAT_DA_CAI"
$env:FINLENS_EMBED_MODEL = "TEN_MO_HINH_EMBEDDING_DA_CAI"
python -m finlens index
python -m finlens serve
```

Nhập thêm tài liệu thì chạy `index` lại. Nếu thay trọng số nhưng giữ tên mô hình, chạy `python -m finlens index --rebuild`. Dừng và chạy lại server sau khi đổi cấu hình. Có thể chỉ cấu hình một mô hình để thử riêng generation hoặc dense retrieval. `.env.example` là hướng dẫn; ứng dụng không tự đọc `.env`.

Khi đủ embedding, truy xuất dùng **BM25 + cosine similarity + RRF**. Khi mô hình lỗi hoặc citation sai, hệ thống quay về tìm kiếm từ khóa/trích xuất và trả thông báo. Ngưỡng cosine 0.25 cần hiệu chỉnh trên bộ development thật. Chat được gọi với `think=false`, context 8192 token, giới hạn 700 token đầu ra để dành tài nguyên cho câu trả lời có nguồn.

Tham khảo API: [Ollama embed](https://docs.ollama.com/api/embed), [Ollama chat](https://docs.ollama.com/api/chat).

## Kiểm tra và đánh giá

```powershell
python -m unittest discover -s tests -v
node --check finlens/static/app.js
python -m finlens evaluate
```

Test dùng `unittest` có sẵn trong Python. Node chỉ cần để kiểm tra cú pháp JavaScript. Evaluate tạo `artifacts/evaluation.json` và `artifacts/evaluation.md`, trả mã lỗi nếu có câu không đạt, dùng DB tạm riêng để giữ nguyên dữ liệu của bạn.

Bộ smoke gồm 24 câu giả lập, 7 câu có trang nguồn chuẩn. Đây là kiểm tra cơ chế, **không phải benchmark hiệu quả trên tài liệu tài chính thật**. Quy trình xây bộ đánh giá độc lập 150–200 câu nằm trong kế hoạch. CI được viết cho Windows/Linux, Python 3.11/3.13; chưa chạy trên GitHub vì repository chưa có remote.

## Phần đồ án tiếp theo

- Thu thập, kiểm tra quyền sử dụng và gán nhãn 15 báo cáo thật của 5 doanh nghiệp.
- OCR tiếng Việt, bảng nhiều trang, biểu đồ và tự động đề xuất chỉ tiêu cần duyệt.
- Thực nghiệm embedding/chat thật, reranker và ablation trên bộ test độc lập.
- Xác thực, phân quyền, hàng đợi và lưu trữ cho nhiều người dùng.

Ứng dụng hiện dành cho một người dùng cục bộ. Kiểm tra citation tồn tại không bảo đảm diễn giải của LLM đúng; cần đối chiếu nguồn. Demo là văn bản giả lập nên không có PDF gốc; PDF bạn nhập có liên kết mở tệp gốc.

PyMuPDF dùng AGPL hoặc giấy phép thương mại; cần chọn phương án phù hợp trước khi phân phối sản phẩm. [Thông tin giấy phép](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
