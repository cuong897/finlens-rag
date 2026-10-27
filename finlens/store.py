"""SQLite persistence. Every query is scoped before retrieval or calculation."""

from contextlib import contextmanager
from datetime import datetime, timezone
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

    def add_document(self, document: dict, pages: list[str]) -> tuple[dict, bool]:
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
        return self.get_document(document["id"]), True

    @staticmethod
    def _document(row):
        result = dict(row)
        result["is_demo"] = bool(result["is_demo"])
        result["has_pdf"] = bool(result.pop("pdf_path", None))
        result["warnings"] = json.loads(result["warnings"])
        return result

    def get_document(self, document_id: str):
        with self.connection() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            return self._document(row) if row else None

    def pdf_path(self, document_id: str):
        with self.connection() as db:
            row = db.execute("SELECT pdf_path FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row or not row[0]:
            return None
        path = (self.root / row[0]).resolve()
        return path if path.is_relative_to(self.root / "uploads") and path.is_file() else None

    def documents(self):
        with self.connection() as db:
            rows = db.execute("""SELECT d.*, (SELECT count(*) FROM pages p
                    WHERE p.document_id=d.id) AS page_count,
                    (SELECT count(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count
                    FROM documents d ORDER BY company, report_year DESC""").fetchall()
            return [self._document(row) for row in rows]

    def page(self, document_id: str, number: int):
        with self.connection() as db:
            row = db.execute("SELECT * FROM pages WHERE document_id=? AND page=?",
                             (document_id, number)).fetchone()
            return dict(row) if row else None

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
            rows = db.execute("""SELECT c.*,d.company,d.report_year,d.scope,d.filename,d.is_demo
                                  FROM chunks c JOIN documents d ON c.document_id=d.id""" + where, args)
            return [dict(row) for row in rows]

    def add_fact(self, fact: dict):
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
            rows = db.execute("""SELECT f.*,d.company,d.scope,d.filename,d.is_demo,d.report_year
                FROM facts f JOIN documents d ON f.document_id=d.id""" + where + " ORDER BY f.year,f.id", args)
            return [dict(row) for row in rows]

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
