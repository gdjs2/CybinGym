#!/usr/bin/env python3
"""Calculate average token usage and duration from Inspect AI .eval logs."""

from __future__ import annotations

import argparse
import glob
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from inspect_ai.log import read_eval_log_sample_summaries


@dataclass
class SampleStats:
    input_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    total_time: float | None
    working_time: float | None


def field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from either an object or dictionary."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def to_sample_stats(sample: Any) -> SampleStats:
    """Aggregate usage across every model used by one sample."""
    model_usage = field(sample, "model_usage", {}) or {}

    input_tokens = 0
    cache_read_tokens = 0
    cache_write_tokens = 0
    output_tokens = 0
    reasoning_tokens = 0
    total_tokens = 0

    for usage in model_usage.values():
        input_tokens += field(usage, "input_tokens", 0) or 0
        cache_read_tokens += field(
            usage, "input_tokens_cache_read", 0
        ) or 0
        cache_write_tokens += field(
            usage, "input_tokens_cache_write", 0
        ) or 0
        output_tokens += field(usage, "output_tokens", 0) or 0
        reasoning_tokens += field(usage, "reasoning_tokens", 0) or 0
        total_tokens += field(usage, "total_tokens", 0) or 0

    return SampleStats(
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        total_time=field(sample, "total_time"),
        working_time=field(sample, "working_time"),
    )


def expand_paths(patterns: Iterable[str]) -> list[Path]:
    """Expand files, directories, and glob patterns."""
    results: set[Path] = set()

    for pattern in patterns:
        path = Path(pattern).expanduser()

        if path.is_dir():
            results.update(path.rglob("*.eval"))
            continue

        matches = glob.glob(str(path), recursive=True)
        if matches:
            results.update(
                Path(match)
                for match in matches
                if Path(match).is_file()
                and Path(match).suffix == ".eval"
            )
        elif path.is_file():
            results.add(path)

    return sorted(results)


def mean(values: Iterable[float | int]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def format_number(value: float | int) -> str:
    return f"{value:,.2f}"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)

    if hours:
        return f"{hours}h {minutes}m {seconds:.2f}s"
    if minutes:
        return f"{minutes}m {seconds:.2f}s"
    return f"{seconds:.2f}s"


def print_report(
    title: str,
    samples: list[SampleStats],
    file_count: int,
) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print(f"{'=' * 72}")
    print(f"Log files: {file_count}")
    print(f"Samples:   {len(samples)}")

    if not samples:
        print("No matching samples found.")
        return

    sample_count = len(samples)

    total_input = sum(s.input_tokens for s in samples)
    total_cache_read = sum(s.cache_read_tokens for s in samples)
    total_cache_write = sum(s.cache_write_tokens for s in samples)
    total_output = sum(s.output_tokens for s in samples)
    total_reasoning = sum(s.reasoning_tokens for s in samples)
    total_tokens = sum(s.total_tokens for s in samples)

    # Inspect reports uncached input separately from cache reads/writes.
    all_input = total_input + total_cache_read + total_cache_write

    total_times = [
        s.total_time for s in samples if s.total_time is not None
    ]
    working_times = [
        s.working_time for s in samples if s.working_time is not None
    ]

    print("\nToken totals")
    print(f"  Input, uncached:       {total_input:,}")
    print(f"  Input, cache read:     {total_cache_read:,}")
    print(f"  Input, cache write:    {total_cache_write:,}")
    print(f"  Input, including cache:{all_input:>13,}")
    print(f"  Output:                {total_output:,}")
    print(f"  Reasoning:             {total_reasoning:,}")
    print(f"  Total:                 {total_tokens:,}")

    print("\nAverage tokens per sample")
    print(f"  Input, uncached:       {format_number(total_input / sample_count)}")
    print(f"  Input, cache read:     {format_number(total_cache_read / sample_count)}")
    print(f"  Input, cache write:    {format_number(total_cache_write / sample_count)}")
    print(f"  Input, including cache:{format_number(all_input / sample_count):>13}")
    print(f"  Output:                {format_number(total_output / sample_count)}")
    print(f"  Reasoning:             {format_number(total_reasoning / sample_count)}")
    print(f"  Total:                 {format_number(total_tokens / sample_count)}")

    average_total_time = mean(total_times)
    average_working_time = mean(working_times)

    print("\nDuration")
    print(
        "  Average total time:   "
        f"{format_duration(average_total_time)} "
        f"({len(total_times)} timed samples)"
    )
    print(
        "  Average working time: "
        f"{format_duration(average_working_time)} "
        f"({len(working_times)} timed samples)"
    )
    print(f"  Sum of total times:    {format_duration(sum(total_times))}")
    print(f"  Sum of working times:  {format_duration(sum(working_times))}")


def load_samples(
    log_path: Path,
    include_incomplete: bool,
) -> list[SampleStats]:
    summaries = read_eval_log_sample_summaries(log_path)

    selected = []
    for sample in summaries:
        completed = bool(field(sample, "completed", True))

        if not include_incomplete and not completed:
            continue

        selected.append(to_sample_stats(sample))

    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate average token usage and sample duration from "
            "Inspect AI .eval logs."
        )
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help=(
            "One or more .eval files, directories, or glob patterns, "
            'for example "logs/*.eval".'
        ),
    )
    parser.add_argument(
        "--include-incomplete",
        action="store_true",
        help="Include incomplete or interrupted samples.",
    )
    parser.add_argument(
        "--per-file",
        action="store_true",
        help="Print an additional report for each log file.",
    )

    args = parser.parse_args()
    log_paths = expand_paths(args.paths)

    if not log_paths:
        print("Error: no .eval files found.", file=sys.stderr)
        return 1

    combined_samples: list[SampleStats] = []

    for log_path in log_paths:
        try:
            samples = load_samples(
                log_path,
                include_incomplete=args.include_incomplete,
            )
        except Exception as exc:
            print(
                f"Warning: could not read {log_path}: {exc}",
                file=sys.stderr,
            )
            continue

        combined_samples.extend(samples)

        if args.per_file:
            print_report(str(log_path), samples, file_count=1)

    if not combined_samples:
        print("Error: no readable samples found.", file=sys.stderr)
        return 1

    selection = (
        "including incomplete samples"
        if args.include_incomplete
        else "completed samples only"
    )
    print_report(
        f"Combined statistics — {selection}",
        combined_samples,
        file_count=len(log_paths),
    )

    return 0


if __name__ == "__main__":
    main()