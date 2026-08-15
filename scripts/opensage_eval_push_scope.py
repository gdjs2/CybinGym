#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = REPO_ROOT / "evals" / "opensage_inspect"
LOG_ROOT = REPO_ROOT / "logs"
RUN_STAMP_RE = re.compile(r"^\d{6}_\d{6}_\d{6}$")
ZERO_SHA = "0" * 40
EXCLUDED_EVAL_FILE_NAMES = {"execution_debug.log"}


@dataclasses.dataclass(frozen=True)
class RunDir:
    path: Path
    timestamp: datetime

    @property
    def relpath(self) -> str:
        return self.path.relative_to(REPO_ROOT).as_posix()


def run_git(args: list[str], *, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def discover_run_dirs() -> list[RunDir]:
    if not RUN_ROOT.exists():
        return []

    runs: list[RunDir] = []
    for sample_dir in RUN_ROOT.iterdir():
        if not sample_dir.is_dir():
            continue
        for run_dir in sample_dir.iterdir():
            if not run_dir.is_dir() or not RUN_STAMP_RE.match(run_dir.name):
                continue
            try:
                timestamp = datetime.strptime(run_dir.name, "%y%m%d_%H%M%S_%f")
            except ValueError:
                continue
            runs.append(RunDir(path=run_dir, timestamp=timestamp))
    return sorted(runs, key=lambda run: run.timestamp)


def latest_batches(*, count: int, gap_seconds: int) -> list[list[RunDir]]:
    runs = discover_run_dirs()
    if not runs:
        return []

    batches: list[list[RunDir]] = [[runs[0]]]
    for run in runs[1:]:
        previous = batches[-1][-1]
        gap = (run.timestamp - previous.timestamp).total_seconds()
        if gap > gap_seconds:
            batches.append([run])
        else:
            batches[-1].append(run)
    return batches[-count:]


def latest_log_files(*, count: int) -> list[Path]:
    if not LOG_ROOT.exists():
        return []
    return sorted(LOG_ROOT.glob("*.eval"))[-count:]


def allowed_eval_paths(*, count: int, gap_seconds: int) -> tuple[set[str], set[str]]:
    allowed_dirs: set[str] = set()
    allowed_files: set[str] = set()

    for batch in latest_batches(count=count, gap_seconds=gap_seconds):
        for run in batch:
            allowed_dirs.add(run.relpath)

    for summary_path in (REPO_ROOT / "evals").glob("opensage_history*.json"):
        allowed_files.add(summary_path.relative_to(REPO_ROOT).as_posix())

    for log_path in latest_log_files(count=count):
        allowed_files.add(log_path.relative_to(REPO_ROOT).as_posix())

    return allowed_dirs, allowed_files


def path_allowed(path: str, *, allowed_dirs: set[str], allowed_files: set[str]) -> bool:
    if path in allowed_files:
        return True
    return any(path == directory or path.startswith(f"{directory}/") for directory in allowed_dirs)


def is_excluded_eval_artifact(path: str) -> bool:
    return Path(path).name in EXCLUDED_EVAL_FILE_NAMES


def is_scoped_eval_path(path: str) -> bool:
    return (
        path.startswith("evals/opensage_inspect/")
        or path.startswith("evals/opensage_history")
        or (path.startswith("logs/") and path.endswith(".eval"))
    )


def staged_paths() -> list[str]:
    output = run_git(["diff", "--cached", "--name-only", "-z"])
    return [path for path in output.split("\0") if path]


def pushed_paths(pre_push_stdin: str) -> list[str]:
    paths: set[str] = set()
    for line in pre_push_stdin.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if local_sha == ZERO_SHA:
            continue
        if remote_sha == ZERO_SHA:
            names = run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", local_sha])
        else:
            names = run_git(["diff", "--name-only", f"{remote_sha}..{local_sha}"])
        paths.update(path for path in names.splitlines() if path)
    return sorted(paths)


def check_paths(paths: list[str], *, count: int, gap_seconds: int) -> int:
    allowed_dirs, allowed_files = allowed_eval_paths(count=count, gap_seconds=gap_seconds)
    excluded = [path for path in paths if is_scoped_eval_path(path) and is_excluded_eval_artifact(path)]
    if excluded:
        print(
            "Refusing push/stage: execution_debug.log files are local-only artifacts.",
            file=sys.stderr,
        )
        print("\nExcluded paths:", file=sys.stderr)
        for path in excluded:
            print(f"  {path}", file=sys.stderr)
        return 1

    violations = [
        path
        for path in paths
        if is_scoped_eval_path(path)
        and not path_allowed(path, allowed_dirs=allowed_dirs, allowed_files=allowed_files)
    ]
    if not violations:
        return 0

    print(
        f"Refusing push/stage: only the latest {count} OpenSAGE eval batches "
        f"and latest {count} logs/*.eval files are allowed.",
        file=sys.stderr,
    )
    print("\nAllowed eval run directories:", file=sys.stderr)
    for directory in sorted(allowed_dirs):
        print(f"  {directory}", file=sys.stderr)
    print("\nViolating paths:", file=sys.stderr)
    for path in violations:
        print(f"  {path}", file=sys.stderr)
    return 1


def cmd_list(args: argparse.Namespace) -> int:
    allowed_dirs, allowed_files = allowed_eval_paths(
        count=args.count,
        gap_seconds=args.gap_seconds,
    )
    for directory in sorted(allowed_dirs):
        print(directory)
    for path in sorted(allowed_files):
        print(path)
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    allowed_dirs, allowed_files = allowed_eval_paths(
        count=args.count,
        gap_seconds=args.gap_seconds,
    )
    paths: set[str] = {
        path
        for path in allowed_files
        if (REPO_ROOT / path).exists() and not is_excluded_eval_artifact(path)
    }
    for directory in allowed_dirs:
        run_dir = REPO_ROOT / directory
        if not run_dir.exists():
            continue
        for path in run_dir.rglob("*"):
            if not path.is_file():
                continue
            relpath = path.relative_to(REPO_ROOT).as_posix()
            if not is_excluded_eval_artifact(relpath):
                paths.add(relpath)
    sorted_paths = sorted(paths)
    if not sorted_paths:
        print("No eval artifacts found to stage.")
        return 0
    run_git(["add", "-f", "--", *sorted_paths])
    for path in sorted_paths:
        print(path)
    return 0


def cmd_check_index(args: argparse.Namespace) -> int:
    return check_paths(
        staged_paths(),
        count=args.count,
        gap_seconds=args.gap_seconds,
    )


def cmd_check_push(args: argparse.Namespace) -> int:
    return check_paths(
        pushed_paths(sys.stdin.read()),
        count=args.count,
        gap_seconds=args.gap_seconds,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Keep pushed OpenSAGE eval artifacts scoped to latest batches."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=2,
        help="Number of latest eval batches/log files to allow.",
    )
    parser.add_argument(
        "--gap-seconds",
        type=int,
        default=600,
        help="Start a new eval batch when run directory timestamps differ by this gap.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list").set_defaults(func=cmd_list)
    subparsers.add_parser("stage").set_defaults(func=cmd_stage)
    subparsers.add_parser("check-index").set_defaults(func=cmd_check_index)
    subparsers.add_parser("check-push").set_defaults(func=cmd_check_push)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be positive")
    if args.gap_seconds < 1:
        parser.error("--gap-seconds must be positive")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
