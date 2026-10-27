# Chuyển phần trả lời sang API miễn phí

Cập nhật 27/10/2026: người dùng làm rõ muốn gọi API có gói miễn phí, thay vì tiếp tục thử mô hình chat cục bộ. Đã dừng tải Qwen3.5. Chưa cấu hình hoặc gọi API đám mây, chưa có key trong dự án.

## Đề xuất

| Dịch vụ | Mô hình đề xuất | Phạm vi miễn phí được công bố |
| --- | --- | --- |
| Google AI Studio / Gemini Developer API | `gemini-3.8-flash` | Input/output miễn phí trong Free tier; hạn mức thực tế theo project và AI Studio |
| GroqCloud | `openai/gpt-oss-120b` | Free plan công bố 30 RPM, 1000 RPD, 8000 TPM, 200000 TPD; tài khoản có thể khác |

Ưu tiên thử Gemini cho câu hỏi tiếng Việt và tài liệu tài chính; đây là lựa chọn để đánh giá, chưa phải kết luận benchmark so với Qwen3. So sánh đúng với bản `qwen3:4b` đã thử, không khái quát cho mọi kích thước Qwen3.

Kiến trúc tiếp theo: BGE-M3 cục bộ + BM25/RRF → đoạn bằng chứng → API trả lời có cấu trúc → kiểm tra nguồn. Phép tính tiếp tục dùng Decimal cục bộ. Chỉ chuyển phần generation; không cần tải thêm mô hình chat lớn.

## Điều kiện vận hành

- Cần API key do người dùng tạo tại nhà cung cấp, lưu qua biến môi trường hoặc tệp riêng được Git bỏ qua; không dán key vào chat hoặc commit.
- Free tier là hạn mức của tài khoản, không chỉ là một tham số API. Dùng project chưa bật tính phí để tránh vượt mục tiêu ngân sách; không tự nâng cấp hoặc fallback sang model trả phí.
- Hết hạn mức thì báo lỗi có kiểm soát/quay về trích nguồn; không tìm cách vượt quota.
- Khi gọi API, đoạn nguồn được gửi cho nhà cung cấp. Google công bố nội dung Free tier có thể dùng cải thiện sản phẩm; bộ báo cáo tài chính công khai/giả lập phù hợp hơn cho bước thử nghiệm này.
- Cần chạy lại cùng bộ câu hỏi để đo độ đúng, citation, độ trễ và giới hạn trước khi chọn mặc định.

## Nguồn đã kiểm tra

- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Groq rate limits](https://console.groq.com/docs/rate-limits)
- [Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- [OpenAI Docs: GPT-OSS 120B](https://developers.openai.com/api/docs/models/gpt-oss-120b)
