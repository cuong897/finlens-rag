"""Local HTTP API and same-origin web client."""

import os
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from finlens import __version__
from finlens.engine import Engine
from finlens.finance import EvidenceError, compare
from finlens.ingest import MAX_PDF_BYTES, IngestError, ingest_pdf, seed_demo
from finlens.models import METRICS, UNIT_LABELS, AskRequest, CompareRequest, FactRequest, ImportURLRequest, TableOptions
from pydantic import ValidationError
from finlens.providers import ModelConfig, ProviderError, gemini_key_configured
from finlens.store import Store, FactReviewError
from finlens.models import FactBatchRequest
from finlens.suggestions import suggest_page


def create_app(data_dir=None, config=None, *, warm_retrieval=False):
    @asynccontextmanager
    async def lifespan(app):
        if warm_retrieval:
            app.state.retrieval_preparation = await run_in_threadpool(engine.prepare_retrieval)
            logging.getLogger(__name__).info("Retrieval preparation: %s", app.state.retrieval_preparation)
        yield

    app = FastAPI(title="FinLens", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
    store = Store(data_dir or os.environ.get("FINLENS_DATA_DIR", "data"))
    engine = Engine(store, config or ModelConfig.from_env())
    app.state.store = store
    app.state.engine = engine
    app.state.retrieval_preparation = {"status": "pending" if warm_retrieval else "not_requested"}

    @app.middleware("http")
    async def local_safety(request: Request, call_next):
        # Browsers may only mutate this local app from its own origin.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Yêu cầu phải đến từ giao diện FinLens cùng địa chỉ."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
        return response

    @app.get("/api/health")
    def health():
        ready = bool(engine.config.chat_model) and engine.config.chat_provider == "gemini" and gemini_key_configured()
        return {"status": "ok", "version": __version__, "chat_model": engine.config.chat_model or None,
                "embedding_model": engine.config.embed_model or None,
                "chat_provider": engine.config.chat_provider, "chat_ready": ready,
                "fallback_models": list(getattr(engine.provider, "models", ()))[1:],
                "setup_required": ["GEMINI_API_KEY"] if engine.config.chat_provider == "gemini" and not ready else [],
                "answer_mode": "generated" if ready else "extractive",
                "retrieval_preparation": app.state.retrieval_preparation}

    @app.get("/api/catalog")
    def catalog():
        return {"metrics": METRICS, "units": UNIT_LABELS}

    @app.get("/api/documents")
    def documents():
        return store.documents()

    @app.post("/api/demo")
    def demo():
        return seed_demo(store)

    @app.post("/api/documents")
    async def upload(request: Request, company: str = Query(min_length=1, max_length=120),
                     year: int = Query(ge=1990, le=2100),
                     scope: Literal["consolidated", "standalone"] = "consolidated",
                     filename: str = Query(default="report.pdf", max_length=200)):
        if not company.strip():
            raise HTTPException(422, "Tên doanh nghiệp không được để trống.")
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "Hãy gửi tệp dưới dạng application/pdf.")
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > MAX_PDF_BYTES:
                raise HTTPException(413, "PDF vượt giới hạn 32 MB.")
        try:
            return await run_in_threadpool(ingest_pdf, store, bytes(content), filename, company, year, scope)
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/documents/{document_id}/pages/{page}")
    def page(document_id: str, page: int):
        content = store.page(document_id, page)
        if not content:
            raise HTTPException(404, "Không tìm thấy trang nguồn.")
        return {**content, "document": store.get_document(document_id), "in_scope": store.page_allowed(document_id, page)}

    @app.post("/api/import-url")
    def import_url(request: ImportURLRequest):
        from finlens.report_url import download_pdf
        try:
            content, filename, final_url, publisher = download_pdf(request.url)
            result = ingest_pdf(store, content, filename, request.company, request.year, request.scope)
            if not result["document"].get("source_url"):
                store.record_source(result["document"]["id"], final_url, publisher)
            result["document"] = store.get_document(result["document"]["id"])
            return result
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def table_content(request):
        from finlens.tables import MAX_TABLE_BYTES
        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > MAX_TABLE_BYTES:
                raise HTTPException(413, "Tệp vượt giới hạn 20 MB.")
        return bytes(content)

    @app.post("/api/tables/preview")
    async def preview(request: Request, filename: str = Query(max_length=200), sheet: str = Query(default="", max_length=100),
                      header_row: int = Query(default=1, ge=1, le=100),
                      delimiter: Literal["auto", ",", ";", "tab"] = "auto", encoding: Literal["utf-8-sig", "cp1258"] = "utf-8-sig"):
        from finlens.tables import preview_table
        content = await table_content(request)
        try:
            return await run_in_threadpool(preview_table, content, filename, sheet=sheet, header_row=header_row, delimiter=delimiter, encoding=encoding)
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/tables")
    async def import_table(request: Request, filename: str = Query(max_length=200), company: str = Query(min_length=1, max_length=120),
                           year: int = Query(ge=1990, le=2100), scope: Literal["consolidated", "standalone"] = "consolidated",
                           options: str = Query(max_length=6000)):
        from finlens.tables import ingest_table
        if not company.strip():
            raise HTTPException(422, "Tên doanh nghiệp không được để trống.")
        try:
            mapping = TableOptions.model_validate_json(options)
        except ValidationError as exc:
            raise HTTPException(422, "Cần chọn đúng sheet, dòng tiêu đề, cột chỉ tiêu và cột năm.") from exc
        content = await table_content(request)
        try:
            return await run_in_threadpool(ingest_table, store, content, filename, company, year, scope, mapping)
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/documents/{document_id}/file")
    def source_file(document_id: str):
        path = store.source_path(document_id)
        if not path:
            raise HTTPException(404, "Không tìm thấy tệp gốc.")
        return FileResponse(path, media_type="application/octet-stream", filename=store.get_document(document_id)["filename"])

    @app.post("/api/documents/{document_id}/index")
    def index_document(document_id: str):
        if not store.get_document(document_id):
            raise HTTPException(404, "Không tìm thấy tài liệu.")
        try:
            return engine.index_embeddings(document_id=document_id)
        except ProviderError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.get("/api/documents/{document_id}/pdf")
    def pdf(document_id: str):
        path = store.pdf_path(document_id)
        if not path:
            raise HTTPException(404, "Tài liệu này không có tệp PDF gốc.")
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline", filename="report.pdf")

    @app.get("/api/facts")
    def facts(company: str | None = None, scope: Literal["consolidated", "standalone"] | None = None,
              dataset: Literal["demo", "real", "all"] = "all"):
        return store.facts(company=company, scope=scope, dataset=dataset)

    @app.get("/api/documents/{document_id}/suggestions")
    def suggestions(document_id: str, page: int = Query(ge=1, le=5000)):
        document = store.get_document(document_id)
        if not document or not store.page(document_id, page):
            raise HTTPException(404, "Không tìm thấy tài liệu hoặc trang nguồn.")
        path = store.pdf_path(document_id)
        table = store.table_row(document_id, page)
        if document["is_demo"] or not (path or table):
            raise HTTPException(409, "Đề xuất số liệu cần tệp gốc. Hãy chọn tài liệu bạn đã nhập.")
        if not store.page_allowed(document_id, page):
            raise HTTPException(409, "Trang này nằm ngoài phần báo cáo đã chọn để phân tích.")
        try:
            if table:
                from finlens.tables import table_suggestions
                result = table_suggestions(table, document_id, page)
            else:
                result = suggest_page(path, document_id, page, context=document, ocr_dir=store.root / "ocr/tessdata")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        existing = {(f["metric"], f["year"], f["value"], f["unit"], f["currency"])
                    for f in store.facts(document["company"], document["scope"], "real")
                    if f["document_id"] == document_id and f["page"] == page and f["verified"]}
        for candidate in result["candidates"]:
            candidate["already_confirmed"] = (candidate["metric"], candidate["year"], candidate["value"], candidate["unit"], candidate["currency"]) in existing
        return {"document_id": document_id, "page": page, **result}

    @app.post("/api/facts", status_code=201)
    def confirm_fact(request: FactRequest):
        content = store.page(request.document_id, request.page)
        if not content:
            raise HTTPException(404, "Tài liệu hoặc trang nguồn không tồn tại.")
        document = store.get_document(request.document_id)
        if document["is_demo"]:
            raise HTTPException(409, "Dữ liệu giả lập là fixture cố định. Hãy nhập PDF riêng để xác nhận chỉ tiêu.")
        if not store.page_allowed(request.document_id, request.page):
            raise HTTPException(409, "Trang này nằm ngoài phần báo cáo đã chọn để phân tích.")
        if not content["text"].strip():
            raise HTTPException(422, "Trang này không có văn bản để đối chiếu.")
        fact = request.model_dump(exclude={"confirmed"})
        fact["verified"] = True
        return store.add_fact(fact)

    @app.post("/api/facts/batch", status_code=201)
    def confirm_fact_batch(request: FactBatchRequest):
        try:
            return store.confirm_facts([f.model_dump(exclude={"confirmed"}) for f in request.facts])
        except FactReviewError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.delete("/api/facts/{fact_id}")
    def remove_fact(fact_id: int):
        with store.connection() as db:
            row = db.execute("SELECT d.is_demo FROM facts f JOIN documents d ON f.document_id=d.id WHERE f.id=?", (fact_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Không tìm thấy chỉ tiêu.")
            if row[0]:
                raise HTTPException(409, "Không thay đổi fixture giả lập.")
            db.execute("DELETE FROM facts WHERE id=?", (fact_id,))
        return {"deleted": fact_id}

    @app.post("/api/compare")
    def comparison(request: CompareRequest):
        try:
            return compare(store, **request.model_dump())
        except EvidenceError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/ask")
    def ask(request: AskRequest):
        return engine.answer(request)

    @app.post("/api/index")
    def index():
        try:
            return engine.index_embeddings()
        except ProviderError as exc:
            raise HTTPException(503, str(exc)) from exc

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
