"""SQLite persistence. Every query is scoped before retrieval or calculation."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from finlens.text import chunk_page


class Store:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "uploads").mkdir(exist_ok=True)
        self.db_path = self.root / "finlens.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL,
                    report_year INTEGER NOT NULL, scope TEXT NOT NULL,
                    filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                    is_demo INTEGER NOT NULL DEFAULT 0, pdf_path TEXT,
                    warnings TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
                    UNIQUE(company, report_year, scope, sha256)
                );
                CREATE TABLE IF NOT EXISTS pages (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    page INTEGER NOT NULL, text TEXT NOT NULL,
                    PRIMARY KEY(document_id, page)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL,
                    page INTEGER NOT NULL, text TEXT NOT NULL,
                    FOREIGN KEY(document_id, page) REFERENCES pages(document_id, page)
                );
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY, document_id TEXT NOT NULL,
                    page INTEGER NOT NULL, metric TEXT NOT NULL, year INTEGER NOT NULL,
                    value TEXT NOT NULL, unit TEXT NOT NULL, currency TEXT NOT NULL,
                    verified INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(document_id, page) REFERENCES pages(document_id, page),
                    UNIQUE(document_id, page, metric, year, value, unit, currency)
                );
                CREATE INDEX IF NOT EXISTS idx_docs_filters
                    ON documents(company, report_year, scope, is_demo);
                CREATE INDEX IF NOT EXISTS idx_facts_metric ON facts(metric, year, verified);
                CREATE TABLE IF NOT EXISTS embeddings (
                    chunk_id INTEGER NOT NULL REFERENCES chunks(id),
                    model TEXT NOT NULL, vector TEXT NOT NULL,
                    PRIMARY KEY(chunk_id, model)
                );
                CREATE TABLE IF NOT EXISTS document_sources (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    url TEXT NOT NULL, publisher TEXT NOT NULL, retrieved_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_context (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    allowed_pages TEXT, period_start TEXT, period_end TEXT,
                    period_label TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS table_sources (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    file_sha256 TEXT NOT NULL, options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_processing (
                    document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS table_rows (
                    document_id TEXT NOT NULL, page INTEGER NOT NULL,
                    sheet TEXT NOT NULL, row_number INTEGER NOT NULL, cell_range TEXT NOT NULL,
                    cells TEXT NOT NULL, PRIMARY KEY(document_id,page),
                    FOREIGN KEY(document_id,page) REFERENCES pages(document_id,page) ON DELETE CASCADE
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_document(self, document: dict, pages: list[str], *, context=None, table_source=None, table_rows=None, processing_version=None) -> tuple[dict, bool]:
        context_values = self._context_values(len(pages), **context) if context else None
        with self.connection() as db:
            existing = db.execute(
                "SELECT * FROM documents WHERE company=? AND report_year=? AND scope=? AND sha256=?",
                (document["company"], document["report_year"], document["scope"], document["sha256"]),
            ).fetchone()
            if existing:
                return self._document(existing), False
            db.execute(
                """INSERT INTO documents
                (id,company,report_year,scope,filename,sha256,is_demo,pdf_path,warnings,created_at)
                VALUES (:id,:company,:report_year,:scope,:filename,:sha256,:is_demo,:pdf_path,:warnings,:created_at)""",
                {**document, "warnings": json.dumps(document.get("warnings", []), ensure_ascii=False),
                 "created_at": datetime.now(timezone.utc).isoformat()},
            )
            for number, content in enumerate(pages, 1):
                db.execute("INSERT INTO pages VALUES (?,?,?)", (document["id"], number, content))
                for chunk in chunk_page(content):
                    db.execute("INSERT INTO chunks(document_id,page,text) VALUES (?,?,?)",
                               (document["id"], number, chunk))
            if context_values is not None:
                db.execute("INSERT INTO document_context VALUES (?,?,?,?,?)", (document["id"], *context_values))
            if processing_version:
                db.execute("INSERT INTO document_processing VALUES (?,?)", (document["id"], processing_version))
            if table_source:
                db.execute("INSERT INTO table_sources VALUES (?,?,?)", (document["id"], table_source["file_sha256"], json.dumps(table_source["options"], ensure_ascii=False)))
                for number, row in enumerate(table_rows, 1):
                    db.execute("INSERT INTO table_rows VALUES (?,?,?,?,?,?)", (document["id"], number, row["sheet"], row["row_number"], row["cell_range"], json.dumps(row["cells"], ensure_ascii=False)))
        return self.get_document(document["id"]), True

    def find_document(self, company: str, year: int, scope: str, sha256: str):
        with self.connection() as db:
            row = db.execute("SELECT id FROM documents WHERE company=? AND report_year=? AND scope=? AND sha256=?",
                             (company, year, scope, sha256)).fetchone()
        return self.get_document(row[0]) if row else None

    def processing_version(self, document_id):
        with self.connection() as db:
            row = db.execute("SELECT version FROM document_processing WHERE document_id=?", (document_id,)).fetchone()
        return row[0] if row else None

    def replace_derived_text(self, document_id, pages, warnings, version):
        with self.connection() as db:
            count = db.execute("SELECT COUNT(*) FROM pages WHERE document_id=?", (document_id,)).fetchone()[0]
            if not count or len(pages) != count or not any(text.strip() for text in pages):
                raise ValueError("Cập nhật chỉ mục phải giữ nguyên số trang và tài liệu nguồn.")
            db.execute("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)", (document_id,))
            db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            for page, content in enumerate(pages, 1):
                db.execute("UPDATE pages SET text=? WHERE document_id=? AND page=?", (content, document_id, page))
                for chunk in chunk_page(content):
                    db.execute("INSERT INTO chunks(document_id,page,text) VALUES (?,?,?)", (document_id, page, chunk))
            db.execute("UPDATE documents SET warnings=? WHERE id=?", (json.dumps(warnings, ensure_ascii=False), document_id))
            db.execute("INSERT OR REPLACE INTO document_processing VALUES (?,?)", (document_id, version))

    @staticmethod
    def _document(row):
        result = dict(row)
        result["is_demo"] = bool(result["is_demo"])
        path = result.pop("pdf_path", None)
        result["file_type"] = Path(path).suffix.lstrip(".").lower() if path else "text"
        result["has_file"] = bool(path)
        result["has_pdf"] = bool(path) and result["file_type"] == "pdf"
        result["warnings"] = json.loads(result["warnings"])
        result["allowed_pages"] = json.loads(result["allowed_pages"]) if result.get("allowed_pages") else None
        return result

    def get_document(self, document_id: str):
        with self.connection() as db:
            row = db.execute("""SELECT d.*,s.url AS source_url,s.publisher AS source_publisher,
                s.retrieved_at AS source_retrieved_at,x.allowed_pages,x.period_start,x.period_end,x.period_label FROM documents d
                LEFT JOIN document_sources s ON s.document_id=d.id
                LEFT JOIN document_context x ON x.document_id=d.id WHERE d.id=?""", (document_id,)).fetchone()
            return self._document(row) if row else None

    def record_source(self, document_id: str, url: str, publisher: str):
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO document_sources VALUES (?,?,?,?)",
                       (document_id, url, publisher, datetime.now(timezone.utc).isoformat()))

    @staticmethod
    def _context_values(page_count, *, allowed_pages=None, period_start=None, period_end=None, period_label=""):
        if allowed_pages is not None:
            if not isinstance(allowed_pages, list) or not allowed_pages or any(type(p) is not int or not 1 <= p <= page_count for p in allowed_pages):
                raise ValueError("Danh sách trang trong phạm vi không hợp lệ.")
            allowed_pages = sorted(set(allowed_pages))
        if bool(period_start) != bool(period_end):
            raise ValueError("Cần cả ngày đầu và ngày cuối kỳ báo cáo.")
        if period_start and date.fromisoformat(period_start) >= date.fromisoformat(period_end):
            raise ValueError("Kỳ báo cáo không hợp lệ.")
        return (json.dumps(allowed_pages) if allowed_pages is not None else None, period_start, period_end, period_label)

    def record_context(self, document_id, **context):
        if not self.get_document(document_id):
            raise ValueError("Tài liệu không tồn tại.")
        with self.connection() as db:
            count = db.execute("SELECT COUNT(*) FROM pages WHERE document_id=?", (document_id,)).fetchone()[0]
        values = self._context_values(count, **context)
        with self.connection() as db:
            db.execute("""INSERT INTO document_context VALUES (?,?,?,?,?)
                ON CONFLICT(document_id) DO UPDATE SET allowed_pages=excluded.allowed_pages,
                period_start=excluded.period_start,period_end=excluded.period_end,period_label=excluded.period_label""",
                       (document_id, *values))

    def page_allowed(self, document_id, page):
        document = self.get_document(document_id)
        return bool(document and (document["allowed_pages"] is None or page in document["allowed_pages"]))

    @staticmethod
    def _eligible(rows):
        result, policies = [], {}
        for row in rows:
            item = dict(row)
            sheet, cells = item.pop("_sheet", None), item.pop("_cell_range", None)
            if sheet:
                item["source_label"] = f"{sheet} · {cells}"
            raw = item.pop("_allowed_pages", None)
            if raw is not None:
                if raw not in policies:
                    policies[raw] = set(json.loads(raw))
                if item["page"] not in policies[raw]:
                    continue
            result.append(item)
        return result

    def source_path(self, document_id: str):
        with self.connection() as db:
            row = db.execute("SELECT pdf_path FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row or not row[0]:
            return None
        path = (self.root / row[0]).resolve()
        return path if path.is_relative_to(self.root / "uploads") and path.is_file() else None

    def pdf_path(self, document_id: str):
        path = self.source_path(document_id)
        return path if path and path.suffix.lower() == ".pdf" else None

    def table_row(self, document_id, page):
        with self.connection() as db:
            row = db.execute("SELECT r.*,s.options FROM table_rows r JOIN table_sources s ON s.document_id=r.document_id WHERE r.document_id=? AND r.page=?", (document_id, page)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["cells"] = json.loads(result["cells"])
        result["options"] = json.loads(result["options"])
        result["source_label"] = f"{result['sheet']} · {result['cell_range']}"
        return result

    def documents(self):
        with self.connection() as db:
            rows = db.execute("""SELECT d.*,s.url AS source_url,s.publisher AS source_publisher,
                    s.retrieved_at AS source_retrieved_at, (SELECT count(*) FROM pages p
                    WHERE p.document_id=d.id) AS page_count,
                    (SELECT count(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count
                    ,x.allowed_pages,x.period_start,x.period_end,x.period_label
                    FROM documents d LEFT JOIN document_sources s ON s.document_id=d.id
                    LEFT JOIN document_context x ON x.document_id=d.id
                    ORDER BY company, report_year DESC""").fetchall()
            return [self._document(row) for row in rows]

    def page(self, document_id: str, number: int):
        with self.connection() as db:
            row = db.execute("SELECT * FROM pages WHERE document_id=? AND page=?",
                             (document_id, number)).fetchone()
            result = dict(row) if row else None
        if result:
            result["table"] = self.table_row(document_id, number)
        return result

    @staticmethod
    def filters(company=None, years=None, scope=None, dataset="all"):
        clauses, args = [], []
        if company:
            clauses.append("d.company=?")
            args.append(company)
        if years:
            clauses.append("d.report_year IN (" + ",".join("?" for _ in years) + ")")
            args.extend(years)
        if scope:
            clauses.append("d.scope=?")
            args.append(scope)
        if dataset != "all":
            clauses.append("d.is_demo=?")
            args.append(1 if dataset == "demo" else 0)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), args

    def chunks(self, company=None, years=None, scope=None, dataset="all"):
        where, args = self.filters(company, years, scope, dataset)
        with self.connection() as db:
            rows = db.execute("""SELECT c.*,d.company,d.report_year,d.scope,d.filename,d.is_demo,
                                  x.allowed_pages AS _allowed_pages,x.period_label,r.sheet AS _sheet,r.cell_range AS _cell_range
                                  FROM chunks c JOIN documents d ON c.document_id=d.id
                                  LEFT JOIN document_context x ON x.document_id=d.id
                                  LEFT JOIN table_rows r ON r.document_id=c.document_id AND r.page=c.page""" + where, args)
            return self._eligible(rows)

    def add_fact(self, fact: dict):
        if not self.page_allowed(fact["document_id"], fact["page"]):
            raise ValueError("Trang nguồn nằm ngoài phạm vi báo cáo đã chọn.")
        with self.connection() as db:
            fields = ("document_id", "page", "metric", "year", "value", "unit", "currency", "verified", "note")
            db.execute("""INSERT OR IGNORE INTO facts
                (document_id,page,metric,year,value,unit,currency,verified,note) VALUES (?,?,?,?,?,?,?,?,?)""",
                       tuple(fact.get(f, "") for f in fields))
            row = db.execute("""SELECT * FROM facts WHERE document_id=? AND page=? AND metric=?
                AND year=? AND value=? AND unit=? AND currency=?""", tuple(fact[f] for f in fields[:7])).fetchone()
            return dict(row)

    def facts(self, company=None, scope=None, dataset="all", metric=None, years=None):
        # Fact years are independent of the report publication year.
        where, args = self.filters(company=company, scope=scope, dataset=dataset)
        clauses = []
        if metric:
            clauses.append("f.metric=?")
            args.append(metric)
        if years:
            clauses.append("f.year IN (" + ",".join("?" for _ in years) + ")")
            args.extend(years)
        if clauses:
            where += (" AND " if where else " WHERE ") + " AND ".join(clauses)
        with self.connection() as db:
            rows = db.execute("""SELECT f.*,d.company,d.scope,d.filename,d.is_demo,d.report_year,
                x.allowed_pages AS _allowed_pages,x.period_label,r.sheet AS _sheet,r.cell_range AS _cell_range
                FROM facts f JOIN documents d ON f.document_id=d.id
                LEFT JOIN document_context x ON x.document_id=d.id
                LEFT JOIN table_rows r ON r.document_id=f.document_id AND r.page=f.page""" + where + " ORDER BY f.year,f.id", args)
            return self._eligible(rows)

    def embeddings(self, model: str):
        with self.connection() as db:
            return {r[0]: json.loads(r[1]) for r in db.execute(
                "SELECT chunk_id,vector FROM embeddings WHERE model=?", (model,))}

    def save_embeddings(self, model: str, pairs):
        with self.connection() as db:
            db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
                           [(cid, model, json.dumps(vector)) for cid, vector in pairs])

    def clear_embeddings(self, model: str):
        with self.connection() as db:
            db.execute("DELETE FROM embeddings WHERE model=?", (model,))
