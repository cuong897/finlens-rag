import argparse
import json
import os


def main():
    parser = argparse.ArgumentParser(description="FinLens local financial document assistant")
    parser.add_argument("command", choices=["serve", "seed", "evaluate", "index", "local", "prepare-local"], nargs="?", default="serve")
    parser.add_argument("--data-dir", default=os.environ.get("FINLENS_DATA_DIR", "data"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default="artifacts")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild embeddings for the configured model")
    args = parser.parse_args()
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
    elif args.command == "index":
        from finlens.engine import Engine
        result = Engine(store).index_embeddings(rebuild=args.rebuild)
    print(json.dumps(result, ensure_ascii=True, indent=2))
