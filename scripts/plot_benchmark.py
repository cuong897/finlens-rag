"""Export publication-sized retrieval figures from measured JSON (matplotlib optional)."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="artifacts/benchmark-v1/test/retrieval-ablation.json")
    parser.add_argument("--output-dir", default="artifacts/benchmark-v1")
    args = parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    report = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    strategies = ["bm25", "dense", "hybrid_rrf", "hybrid_context"]
    names = ["BM25", "BGE-M3", "Hybrid RRF", "Hybrid context"]
    colors = ["#71849b", "#609ba2", "#b09970", "#2e634d"]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none"})
    figure, axes = plt.subplots(1, 3, figsize=(12.5, 4.6), layout="constrained")
    for axis, key, title in zip(axes, ["recall_at_5", "mrr_at_5", "evidence_recall_at_5"],
                                 ["Recall@5 · trang chuẩn", "MRR@5 · trang chuẩn", "Recall@5 · bằng chứng"]):
        values = [report["summary"][name][key] for name in strategies]
        bars = axis.bar(names, values, color=colors, width=.62)
        axis.bar_label(bars, labels=[f"{value:.4f}" for value in values], padding=4, fontsize=10)
        axis.set_ylim(0, 1)
        axis.set_title(title, fontsize=12, fontweight="bold", pad=15)
        axis.tick_params(axis="x", labelrotation=25)
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#e5e9e5", linewidth=.7)
        for edge in ("top", "right"):
            axis.spines[edge].set_visible(False)
    figure.suptitle(f"FinLens · {report['cases']} câu hỏi trên HPG và PNJ", fontsize=16, fontweight="bold")
    figure.supxlabel("Top 5 đoạn nguồn · Toàn bộ báo cáo · Nhãn do trợ lý biên soạn, chưa có thẩm định độc lập", fontsize=10)
    for extension in ("png", "svg"):
        figure.savefig(output / f"retrieval-benchmark.{extension}", dpi=300, facecolor="white")
    plt.close(figure)
    print("Saved benchmark figures from measured results.")


if __name__ == "__main__":
    main()
