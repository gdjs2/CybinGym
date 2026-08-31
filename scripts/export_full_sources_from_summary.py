#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return safe.strip("._-") or "unknown"


def _resolve_source(source_text: str, csv_path: Path, source_root: Path) -> Path:
    source = Path(source_text).expanduser()
    if source.is_absolute():
        return source
    candidates = [
        source_root / source,
        REPO_ROOT / source,
        csv_path.parent / source,
        Path.cwd() / source,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return source_root / source


def iter_csv_paths(paths: Iterable[Path]) -> list[Path]:
    csv_paths: dict[Path, Path] = {}
    for path in paths:
        path = path.expanduser()
        if path.is_file() and path.suffix.lower() == ".csv":
            csv_paths[path.resolve()] = path
        elif path.is_dir():
            for csv_path in path.rglob("*.csv"):
                csv_paths[csv_path.resolve()] = csv_path
    return [csv_paths[key] for key in sorted(csv_paths, key=lambda item: str(item))]


def collect_sources(csv_paths: Iterable[Path], source_root: Path) -> list[dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    for csv_path in csv_paths:
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            for row in reader:
                source_text = _clean(row.get("selected_source"))
                if not source_text:
                    continue
                if source_text in sources:
                    continue
                resolved = _resolve_source(source_text, csv_path, source_root)
                sources[source_text] = {
                    "selected_source": source_text,
                    "resolved_source": str(resolved),
                    "first_csv_path": str(csv_path),
                }
    return [sources[key] for key in sorted(sources)]


def destination_for_source(output_dir: Path, source_text: str, resolved_source: Path) -> Path:
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:12]
    source_name = resolved_source.name or Path(source_text).name or "source"
    return output_dir / f"{digest}__{_safe_name(source_name)}"


def copy_source(source: Path, dest: Path) -> tuple[str, str]:
    if not source.exists():
        return "error", f"source does not exist: {source}"
    if source.is_dir():
        shutil.copytree(source, dest, dirs_exist_ok=True)
        return "ok", "directory"
    if source.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return "ok", "file"
    return "error", f"unsupported source type: {source}"


def write_manifest(output_dir: Path, rows: list[dict[str, str]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "selected_source",
        "resolved_source",
        "output_path",
        "source_kind",
        "first_csv_path",
        "status",
        "message",
    ]
    with (output_dir / "manifest.csv").open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "manifest.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy complete source logs referenced by summary CSV selected_source rows."
    )
    parser.add_argument("--csv", nargs="+", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-root", default=str(REPO_ROOT))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    csv_paths = iter_csv_paths(Path(path) for path in args.csv)
    if not csv_paths:
        raise SystemExit("error: no CSV files found")

    output_dir = Path(args.output_dir).expanduser()
    source_root = Path(args.source_root).expanduser()
    sources = collect_sources(csv_paths, source_root)
    if not sources:
        raise SystemExit("error: no selected_source rows found")

    rows: list[dict[str, str]] = []
    for source_info in sources:
        source_text = source_info["selected_source"]
        resolved = Path(source_info["resolved_source"])
        dest = destination_for_source(output_dir, source_text, resolved)
        status, message = copy_source(resolved, dest)
        source_kind = "directory" if resolved.is_dir() else "file" if resolved.is_file() else ""
        rows.append(
            {
                "selected_source": source_text,
                "resolved_source": str(resolved),
                "output_path": str(dest),
                "source_kind": source_kind,
                "first_csv_path": source_info["first_csv_path"],
                "status": status,
                "message": message,
            }
        )

    write_manifest(output_dir, rows)
    ok_count = sum(row["status"] == "ok" for row in rows)
    error_count = len(rows) - ok_count
    print(f"csv_files: {len(csv_paths)}")
    print(f"unique_sources: {len(rows)}")
    print(f"copied: {ok_count}")
    print(f"errors: {error_count}")
    print(f"output_dir: {output_dir}")
    if error_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
