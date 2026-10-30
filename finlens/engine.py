"""Evidence answering with explicit modes, abstention and citation validation."""

import re
import time
from decimal import Decimal

from finlens.finance import EvidenceError, compare, debt_capacity, fact_citation, select_fact
from finlens.models import METRICS, UNIT_LABELS, AskRequest
from finlens.providers import ModelConfig, ProviderError, make_provider
from finlens.retrieval import bm25, content_terms, cosine, financial_query, rrf
from finlens.store import Store
from finlens.text import normalize

ALIASES = {
    "revenue": ["doanh thu", "revenue"],
    "net_income": ["loi nhuan sau thue", "net income"],
    "total_assets": ["tong tai san", "total assets"],
    "liabilities": ["no phai tra", "liabilities"],
    "operating_cash_flow": ["dong tien kinh doanh", "dong tien tu hoat dong kinh doanh"],
    "gross_profit": ["loi nhuan gop"], "equity": ["von chu so huu"],
    "cash": ["tien va tuong duong tien"], "receivables": ["phai thu khach hang"],
    "inventory": ["hang ton kho"],
    "current_assets": ["tai san ngan han", "current assets"],
    "current_liabilities": ["no ngan han", "current liabilities", "short-term liabilities"],
    "interest_expense": ["chi phi lai vay", "interest expense"],
}


def valid_citations(text: str, evidence: list[dict]):
    allowed = {c["id"] for c in evidence}
    cited = re.findall(r"\[(S\d+)\]", text)
    if not cited or not set(cited).issubset(allowed):
        return False
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return all(re.search(r"\[S\d+\][.!?。]?\s*$", p) for p in paragraphs)


