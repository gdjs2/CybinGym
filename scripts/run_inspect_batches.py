#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence


DEFAULT_BATCH_SIZE = 32


def split_ids(text: str) -> list[str]:
    ids: list[str] = []
    for chunk in text.replace(",", "\n").splitlines():
        item = chunk.strip()
        if item and not item.startswith("#"):
            ids.append(item)
    return ids


def read_id_file(path: Path) -> list[str]:
    return split_ids(path.read_text())


def read_dataset_ids(path: Path) -> list[str]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list of samples")

    ids: list[str] = []
    for index, sample in enumerate(data):
        if not isinstance(sample, dict) or "id" not in sample:
            raise ValueError(f"{path} sample at index {index} has no id")
        ids.append(str(sample["id"]))
    return ids


def resolve_sample_ids(sample_ids: str, dataset: Path) -> list[str]:
    value = sample_ids.strip()
    if not value or value.lower() == "all":
        return read_dataset_ids(dataset)
    if value.startswith("@"):
        return read_id_file(Path(value[1:]))
    return split_ids(value)


def batches(items: Sequence[str], batch_size: int) -> list[list[str]]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")
    return [
        list(items[index : index + batch_size])
        for index in range(0, len(items), batch_size)
    ]


def build_eval_command(
    *,
    uv: str,
    uv_extras: Sequence[str],
    inspect_bin: str,
    task: str,
    batch_ids: Sequence[str],
    inspect_args: Sequence[str],
) -> list[str]:
    cmd = [uv, "run"]
    for extra in uv_extras:
        cmd.extend(["--extra", extra])
    cmd.extend([inspect_bin, "eval", task, "--sample-id", ",".join(batch_ids)])
    cmd.extend(inspect_args)
    return cmd


def format_batch(batch_ids: Sequence[str], max_preview: int = 4) -> str:
    preview = ",".join(batch_ids[:max_preview])
    if len(batch_ids) > max_preview:
        preview += f",.../{batch_ids[-1]}"
    return preview


def run_batches(
    *,
    all_batches: Sequence[Sequence[str]],
    uv: str,
    uv_extras: Sequence[str],
    inspect_bin: str,
    task: str,
    inspect_args: Sequence[str],
    dry_run: bool,
    continue_on_error: bool,
    sleep_between_batches: float,
) -> int:
    failures = 0
    total = len(all_batches)

    for index, batch_ids in enumerate(all_batches, start=1):
        cmd = build_eval_command(
            uv=uv,
            uv_extras=uv_extras,
            inspect_bin=inspect_bin,
            task=task,
            batch_ids=batch_ids,
            inspect_args=inspect_args,
        )
        print(
            f"[batch {index}/{total}] {len(batch_ids)} sample(s): "
            f"{format_batch(batch_ids)}",
            flush=True,
        )
        print(shlex.join(cmd), flush=True)

        if dry_run:
            continue

        result = subprocess.run(cmd)
        if result.returncode != 0:
            failures += 1
            print(
                f"[batch {index}/{total}] failed with exit code {result.returncode}",
                file=sys.stderr,
                flush=True,
            )
            if not continue_on_error:
                return result.returncode

        if sleep_between_batches > 0 and index < total:
            time.sleep(sleep_between_batches)

    return 1 if failures else 0


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run Inspect evals in hard sample-id batches.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument(
        "--sample-ids",
        default="all",
        help="Comma/newline separated ids, 'all' for --dataset, or @path/to/ids.txt.",
    )
    parser.add_argument("--dataset", default="dataset.json")
    parser.add_argument("--task", default="cybingym.py")
    parser.add_argument("--uv", default="uv")
    parser.add_argument(
        "--uv-extra",
        action="append",
        default=[],
        help="Extra to pass to uv run; repeat for multiple extras.",
    )
    parser.add_argument("--inspect-bin", default="inspect")
    parser.add_argument("--sleep-between-batches", type=float, default=0.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Run later batches even if an earlier batch exits nonzero.",
    )
    parser.add_argument(
        "inspect_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to 'inspect eval' after '--'.",
    )
    args = parser.parse_args(argv)
    inspect_args = list(args.inspect_args)
    if inspect_args and inspect_args[0] == "--":
        inspect_args = inspect_args[1:]
    return args, inspect_args


def main(argv: Sequence[str] | None = None) -> int:
    args, inspect_args = parse_args(sys.argv[1:] if argv is None else argv)
    sample_ids = resolve_sample_ids(args.sample_ids, Path(args.dataset))
    all_batches = batches(sample_ids, args.batch_size)
    if not all_batches:
        print("No sample ids selected.", file=sys.stderr)
        return 2

    return run_batches(
        all_batches=all_batches,
        uv=args.uv,
        uv_extras=args.uv_extra,
        inspect_bin=args.inspect_bin,
        task=args.task,
        inspect_args=inspect_args,
        dry_run=args.dry_run,
        continue_on_error=args.continue_on_error,
        sleep_between_batches=args.sleep_between_batches,
    )


if __name__ == "__main__":
    raise SystemExit(main())
