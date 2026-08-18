#!/usr/bin/env python3
import csv
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULTS = [
    ROOT / "reports/edited/cybingym - 20-result-cc.csv",
    ROOT / "reports/edited/cybingym - codex-gpt-20.csv",
    ROOT / "reports/edited/cybingym - sageagent-opus-20.csv",
    ROOT / "reports/edited/cybingym - sageagent-gpt-20.csv",
    ROOT / "reports/edited/cybingym - sageagent-kimi-20.csv",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def succeeded(value: str) -> bool:
    return value.strip().lower() == "true"


def main() -> None:
    results = [read_rows(path) for path in RESULTS]
    tasks = Counter(row["difficulty_label"].lower() for row in results[0])

    for difficulty in ("easy", "medium", "hard"):
        cells = [difficulty.title(), str(tasks[difficulty])]
        for rows in results:
            group = [row for row in rows if row["difficulty_label"].lower() == difficulty]
            cells.extend(
                [
                    str(sum(succeeded(row["poc"]) for row in group)),
                    str(sum(succeeded(row["exploit"]) for row in group)),
                ]
            )
        print(" & ".join(cells) + r" \\")


if __name__ == "__main__":
    main()
