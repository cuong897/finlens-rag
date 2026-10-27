"""Verify a running FinLens/Gemini integration using synthetic documents only.

Stops if all generation options fail. Extractive fallback never counts as AI
success; validated model failover records the actual model and attempt history.
No API keys are read by this tool.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import httpx

CASES = [
    {"id": "explain-logistics", "company": "Bình Minh Demo", "years": [2024],
     "question": "Những yếu tố nào giúp biên lợi nhuận cải thiện trong năm 2024?",
     "mode": "generated", "status": "answered", "source_page": 2},
    {"id": "numeric", "company": "An Phát Demo", "years": [2023, 2024],
     "question": "So sánh doanh thu và lợi nhuận sau thuế năm 2023 và 2024",
     "mode": "calculated", "status": "answered", "growth": ["25.00", "-10.00"]},
    {"id": "explain-cashflow", "company": "An Phát Demo", "years": [2025],
     "question": "Dòng tiền kinh doanh trong năm 2025 cải thiện nhờ đâu?",
     "mode": "generated", "status": "answered", "source_page": 2},
    {"id": "unknown-salary", "company": "An Phát Demo", "years": [2024],
     "question": "CEO nhận lương bao nhiêu năm 2024?", "status": "insufficient_evidence"},
]


def run_checks(client, base_url, cases=CASES, interval=15, pause=time.sleep):
    response = client.get(base_url + "/api/health")
    response.raise_for_status()
    health = response.json()
    if health.get("chat_provider") != "gemini" or not health.get("chat_ready"):
        raise ValueError("Start FinLens with the configured Gemini profile first.")
    results = []
    for case in cases:
        if results and interval:
            pause(interval)
        payload = {key: case[key] for key in ("company", "question", "years")}
        payload.update(scope="consolidated", dataset="demo", top_k=5)
        started = time.perf_counter()
        response = client.post(base_url + "/api/ask", json=payload)
        response.raise_for_status()
        answer = response.json()
        checks = {"status": answer["status"] == case["status"],
                  "no_provider_error": answer.get("generation_error") is None,
                  "company_isolation": all(c["company"] == case["company"] for c in answer["citations"]),
                  "year_isolation": all(c["report_year"] in case["years"] for c in answer["citations"])}
        if "mode" in case:
            checks["mode"] = answer["mode"] == case["mode"]
        if case.get("mode") == "generated":
            checks["real_generation_attempted"] = answer.get("generation_attempted") is True
            checks["actual_model_recorded"] = bool(answer.get("generation_model"))
            checks["successful_attempt_recorded"] = any(
                a.get("status") == "succeeded" and a.get("model") == answer.get("generation_model")
                for a in answer.get("generation_attempts", []))
            checks["hybrid_retrieval"] = answer["retrieval"] == "hybrid_rrf"
            checks["has_generated_source"] = any(c["id"].startswith("S") for c in answer["citations"])
        if "source_page" in case:
            checks["expected_page"] = any(c["page"] == case["source_page"] for c in answer["citations"])
        if "growth" in case:
            checks["exact_arithmetic"] = [c["growth_percent"] for c in answer["comparisons"]] == case["growth"]
        checks["sources_exist"] = all(client.get(base_url + c["url"]).status_code == 200 for c in answer["citations"])
        result = {"id": case["id"], "question": case["question"], "passed": all(checks.values()),
                  "checks": checks, "http_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                  "response": answer}
        results.append(result)
        print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'} | {answer['mode']} | {answer.get('generation_error')}", flush=True)
        if answer.get("generation_error"):
            break
    return {"kind": "live_gemini_integration_on_synthetic_corpus",
            "generated_at": datetime.now(timezone.utc).isoformat(), "health": health,
            "warning": "Synthetic integration checks; not a real-document quality benchmark.",
            "planned": len(cases), "attempted": len(results), "passed": sum(r["passed"] for r in results),
            "skipped": len(cases) - len(results), "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", default="artifacts/gemini")
    parser.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("Interval must be nonnegative.")
    with httpx.Client(timeout=150, trust_env=False) as client:
        report = run_checks(client, args.url.rstrip("/"), interval=args.interval)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "live-check.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {target}")
    raise SystemExit(0 if report["passed"] == report["planned"] else 1)


if __name__ == "__main__":
    main()
