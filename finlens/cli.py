import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description="FinLens local financial document assistant")
    parser.add_argument("command", choices=["serve", "seed", "evaluate", "index", "local", "prepare-local", "gemini", "public-pilot", "check-pilot", "evaluate-retrieval"], nargs="?", default="serve")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--env-file", default=None, help="Explicit local configuration file; never prints values")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild embeddings for the configured model")
    parser.add_argument("--with-reviewed-facts", action="store_true", help="Include pilot facts already visually checked against source PDFs")
    args = parser.parse_args()
    if args.env_file:
        from finlens.environment import load_env_file
        try:
            load_env_file(args.env_file)
        except ValueError as exc:
            parser.error(str(exc))
    args.data_dir = args.data_dir or os.environ.get("FINLENS_DATA_DIR", "data")
    if args.command == "gemini":
        from dataclasses import replace
        from finlens.providers import ModelConfig
        config = ModelConfig.from_env()
        config = replace(config, chat_provider="gemini", chat_model=config.chat_model or "gemini-3.8-flash",
                         embed_model=config.embed_model or "bge-m3:latest")
        from finlens.local import ensure_server
        from finlens.engine import Engine
        from finlens.store import Store
        from finlens.providers import ProviderError
        try:
            ensure_server(config, args.data_dir)
            print(json.dumps(Engine(Store(args.data_dir), config).index_embeddings(), ensure_ascii=True))
        except ProviderError as exc:
            # Gemini also works with keyword retrieval if local embeddings are unavailable.
            print(str(exc) + " Using BM25 until the embedding service is ready.")
        import uvicorn
        from finlens.api import create_app
        uvicorn.run(create_app(args.data_dir, config), host=args.host, port=args.port)
        return
    if args.command in {"local", "prepare-local"}:
        from finlens.local import prepare_local
        config = prepare_local(args.data_dir)
        if args.command == "prepare-local":
            return
        from finlens.engine import Engine
        from finlens.store import Store
        print(json.dumps(Engine(Store(args.data_dir), config).index_embeddings(), ensure_ascii=True))
        import uvicorn
        from finlens.api import create_app
        uvicorn.run(create_app(args.data_dir, config), host=args.host, port=args.port)
        return
    if args.command == "serve":
        import uvicorn
        from finlens.api import create_app
        uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)
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
        result = evaluate_retrieval(store, args.output_dir)
        print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=True, indent=2))
        return
    print(json.dumps(result, ensure_ascii=True, indent=2))
