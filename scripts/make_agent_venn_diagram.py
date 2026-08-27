#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle


DEFAULT_REPORTS = {
    "Claude Code": "reports/dataset20_exploit_summary_claude_code_anthropic_claude-opus-5.csv",
    "Kimi Code": "reports/dataset20_exploit_summary_kimi_code_moonshot_kimi-k3.csv",
    "Codex": "reports/dataset20_exploit_summary_codex_openai_gpt-5.6.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a clean three-agent Venn diagram from CyBinGym report CSVs."
    )
    parser.add_argument("--claude-csv", default=DEFAULT_REPORTS["Claude Code"])
    parser.add_argument("--kimi-csv", default=DEFAULT_REPORTS["Kimi Code"])
    parser.add_argument("--codex-csv", default=DEFAULT_REPORTS["Codex"])
    parser.add_argument(
        "--metric",
        choices=("poc", "exploit"),
        default="poc",
        help="CSV success column used as the solved definition.",
    )
    parser.add_argument(
        "--kimi-max-llm-calls",
        type=int,
        default=1500,
        help="Kimi Code rows above this call count are treated as unsolved.",
    )
    parser.add_argument(
        "--output",
        default="reports/agent_solved_venn_poc.png",
        help="Output image path. Use .png or .pdf.",
    )
    parser.add_argument(
        "--also-pdf",
        default="reports/agent_solved_venn_poc.pdf",
        help="Optional PDF output path. Set to empty to disable.",
    )
    return parser.parse_args()


def clean_bool(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def solved_ids(path: Path, *, metric: str, kimi_max_llm_calls: int | None = None) -> tuple[set[str], set[str]]:
    solved: set[str] = set()
    all_ids: set[str] = set()
    with path.open(newline="", encoding="utf-8") as input_file:
        for row in csv.DictReader(input_file):
            sample_id = (row.get("id") or "").strip()
            if not sample_id:
                continue
            all_ids.add(sample_id)

            over_limit = False
            if kimi_max_llm_calls is not None:
                llm_calls = (row.get("llm_calls") or "").strip()
                over_limit = not llm_calls or int(llm_calls) > kimi_max_llm_calls

            if not over_limit and clean_bool(row.get(metric)):
                solved.add(sample_id)
    return solved, all_ids


def render_venn(
    *,
    claude: set[str],
    kimi: set[str],
    codex: set[str],
    all_ids: set[str],
    output: Path,
    title: str,
) -> None:
    regions = {
        "claude_only": claude - kimi - codex,
        "kimi_only": kimi - claude - codex,
        "codex_only": codex - claude - kimi,
        "claude_kimi": (claude & kimi) - codex,
        "claude_codex": (claude & codex) - kimi,
        "kimi_codex": (kimi & codex) - claude,
        "all_three": claude & kimi & codex,
        "none": all_ids - (claude | kimi | codex),
    }

    fig, ax = plt.subplots(figsize=(7.0, 5.4))
    ax.set_aspect("equal")
    ax.axis("off")

    circles = [
        ((-1.05, 0.45), "Claude Code", "#d95f02"),
        ((1.05, 0.45), "Kimi Code", "#1b9e77"),
        ((0.0, -1.15), "Codex", "#7570b3"),
    ]
    for center, label, color in circles:
        ax.add_patch(Circle(center, 1.85, facecolor=color, edgecolor=color, alpha=0.22, linewidth=2.2))
        ax.add_patch(Circle(center, 1.85, fill=False, edgecolor=color, linewidth=2.2))
        label_y = 2.45 if label != "Codex" else -3.45
        label_x = center[0] if label != "Codex" else 0
        ax.text(label_x, label_y, label, ha="center", va="center", fontsize=13, fontweight="bold", color=color)

    positions = {
        "claude_only": (-1.8, 0.65),
        "kimi_only": (1.8, 0.65),
        "codex_only": (0.0, -2.55),
        "claude_kimi": (0.0, 1.05),
        "claude_codex": (-0.82, -1.1),
        "kimi_codex": (0.82, -1.1),
        "all_three": (0.0, -0.25),
    }
    for name, position in positions.items():
        ax.text(*position, str(len(regions[name])), ha="center", va="center", fontsize=19, fontweight="bold")

    ax.text(
        2.95,
        -2.95,
        f"None: {len(regions['none'])}",
        ha="center",
        va="center",
        fontsize=12,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "edgecolor": "#999999"},
    )
    ax.set_title(title, fontsize=15, fontweight="bold", pad=12)
    ax.set_xlim(-3.5, 4.0)
    ax.set_ylim(-3.75, 2.95)

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=240, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    claude, claude_ids = solved_ids(Path(args.claude_csv), metric=args.metric)
    kimi, kimi_ids = solved_ids(
        Path(args.kimi_csv),
        metric=args.metric,
        kimi_max_llm_calls=args.kimi_max_llm_calls,
    )
    codex, codex_ids = solved_ids(Path(args.codex_csv), metric=args.metric)
    all_ids = claude_ids | kimi_ids | codex_ids

    title = f"{args.metric.upper()} solved sample overlap"
    render_venn(
        claude=claude,
        kimi=kimi,
        codex=codex,
        all_ids=all_ids,
        output=Path(args.output),
        title=title,
    )
    print(f"wrote: {args.output}")

    if args.also_pdf:
        render_venn(
            claude=claude,
            kimi=kimi,
            codex=codex,
            all_ids=all_ids,
            output=Path(args.also_pdf),
            title=title,
        )
        print(f"wrote: {args.also_pdf}")


if __name__ == "__main__":
    main()
