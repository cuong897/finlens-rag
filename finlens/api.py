"""Local HTTP API and same-origin web client."""

import os
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
from finlens.models import METRICS, UNIT_LABELS, AskRequest, CompareRequest, FactRequest
from finlens.providers import ModelConfig, ProviderError
from finlens.store import Store


def create_app(data_dir=None, config=None):
    app = FastAPI(title="FinLens", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
    store = Store(data_dir or os.environ.get("FINLENS_DATA_DIR", "data"))
    engine = Engine(store, config or ModelConfig.from_env())
    app.state.store = store
    app.state.engine = engine

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
        return {"status": "ok", "version": __version__, "chat_model": engine.config.chat_model or None,
                "embedding_model": engine.config.embed_model or None,
                "answer_mode": "generated" if engine.config.chat_model else "extractive"}

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
                raise HTTPException(413, "PDF vượt giới hạn 20 MB.")
        try:
            return await run_in_threadpool(ingest_pdf, store, bytes(content), filename, company, year, scope)
        except IngestError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/documents/{document_id}/pages/{page}")
    def page(document_id: str, page: int):
        content = store.page(document_id, page)
        if not content:
            raise HTTPException(404, "Không tìm thấy trang nguồn.")
        return {**content, "document": store.get_document(document_id)}

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

    @app.post("/api/facts", status_code=201)
    def confirm_fact(request: FactRequest):
        content = store.page(request.document_id, request.page)
        if not content:
            raise HTTPException(404, "Tài liệu hoặc trang nguồn không tồn tại.")
        document = store.get_document(request.document_id)
        if document["is_demo"]:
            raise HTTPException(409, "Dữ liệu giả lập là fixture cố định. Hãy nhập PDF riêng để xác nhận chỉ tiêu.")
        if not content["text"].strip():
            raise HTTPException(422, "Trang này không có văn bản để đối chiếu.")
        fact = request.model_dump(exclude={"confirmed"})
        fact["verified"] = True
        return store.add_fact(fact)

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
