import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description="FinLens local financial document assistant")
    parser.add_argument("command", choices=["serve", "seed", "evaluate", "index", "reprocess", "prepare-ocr", "prepare-embeddings", "gemini", "public-pilot", "benchmark-import", "benchmark-holdout-import", "check-pilot", "evaluate-retrieval", "backup", "restore"], nargs="?", default="serve")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--destination", help="New backup or restore directory; existing data is never overwritten")
    parser.add_argument("--backup-dir", help="Verified backup directory to restore")
    parser.add_argument("--env-file", default=None, help="Explicit local configuration file; never prints values")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--warm-retrieval", action=argparse.BooleanOptionalAction, default=True,
                        help="Prepare vectors and local embeddings before serving (no chat API call)")
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild embeddings for the configured model")
    parser.add_argument("--with-reviewed-facts", action="store_true", help="Include pilot facts already visually checked against source PDFs")
    parser.add_argument("--cases-file", help="Versioned JSON evaluation set with source checksums and evidence quotes; evaluate-retrieval only")
    parser.add_argument("--reports-file", help="JSON list of public source reports with checksums; benchmark-import only")
    args = parser.parse_args()
    if args.destination and args.command not in {'backup','restore'}:parser.error('--destination chỉ dùng với backup/restore.')
    if args.backup_dir and args.command!='restore':parser.error('--backup-dir chỉ dùng với restore.')
    if args.cases_file and args.command != "evaluate-retrieval":
        parser.error("--cases-file chỉ dùng với evaluate-retrieval")
    if args.reports_file and args.command != "benchmark-import":
        parser.error("--reports-file chỉ dùng với benchmark-import")
    if args.env_file:
        from finlens.environment import load_env_file
        try:
            load_env_file(args.env_file)
        except ValueError as exc:
            parser.error(str(exc))
    args.data_dir = args.data_dir or os.environ.get("FINLENS_DATA_DIR", "data")
    if args.command in {"backup","restore"}:
        import sqlite3
        from finlens.backup import BackupError,create_backup,restore_backup
        if not args.destination:parser.error('Cần --destination trỏ tới thư mục mới.')
        if args.command=='restore' and not args.backup_dir:parser.error('Cần --backup-dir để khôi phục.')
        try:
            result=create_backup(args.data_dir,args.destination) if args.command=='backup' else restore_backup(args.backup_dir,args.destination)
        except (BackupError,OSError,sqlite3.Error) as exc:parser.error(str(exc))
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return
    if args.command == "gemini":
        from dataclasses import replace
        from finlens.providers import ModelConfig
        config = ModelConfig.from_env()
        config = replace(config, chat_provider="gemini", chat_model=config.chat_model or "gemini-3.8-flash",
                         embed_model=config.embed_model or "bge-m3:latest")
        from finlens.local import prepare_embeddings
        from finlens.api import create_app
        from finlens.providers import ProviderError
        try:
            config = prepare_embeddings(args.data_dir, config)
        except ProviderError as exc:
            # Gemini also works with keyword retrieval if local embeddings are unavailable.
            print(str(exc) + " Using BM25 until the embedding service is ready.")
        app = create_app(args.data_dir, config, warm_retrieval=args.warm_retrieval)
        try:
            print(json.dumps(app.state.engine.index_embeddings(), ensure_ascii=True))
        except ProviderError as exc:
            print(str(exc) + " Using BM25 until the embedding service is ready.")
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
        return
    if args.command == "prepare-embeddings":
        from finlens.local import prepare_embeddings
        config = prepare_embeddings(args.data_dir)
        print(json.dumps({"embedding_model": config.embed_model, "ready": True}))
        return
    if args.command == "prepare-ocr":
        from finlens.ocr import prepare_ocr
        print(json.dumps(prepare_ocr(args.data_dir), ensure_ascii=True))
        return
    if args.command == "serve":
        import uvicorn
        from finlens.api import create_app
        uvicorn.run(create_app(args.data_dir, warm_retrieval=args.warm_retrieval), host=args.host, port=args.port)
        return
    if args.command == "evaluate":
        from finlens.evaluate import evaluate
        result = evaluate(args.output_dir)
        print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=True, indent=2))
        raise SystemExit(0 if result["passed"] == result["cases"] else 1)
    from finlens.store import Store
    store = Store(args.data_dir)
    if args.command == "seed":
        from finlens.ingest import seed_demo
        result = seed_demo(store)
    elif args.command == "public-pilot":
        from finlens.public_pilot import import_pilot
        result = import_pilot(store, args.with_reviewed_facts)
    elif args.command == "benchmark-import":
        from finlens.benchmark import import_benchmark
        options = {}
        if args.reports_file:
            from pathlib import Path
            try:
                reports = json.loads(Path(args.reports_file).read_text(encoding="utf-8-sig"))
                if not isinstance(reports, list) or not reports or any(not isinstance(r, dict) for r in reports):
                    raise ValueError()
            except (OSError, UnicodeError, ValueError):
                parser.error("Không đọc được danh sách nguồn báo cáo JSON.")
            options["reports"] = reports
        result = import_benchmark(store, **options)
    elif args.command == "benchmark-holdout-import":
        from finlens.benchmark import import_benchmark, holdout_reports
        result = import_benchmark(store, reports=holdout_reports())
    elif args.command == "reprocess":
        from finlens.ingest import reprocess_pdfs
        result = reprocess_pdfs(store)
    elif args.command == "check-pilot":
        from finlens.pilot_check import check_pilot
        result = check_pilot(store, args.output_dir)
        print(json.dumps({k: v for k, v in result.items() if k not in {"results", "comparisons"}}, ensure_ascii=True, indent=2))
        raise SystemExit(0 if result["passed"] == result["cases"] else 1)
    elif args.command == "index":
        from finlens.engine import Engine
        result = Engine(store).index_embeddings(rebuild=args.rebuild)
    elif args.command == "evaluate-retrieval":
        from finlens.retrieval_check import evaluate_retrieval
        options = {}
        if args.cases_file:
            from finlens.retrieval_dataset import load_dataset
            try:
                dataset = load_dataset(args.cases_file)
            except ValueError as exc:
                parser.error(str(exc))
            options = {"cases": dataset["cases"], "dataset_info": {k: v for k, v in dataset.items() if k != "cases"}}
        result = evaluate_retrieval(store, args.output_dir, **options)
        print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=True, indent=2))
        return
    print(json.dumps(result, ensure_ascii=True, indent=2))
