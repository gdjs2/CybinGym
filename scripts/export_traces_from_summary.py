#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV_DIR = REPO_ROOT / "reports" / "dataset20_logs"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "dataset20_traces"
SUMMARY_ROW_NAME = "summary_row.json"
RUN_HEADER_NAME = "run_header.json"
MANIFEST_CSV_NAME = "manifest.csv"
MANIFEST_JSON_NAME = "manifest.json"
INSPECT_TRACE_NAME = "sample_trace.json"
INSPECT_SUMMARY_FALLBACK_NAME = "summary_trace.json"

OPENSAGE_RUN_FILES = [
    "inspect_score.json",
    "cybingym_result.json",
    "opensage_bridge_status.json",
    "eval_params.json",
    "evaluation_master.log",
    "opensage_stdout.log",
    "opensage_stderr.log",
]
OPENSAGE_TASK_TRACE_FILES = [
    "session_trace.json",
    "live_events.jsonl",
    "execution_info.log",
    "cost_info.json",
    "system_prompt.txt",
    "config_used.toml",
    "mcp_preflight.json",
]
OPENSAGE_ARTIFACT_FILES = ["poc", "poc_crash"]


@dataclass(frozen=True)
class CsvEntry:
    csv_path: Path
    row_index: int
    row: dict[str, str]


@dataclass(frozen=True)
class ExportResult:
    csv_path: str
    row_index: int
    sample_id: str
    agent_type: str
    model: str
    selected_source: str
    output_dir: str
    trace_kind: str
    status: str
    message: str = ""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return safe.strip("._-") or "unknown"