class Engine:
    def __init__(self, store: Store, config: ModelConfig | None = None, provider=None):
        self.store = store
        self.config = config or ModelConfig.from_env()
        self.provider = provider or make_provider(self.config)

    def index_embeddings(self, rebuild=False):
        model = self.config.embed_model
        if not model:
            raise ProviderError("Hãy cấu hình FINLENS_EMBED_MODEL trước khi lập chỉ mục ngữ nghĩa.")
        if rebuild:
            self.store.clear_embeddings(model)
        chunks = self.store.chunks()
        cached = self.store.embeddings(model)
        missing = [c for c in chunks if c["id"] not in cached]
        for start in range(0, len(missing), 16):
            batch = missing[start:start + 16]
            vectors = self.provider.embed([c["text"] for c in batch])
            self.store.save_embeddings(model, zip([c["id"] for c in batch], vectors))
        return {"model": model, "indexed": len(missing), "total": len(chunks)}

    def retrieve(self, request: AskRequest):
        chunks = self.store.chunks(request.company, request.years, request.scope, request.dataset)
        sparse = bm25(financial_query(request.question), chunks, max(request.top_k, 20))
        if not self.config.embed_model:
            return sparse[:request.top_k], "bm25", []
        cached = self.store.embeddings(self.config.embed_model)
        if not chunks or any(c["id"] not in cached for c in chunks):
            return sparse[:request.top_k], "bm25", ["Chỉ mục embedding chưa đủ. Hãy lập chỉ mục lại để dùng hybrid."]
        try:
            vector = self.provider.embed([request.question])[0]
            if any(len(cached[c["id"]]) != len(vector) for c in chunks):
                raise ProviderError("Chiều embedding đã thay đổi; cần lập lại chỉ mục với tên mô hình mới.")
            dense = [{**c, "score": cosine(vector, cached[c["id"]])} for c in chunks]
            dense = sorted((c for c in dense if c["score"] >= 0.25), key=lambda c: -c["score"])[:20]
            return rrf([sparse, dense], request.top_k), "hybrid_rrf", []
        except ProviderError as exc:
            return sparse[:request.top_k], "bm25", [str(exc) + " Đã dùng tìm kiếm từ khóa."]

    def answer(self, request: AskRequest):
        started = time.perf_counter()
        result = {"status": "insufficient_evidence", "mode": "extractive", "retrieval": "bm25",
                  "answer": "", "citations": [], "comparisons": [], "warnings": [],
                  "ratios": [], "missing_inputs": [],
                  "generation_attempted": False, "generation_error": None,
                  "generation_model": None, "generation_attempts": [], "fallback_used": False,
                  "premise_check": None,
                  "company": request.company, "years": request.years, "dataset": request.dataset}

        def finish():
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            return result

        question_years = sorted({int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", request.question)})
        if question_years and request.years and not set(question_years).issubset(request.years):
            result["answer"] = "Năm trong câu hỏi nằm ngoài bộ lọc. Hãy chọn lại các năm báo cáo."
            return finish()
        years = question_years or request.years
        request = request.model_copy(update={"years": years})
        result["years"] = years
        normalized = normalize(request.question)
        metrics = [metric for metric, aliases in ALIASES.items() if any(a in normalized for a in aliases)]
        wants_reason = any(word in normalized for word in ["vi sao", "tai sao", "nguyen nhan", "giai thich", "nhu the nao", "nho dau", "yeu to", "dong luc"])
        debt_intent = any(word in normalized for word in ["kha nang tra no", "kha nang thanh toan", "thanh toan hien hanh"])
        causal_reason = any(word in normalized for word in ["vi sao", "tai sao", "nguyen nhan", "giai thich", "nho dau", "yeu to", "dong luc"])
        if debt_intent and not causal_reason:
            try:
                analysis = debt_capacity(self.store, request.company, years, request.scope, request.dataset)
            except EvidenceError as exc:
                result["answer"] = str(exc)
                return finish()
            result.update(analysis)
            paragraphs = []
            for ratio in analysis["ratios"]:
                observations = []
                for value in ratio["values"]:
                    refs = " ".join(f"[{source}]" for source in value["sources"])
                    observations.append(f"{value['year']}: {value['value']} {ratio['unit']} {refs}")
                paragraphs.append(f"{ratio['label']}: " + "; ".join(observations) + f". Công thức: {ratio['formula']}.")
            if analysis["missing_inputs"]:
                paragraphs.append("Cần bổ sung hoặc đối chiếu số liệu: " + " ".join(analysis["missing_inputs"]))
            if paragraphs:
                result.update(answer="\n\n".join(paragraphs), mode="calculated",
                              status=("partial" if analysis["missing_inputs"] else "answered") if analysis["ratios"] else "insufficient_evidence")
            else:
                result["answer"] = "Chưa có số liệu đã xác nhận để tính chỉ số thanh toán."
            result["warnings"].append("Các chỉ số phản ánh thanh khoản và cơ cấu nguồn vốn; dòng tiền kinh doanh chia nợ ngắn hạn dùng số dư cuối kỳ. Chưa tính DSCR vì thiếu nghĩa vụ trả gốc và lãi đến hạn theo lịch vay; chưa đủ để kết luận chắc chắn khả năng trả nợ. Phần này tính cục bộ, không cần API AI.")
            return finish()
        wants_change = any(word in normalized for word in ["tang", "giam", "so sanh", "chenh lech", "thay doi"])
        wants_numbers = any(word in normalized for word in ["bao nhieu", "dat", "gia tri", "so lieu", "so sanh", "chenh lech", "%", "ty le"])
        wants_numbers = wants_numbers or bool(metrics and years and not wants_reason)
        if metrics and wants_change and len(years) != 2 and not wants_reason:
            result["answer"] = "Hãy chọn đúng hai năm để so sánh; cần xác định rõ năm gốc."
            return finish()
        numeric_answer = []

        def record_comparisons(comparisons):
            result["comparisons"] = comparisons
            for i, item in enumerate(comparisons):
                citations = item["citations"]
                for j, citation in enumerate(citations):
                    citation["id"] = f"F{i * 2 + j + 1}"
                result["citations"].extend(citations)
                growth = f"; thay đổi {item['growth_percent']}%" if item["growth_percent"] is not None else ""
                numeric_answer.append(f"{item['label']}: {item['from_year']} = {item['from_value']}, {item['to_year']} = {item['to_value']} {item['unit_label']} {item['currency']}; chênh lệch {item['delta']}{growth}. [{citations[0]['id']}] [{citations[1]['id']}]")
                if item["note"]:
                    result["warnings"].append(item["note"])

        # A "why did X rise?" question must not make the model rationalize a
        # change that verified figures contradict. One year implies prior-year
        # comparison, stated explicitly; two years use the selected baseline.
        if len(metrics) == 1 and wants_reason and wants_change and len(years) in {1, 2}:
            pair = years if len(years) == 2 else [years[0] - 1, years[0]]
            try:
                item = compare(self.store, request.company, metrics[0], *pair, request.scope, request.dataset)
                record_comparisons([item])
                if len(years) == 1:
                    result["warnings"].append(f"Đối chiếu với năm liền trước: {pair[0]} → {pair[1]}.")
                # Verified decimals carry the exact sign; the model never decides
                # the direction or computes the percentage.
                delta = Decimal(item["delta"])
                sign = 1 if delta > 0 else -1 if delta < 0 else 0
                has_up = bool(re.search(r"\btang\b", normalized))
                has_down = bool(re.search(r"\bgiam\b", normalized))
                negated = bool(re.search(r"\b(khong|chua)\s+(?:con\s+)?(?:tang|giam)\b", normalized))
                expected = (1 if has_up else -1) if has_up != has_down and not negated else None
                result["premise_check"] = {"from_year": pair[0], "to_year": pair[1],
                                           "direction": "increase" if sign > 0 else "decrease" if sign < 0 else "unchanged",
                                           "contradicted": expected is not None and expected != sign}
                if result["premise_check"]["contradicted"]:
                    direction = "tăng" if sign > 0 else "giảm" if sign < 0 else "không đổi"
                    result.update(status="answered", mode="calculated", answer="\n\n".join(
                        [f"Giả định trong câu hỏi chưa khớp số liệu đã xác nhận: {item['label']} {direction} trong năm {pair[1]} so với {pair[0]}."] + numeric_answer +
                        ["Cần kiểm tra lại chiều biến động trước khi giải thích nguyên nhân."]))
                    return finish()
                if not causal_reason:
                    result.update(status="answered", mode="calculated", answer="\n\n".join(numeric_answer))
                    return finish()
            except EvidenceError as exc:
                result["warnings"].append("Chưa kiểm chứng được chiều biến động: " + str(exc))
        try:
            if not numeric_answer and metrics and wants_change and len(years) == 2 and (wants_numbers or not wants_reason):
                record_comparisons([compare(self.store, request.company, metric, years[0], years[1], request.scope, request.dataset) for metric in metrics])
            elif not numeric_answer and metrics and wants_numbers and not wants_change and len(years) == 1:
                for i, metric in enumerate(metrics):
                    fact = select_fact(self.store, request.company, metric, years[0], request.scope, request.dataset)
                    citation = fact_citation(fact, f"F{i + 1}")
                    result["citations"].append(citation)
                    numeric_answer.append(f"{METRICS[metric]} năm {years[0]}: {fact['value']} {UNIT_LABELS[fact['unit']]} {fact['currency']}. [{citation['id']}]")
        except EvidenceError as exc:
            result.update(answer=str(exc), citations=[], comparisons=[])
            return finish()
        if numeric_answer and not wants_reason:
            result.update(status="answered", mode="calculated", answer="\n\n".join(numeric_answer))
            return finish()
        if metrics and wants_numbers and not wants_reason and not numeric_answer:
            result["answer"] = "Hãy chọn một năm để tra cứu hoặc hai năm để so sánh chỉ tiêu đã xác nhận."
            return finish()

        hits, retrieval_mode, warnings = self.retrieve(request)
        result["retrieval"] = retrieval_mode
        result["warnings"].extend(warnings)
        if not hits:
            result["answer"] = "\n\n".join(numeric_answer + ["Không tìm thấy bằng chứng phù hợp cho câu hỏi trong phạm vi đã chọn."])
            result["status"] = "partial" if numeric_answer else "insufficient_evidence"
            return finish()
        evidence = [{"id": f"S{i}", "document_id": h["document_id"], "page": h["page"],
                     "company": h["company"], "report_year": h["report_year"], "filename": h["filename"],
                     "is_demo": bool(h["is_demo"]), "excerpt": h["text"], "score": h["score"],
                     "period_label": h.get("period_label"),
                     "url": f"/api/documents/{h['document_id']}/pages/{h['page']}"} for i, h in enumerate(hits, 1)]
        if self.config.chat_model:
            result["generation_attempted"] = True
            try:
                if hasattr(self.provider, "generate_with_report"):
                    generation = self.provider.generate_with_report(request.question, evidence)
                    generated = generation.text
                    result["generation_model"] = generation.model
                    result["generation_attempts"] = generation.attempts
                    result["fallback_used"] = generation.model != self.config.chat_model
                    if result["fallback_used"]:
                        result["warnings"].append(f"Đã dùng mô hình dự phòng {generation.model} vì mô hình ưu tiên chưa phục vụ được.")
                else:
                    generated = self.provider.generate(request.question, evidence)
                    result["generation_model"] = self.config.chat_model
                if generated == "KHONG_DU_DU_LIEU":
                    result.update(status="partial" if numeric_answer else "insufficient_evidence",
                                  mode="calculated" if numeric_answer else "extractive",
                                  answer="\n\n".join(numeric_answer + ["Không đủ bằng chứng để trả lời phần giải thích."]))
                    return finish()
                if not valid_citations(generated, evidence):
                    raise ProviderError("Câu trả lời của mô hình không đạt kiểm tra nguồn dẫn.")
                cited_ids = set(re.findall(r"\[(S\d+)\]", generated))
                result["citations"].extend(c for c in evidence if c["id"] in cited_ids)
                result.update(status="answered", mode="generated", answer="\n\n".join(numeric_answer + [generated]))
                result["warnings"].append("Nguồn dẫn đã được kiểm tra tồn tại; cần đối chiếu nội dung suy luận với tài liệu.")
                return finish()
            except ProviderError as exc:
                result["generation_error"] = {"code": exc.code, "retryable": exc.retryable}
                result["generation_attempts"] = exc.attempts
                result["warnings"].append(str(exc) + " Đã chuyển sang trích xuất nguồn.")
        # Extract sentences rather than pretending retrieval alone proves an answer.
        excerpts = []
        query_terms = content_terms(financial_query(request.question))
        for citation in evidence[:3]:
            sentences = re.split(r"(?<=[.!?])\s+|\n", citation["excerpt"])
            candidates = [(len(content_terms(s) & query_terms), s.strip()) for s in sentences if len(s.strip()) >= 25]
            candidates.sort(key=lambda pair: -pair[0])
            if candidates and candidates[0][0] >= min(2, len(query_terms)) and query_terms:
                excerpts.append(f"“{candidates[0][1]}” [{citation['id']}]")
                result["citations"].append(citation)
        if excerpts:
            result["status"] = "partial" if numeric_answer else "evidence_found"
            result["answer"] = "\n\n".join(numeric_answer + ["Các đoạn nguồn liên quan để bạn đối chiếu:"] + excerpts)
        else:
            result["answer"] = "\n\n".join(numeric_answer + ["Không đủ bằng chứng liên quan để trả lời."])
            result["status"] = "partial" if numeric_answer else "insufficient_evidence"
        return finish()
