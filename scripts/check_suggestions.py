"""Check local PDF drafts against reviewed pilot anchors, without saving facts.

This is a development regression check on known reports, not a held-out
benchmark. Extra draft values are intentionally not counted as reviewed.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finlens.public_pilot import manifest
from finlens.suggestions import suggest_page


def check(data_dir):
    results, pages = [], []
    for report in manifest():
        path = Path(data_dir) / "public-pilot" / report["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != report["sha256"]:
            raise ValueError("Missing or changed pilot PDF. Run public-pilot and check the pinned source first.")
        for page in sorted({fact["page"] for fact in report["facts"]}):
            draft = suggest_page(path, "pilot-check", page)
            expected = [fact for fact in report["facts"] if fact["page"] == page]
            pages.append({"report_year": report["year"], "page": page,
                          "draft_count": len(draft["candidates"]), "warnings": draft["warnings"],
                          "drafts": draft["candidates"]})
            for fact in expected:
                matched = any(c["metric"] == fact["metric"] and c["year"] == fact["year"]
                              and c["value"] == fact["value"] and c["currency"] == "VND"
                              and c["unit"] == "one" and c["verified"] is False for c in draft["candidates"])
                results.append({"report_year": report["year"], "page": page, "metric": fact["metric"],
                                "fact_year": fact["year"], "passed": matched})
    return {"kind": "known_pilot_draft_regression", "generated_at": datetime.now(timezone.utc).isoformat(),
            "warning": "Known development anchors only; extra drafts are not independently reviewed or scored.",
            "cloud_calls": 0, "facts_saved": 0, "cases": len(results),
            "passed": sum(r["passed"] for r in results), "results": results, "pages": pages}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="artifacts/suggestions")
    args = parser.parse_args()
    report = check(args.data_dir)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"results", "pages"}}))
    raise SystemExit(0 if report["passed"] == report["cases"] else 1)


if __name__ == "__main__":
    main()
