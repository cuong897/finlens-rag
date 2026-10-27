"""Run real local model checks against the running HTTP app, using demo data only."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time

import httpx

CASES = [
    {"id": "reason-cost", "question": "Vì sao lợi nhuận sau thuế của doanh nghiệp giảm trong năm 2024?",
     "company": "An Phát Demo", "years": [2024], "mode": "generated", "status": "answered", "source_page": 2},
    {"id": "reason-logistics", "question": "Những yếu tố nào giúp biên lợi nhuận cải thiện trong năm 2024?",
     "company": "Bình Minh Demo", "years": [2024], "mode": "generated", "status": "answered", "source_page": 2},
    {"id": "reason-cashflow", "question": "Dòng tiền kinh doanh trong năm 2025 cải thiện nhờ đâu?",
     "company": "An Phát Demo", "years": [2025], "mode": "generated", "status": "answered", "source_page": 2},
    {"id": "numeric", "question": "So sánh doanh thu và lợi nhuận sau thuế năm 2023 và 2024",
     "company": "An Phát Demo", "years": [2023, 2024], "mode": "calculated", "status": "answered", "growth": ["25.00", "-10.00"]},
    {"id": "combined", "question": "Doanh thu và lợi nhuận sau thuế năm 2023 và 2024 thay đổi bao nhiêu, vì sao lợi nhuận giảm?",
     "company": "An Phát Demo", "years": [2023, 2024], "mode": "generated", "status": "answered", "growth": ["25.00", "-10.00"]},
    {"id": "unknown-salary", "question": "CEO nhận lương bao nhiêu năm 2024?",
     "company": "An Phát Demo", "years": [2024], "status": "insufficient_evidence"},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", default="artifacts/local-models")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(timeout=150, trust_env=False) as client:
        health = client.get(args.url + "/api/health")
        health.raise_for_status()
        health = health.json()
        if not health.get("chat_model") or not health.get("embedding_model"):
            raise SystemExit("Start python -m finlens local before running live checks.")
        for case in CASES:
            payload = {key: case[key] for key in ("question", "company", "years")}
            payload.update(scope="consolidated", dataset="demo", top_k=5)
            started = time.perf_counter()
            response = client.post(args.url + "/api/ask", json=payload)
            response.raise_for_status()
            answer = response.json()
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            checks = {"status": answer["status"] == case["status"],
                      "company_isolation": all(c["company"] == case["company"] for c in answer["citations"]),
                      "year_isolation": all(c["report_year"] in case["years"] for c in answer["citations"])}
            if "mode" in case:
                checks["mode"] = answer["mode"] == case["mode"]
            if case.get("mode") == "generated":
                checks["hybrid_retrieval"] = answer["retrieval"] == "hybrid_rrf"
                checks["generated_citation"] = any(c["id"].startswith("S") for c in answer["citations"])
            if "source_page" in case:
                checks["source_page"] = any(c["page"] == case["source_page"] for c in answer["citations"])
            if "growth" in case:
                checks["exact_growth"] = [c["growth_percent"] for c in answer["comparisons"]] == case["growth"]
            checks["sources_exist"] = all(client.get(args.url + c["url"]).status_code == 200 for c in answer["citations"])
            item = {"id": case["id"], "question": case["question"], "passed": all(checks.values()),
                    "checks": checks, "http_latency_ms": elapsed, "response": answer}
            results.append(item)
            print(f"{case['id']}: {'PASS' if item['passed'] else 'FAIL'} | {answer['mode']} | {answer['status']} | {elapsed} ms", flush=True)
            (output / "results-in-progress.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        version = client.get("http://127.0.0.1:11434/api/version").json()
        tags = client.get("http://127.0.0.1:11434/api/tags").json()
        running = client.get("http://127.0.0.1:11434/api/ps").json()
    report = {"kind": "real_local_model_integration_on_synthetic_corpus",
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "warning": "Integration checks on synthetic data; not a financial-domain quality benchmark.",
              "health": health, "ollama": version, "models": tags, "running_models": running,
              "cases": len(results), "passed": sum(r["passed"] for r in results),
              "http_latency_p50_ms": statistics.median(r["http_latency_ms"] for r in results),
              "results": results}
    (output / "live-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {output / 'live-check.json'}", flush=True)
    raise SystemExit(0 if report["passed"] == report["cases"] else 1)


if __name__ == "__main__":
    main()