def _read_json_bytes(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"))


def _read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_file(source: Path, dest: Path) -> bool:
    if not source.exists() or not source.is_file():
        return False
    if source.name.endswith("_debug.log") or source.name == "debug.log":
        return False
    if source.name == "metadata.json":
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return True


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


def read_csv_entries(csv_paths: Iterable[Path]) -> list[CsvEntry]:
    entries: list[CsvEntry] = []
    for csv_path in csv_paths:
        with csv_path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            for row_index, row in enumerate(reader, start=1):
                entries.append(CsvEntry(csv_path=csv_path, row_index=row_index, row=dict(row)))
    return entries


def _entry_identity(entry: CsvEntry) -> tuple[str, str, str, str]:
    row = entry.row
    return (
        _clean(row.get("selected_agent_type")),
        _clean(row.get("selected_model")),
        _clean(row.get("id")),
        _clean(row.get("selected_source")),
    )


def filter_selected_entries(entries: Iterable[CsvEntry]) -> list[CsvEntry]:
    selected: list[CsvEntry] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in entries:
        row = entry.row
        if not _clean(row.get("id")) or not _clean(row.get("selected_source")):
            continue
        identity = _entry_identity(entry)
        if identity in seen:
            continue
        seen.add(identity)
        selected.append(entry)
    return selected


def output_dir_for_entry(output_root: Path, entry: CsvEntry) -> Path:
    row = entry.row
    return (
        output_root
        / _safe_name(_clean(row.get("selected_agent_type")) or "unknown_agent")
        / _safe_name(_clean(row.get("selected_model")) or "unknown_model")
        / _safe_name(_clean(row.get("id")) or f"row_{entry.row_index}")
    )


def _load_archive_json(archive: zipfile.ZipFile, name: str) -> Any:
    try:
        return _read_json_bytes(archive.read(name))
    except KeyError:
        return None


def _journal_summary_sort_key(name: str) -> tuple[int, str]:
    stem = Path(name).stem
    try:
        return (int(stem), name)
    except ValueError:
        return (sys.maxsize, name)


def _matching_summary_from_archive(
    archive: zipfile.ZipFile,
    sample_id: str,
) -> dict[str, Any] | None:
    summaries = _load_archive_json(archive, "summaries.json")
    payloads = [summaries] if summaries is not None else []
    for name in sorted(
        (item for item in archive.namelist() if item.startswith("_journal/summaries/") and item.endswith(".json")),
        key=_journal_summary_sort_key,
    ):
        payload = _load_archive_json(archive, name)
        if payload is not None:
            payloads.append(payload)

    for payload in payloads:
        rows = payload.get("samples") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and _clean(row.get("id")) == sample_id:
                return row
    return None


def _sample_names_for_archive(archive: zipfile.ZipFile, sample_id: str) -> list[str]:
    exact = f"samples/{sample_id}_epoch_1.json"
    names = archive.namelist()
    matches = [name for name in names if name == exact]
    matches.extend(
        name
        for name in names
        if name.startswith(f"samples/{sample_id}_epoch_") and name.endswith(".json") and name != exact
    )
    return matches


def export_inspect_archive_trace(source: Path, sample_id: str, dest_dir: Path) -> str:
    try:
        import zipfile_zstd  # noqa: F401 - registers ZIP Zstandard support.
    except ImportError:
        pass

    with zipfile.ZipFile(source) as archive:
        header = _load_archive_json(archive, "header.json")
        if header is None:
            header = _load_archive_json(archive, "_journal/start.json")
        if header is not None:
            _write_json(dest_dir / RUN_HEADER_NAME, header)

        sample_names = _sample_names_for_archive(archive, sample_id)
        if sample_names:
            sample = _load_archive_json(archive, sample_names[0])
            _write_json(dest_dir / INSPECT_TRACE_NAME, sample)
            if len(sample_names) > 1:
                for name in sample_names[1:]:
                    _write_json(dest_dir / f"{Path(name).stem}.json", _load_archive_json(archive, name))
            return "inspect_sample"

        summary = _matching_summary_from_archive(archive, sample_id)
        if summary is not None:
            _write_json(dest_dir / INSPECT_SUMMARY_FALLBACK_NAME, summary)
            return "inspect_summary_fallback"

    raise FileNotFoundError(f"sample {sample_id} not found in {source}")


def export_inspect_directory_trace(source: Path, sample_id: str, dest_dir: Path) -> str:
    header_path = source / "header.json"
    if not header_path.exists():
        header_path = source / "_journal" / "start.json"
    if header_path.exists():
        _write_json(dest_dir / RUN_HEADER_NAME, _read_json_file(header_path))

    sample_paths = sorted((source / "samples").glob(f"{sample_id}_epoch_*.json"))
    if sample_paths:
        _write_json(dest_dir / INSPECT_TRACE_NAME, _read_json_file(sample_paths[0]))
        for extra_path in sample_paths[1:]:
            _write_json(dest_dir / extra_path.name, _read_json_file(extra_path))
        return "inspect_sample"

    summary_paths = []
    summaries_path = source / "summaries.json"
    if summaries_path.exists():
        summary_paths.append(summaries_path)
    journal_dir = source / "_journal" / "summaries"
    if journal_dir.exists():
        summary_paths.extend(sorted(journal_dir.glob("*.json"), key=lambda item: _journal_summary_sort_key(item.name)))
    for summary_path in summary_paths:
        payload = _read_json_file(summary_path)
        rows = payload.get("samples") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and _clean(row.get("id")) == sample_id:
                _write_json(dest_dir / INSPECT_SUMMARY_FALLBACK_NAME, row)
                return "inspect_summary_fallback"

    raise FileNotFoundError(f"sample {sample_id} not found in {source}")


def export_opensage_trace(source: Path, sample_id: str, dest_dir: Path, include_artifacts: bool) -> str:
    copied = 0
    run_dest = dest_dir / "run"
    for name in OPENSAGE_RUN_FILES:
        copied += int(_copy_file(source / name, run_dest / name))

    task_dir = source / f"cybingym_{sample_id}"
    if not task_dir.exists():
        matches = sorted(source.glob("cybingym_*"))
        task_dir = matches[0] if matches else task_dir
    task_dest = dest_dir / task_dir.name
    for name in OPENSAGE_TASK_TRACE_FILES:
        copied += int(_copy_file(task_dir / name, task_dest / name))

    if include_artifacts:
        for name in OPENSAGE_ARTIFACT_FILES:
            copied += int(_copy_file(source / name, run_dest / name))

    if copied == 0:
        raise FileNotFoundError(f"no OpenSAGE trace files found in {source}")
    return "opensage_trace"


def export_entry(
    entry: CsvEntry,
    *,
    output_root: Path,
    source_root: Path,
    include_artifacts: bool,
) -> ExportResult:
    row = entry.row
    sample_id = _clean(row.get("id"))
    agent_type = _clean(row.get("selected_agent_type"))
    model = _clean(row.get("selected_model"))
    selected_source = _clean(row.get("selected_source"))
    dest_dir = output_dir_for_entry(output_root, entry)
    dest_dir.mkdir(parents=True, exist_ok=True)
    _write_json(dest_dir / SUMMARY_ROW_NAME, row)

    source = _resolve_source(selected_source, entry.csv_path, source_root)
    try:
        if not source.exists():
            raise FileNotFoundError(f"source does not exist: {source}")
        if agent_type == "opensage" and source.is_dir():
            trace_kind = export_opensage_trace(source, sample_id, dest_dir, include_artifacts)
        elif source.is_dir():
            trace_kind = export_inspect_directory_trace(source, sample_id, dest_dir)
        else:
            trace_kind = export_inspect_archive_trace(source, sample_id, dest_dir)
        status = "ok"
        message = ""
    except Exception as error:  # Keep exporting other samples and record the failure.
        trace_kind = ""
        status = "error"
        message = str(error)

    return ExportResult(
        csv_path=str(entry.csv_path),
        row_index=entry.row_index,
        sample_id=sample_id,
        agent_type=agent_type,
        model=model,
        selected_source=selected_source,
        output_dir=str(dest_dir),
        trace_kind=trace_kind,
        status=status,
        message=message,
    )


def write_manifest(output_root: Path, results: list[ExportResult]) -> None:
    rows = [result.__dict__ for result in results]
    _write_json(output_root / MANIFEST_JSON_NAME, rows)
    fieldnames = list(ExportResult.__dataclass_fields__.keys())
    with (output_root / MANIFEST_CSV_NAME).open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export selected per-sample traces from per-agent/per-model summary CSVs."
    )
    parser.add_argument(
        "--csv",
        nargs="+",
        default=[str(DEFAULT_CSV_DIR)],
        help="Summary CSV files or directories containing CSV files. Defaults to reports/dataset20_logs.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-root", default=str(REPO_ROOT))
    parser.add_argument(
        "--include-artifacts",
        action="store_true",
        help="Also copy OpenSAGE poc/poc_crash artifacts. Off by default to keep trace exports focused.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    csv_paths = iter_csv_paths(Path(path) for path in args.csv)
    if not csv_paths:
        raise SystemExit("error: no CSV files found")

    output_root = Path(args.output_dir).expanduser()
    source_root = Path(args.source_root).expanduser()
    entries = filter_selected_entries(read_csv_entries(csv_paths))
    if not entries:
        raise SystemExit("error: no selected_source rows found in CSV input")

    results = [
        export_entry(
            entry,
            output_root=output_root,
            source_root=source_root,
            include_artifacts=args.include_artifacts,
        )
        for entry in entries
    ]
    write_manifest(output_root, results)

    ok_count = sum(1 for result in results if result.status == "ok")
    error_count = len(results) - ok_count
    print(f"csv_files: {len(csv_paths)}")
    print(f"selected_rows: {len(entries)}")
    print(f"exported: {ok_count}")
    print(f"errors: {error_count}")
    print(f"output_dir: {output_root}")
    if error_count:
        print("export_errors:", file=sys.stderr)
        for result in results:
            if result.status == "error":
                print(f"- {result.agent_type} {result.model} {result.sample_id}: {result.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
