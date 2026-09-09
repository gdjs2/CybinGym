#!/usr/bin/env python3
"""Export trajectories for every current reported per-sample result row.

This script uses the same final row selection as
integrate_full100_overall_results.py, then exports one trace bundle for each
reported crash/full row for Codex and Kimi. Existing extracted sample traces are
reused when they already match the selected source.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import export_traces_from_summary as traces
import integrate_full100_overall_results as results


TRACE_ROOT = results.ROOT / "reported_execution_traces"
GROUPS = {
    ("crash", "OpenAI Codex"): "crash_codex_gpt-5.6",
    ("crash", "Kimi Code"): "crash_kimi_kimi-k3",
    ("full", "OpenAI Codex"): "full_codex_gpt-5.6",
    ("full", "Kimi Code"): "full_kimi_kimi-k3",
}
ROOT_MANIFEST_JSON = TRACE_ROOT / "manifest.json"
ROOT_MANIFEST_CSV = TRACE_ROOT / "manifest.csv"
TRACE_EXPORT_FAILURES = TRACE_ROOT / "trace_export_failures.csv"
MISSING_FAILURES = TRACE_ROOT / "missing_or_running_failures.csv"
EVALUATION_ERROR_FAILURES = TRACE_ROOT / "evaluation_error_failures.csv"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ordered_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    return fieldnames


def current_reported_rows() -> tuple[
    dict[tuple[str, str], list[dict[str, str]]],
    dict[str, object],
]:
    metadata_rows = results.read_csv(results.DATASET100)
    expected_ids = {row["id"] for row in metadata_rows}
    source_rows_by_key = {
        key: results.load_rows(paths) for key, paths in results.CSV_SOURCES.items()
    }
    eval_rows_by_key, eval_stats = results.load_eval_full_rows(
        metadata_rows,
        expected_ids,
    )
    for key in [("full", "OpenAI Codex"), ("full", "Kimi Code")]:
        source_rows_by_key[key] = results.merge_full_rows(
            key,
            source_rows_by_key[key],
            eval_rows_by_key.get(key, {}),
            metadata_rows,
        )
    for key, rows in source_rows_by_key.items():
        results.validate_dataset_rows(f"{key[1]} {key[0]}", rows, expected_ids)
    return source_rows_by_key, eval_stats


def previous_manifest(output_root: Path) -> dict[tuple[str, str, str, str], dict[str, str]]:
    manifest_path = output_root / traces.MANIFEST_JSON_NAME
    if not manifest_path.exists():
        return {}
    try:
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(rows, list):
        return {}
    indexed: dict[tuple[str, str, str, str], dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        indexed[
            (
                str(row.get("agent_type", "")),
                str(row.get("model", "")),
                str(row.get("sample_id", "")),
                str(row.get("selected_source", "")),
            )
        ] = {key: str(value) for key, value in row.items()}
    return indexed


def entry_identity(entry: traces.CsvEntry) -> tuple[str, str, str, str]:
    row = entry.row
    return (
        str(row.get("selected_agent_type", "")),
        str(row.get("selected_model", "")),
        str(row.get("id", "")),
        str(row.get("selected_source", "")),
    )


def reusable_trace(previous: dict[str, str]) -> bool:
    if previous.get("status") != "ok":
        return False
    output_dir = Path(previous.get("output_dir", ""))
    return (output_dir / traces.INSPECT_TRACE_NAME).exists()


def reuse_result(
    previous: dict[str, str],
    entry: traces.CsvEntry,
    reported_rows_csv: Path,
) -> traces.ExportResult:
    output_dir = Path(previous["output_dir"])
    write_json(output_dir / traces.SUMMARY_ROW_NAME, entry.row)
    return traces.ExportResult(
        csv_path=str(reported_rows_csv),
        row_index=entry.row_index,
        sample_id=str(entry.row.get("id", "")),
        agent_type=str(entry.row.get("selected_agent_type", "")),
        model=str(entry.row.get("selected_model", "")),
        selected_source=str(entry.row.get("selected_source", "")),
        output_dir=str(output_dir),
        trace_kind=previous.get("trace_kind", "inspect_sample"),
        status="ok",
        message="reused existing sample_trace.json",
    )


def export_group(
    key: tuple[str, str],
    rows: list[dict[str, str]],
) -> tuple[dict[str, str], list[traces.ExportResult]]:
    trace_group = GROUPS[key]
    output_root = TRACE_ROOT / trace_group
    output_root.mkdir(parents=True, exist_ok=True)
    reported_rows_csv = output_root / "reported_rows.csv"
    write_csv(reported_rows_csv, ordered_fieldnames(rows), rows)

    previous = previous_manifest(output_root)
    export_results: list[traces.ExportResult] = []
    for row_index, row in enumerate(rows, start=1):
        entry = traces.CsvEntry(
            csv_path=reported_rows_csv,
            row_index=row_index,
            row=row,
        )
        cached = previous.get(entry_identity(entry))
        if cached is not None and reusable_trace(cached):
            export_results.append(reuse_result(cached, entry, reported_rows_csv))
            continue
        export_results.append(
            traces.export_entry(
                entry,
                output_root=output_root,
                source_root=Path.cwd(),
                include_artifacts=False,
            )
        )

    traces.write_manifest(output_root, export_results)
    ok_count = sum(result.status == "ok" for result in export_results)
    sample_trace_count = sum(
        result.status == "ok" and (Path(result.output_dir) / traces.INSPECT_TRACE_NAME).exists()
        for result in export_results
    )
    error_ids = [
        result.sample_id for result in export_results if result.status != "ok"
    ]
    group_summary = {
        "trace_group": trace_group,
        "task_type": key[0],
        "agent": key[1],
        "reported_rows": str(len(rows)),
        "exported_ok": str(ok_count),
        "export_errors": str(len(export_results) - ok_count),
        "sample_trace_files": str(sample_trace_count),
        "reported_rows_csv": str(reported_rows_csv),
        "manifest_csv": str(output_root / traces.MANIFEST_CSV_NAME),
        "manifest_json": str(output_root / traces.MANIFEST_JSON_NAME),
        "error_sample_ids": ",".join(error_ids),
    }
    return group_summary, export_results


def selected_failure_rows(
    source_rows_by_key: dict[tuple[str, str], list[dict[str, str]]],
    predicate_key: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for (task_type, agent), source_rows in source_rows_by_key.items():
        if task_type != "full":
            continue
        for row in source_rows:
            if row.get(predicate_key) != "true":
                continue
            rows.append(
                {
                    "task_type": task_type,
                    "agent": agent,
                    "id": row.get("id", ""),
                    "task_id": row.get("task_id", ""),
                    "entry_name": row.get("entry_name", ""),
                    "selected_source": row.get("selected_source", ""),
                    "sample_status": row.get("sample_status", ""),
                    "effective_failure_reason": row.get("effective_failure_reason", ""),
                }
            )
    return rows


def evaluation_error_rows(
    source_rows_by_key: dict[tuple[str, str], list[dict[str, str]]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for (task_type, agent), source_rows in source_rows_by_key.items():
        if task_type != "full":
            continue
        for row in source_rows:
            if row.get("sample_status") != "error":
                continue
            rows.append(
                {
                    "task_type": task_type,
                    "agent": agent,
                    "id": row.get("id", ""),
                    "task_id": row.get("task_id", ""),
                    "entry_name": row.get("entry_name", ""),
                    "selected_source": row.get("selected_source", ""),
                    "sample_status": row.get("sample_status", ""),
                    "evaluation_error": row.get("evaluation_error", ""),
                    "effective_failure_reason": row.get("effective_failure_reason", ""),
                }
            )
    return rows


def trace_export_failure_rows(
    export_results: list[traces.ExportResult],
) -> list[dict[str, str]]:
    return [
        {
            "csv_path": result.csv_path,
            "row_index": str(result.row_index),
            "sample_id": result.sample_id,
            "agent_type": result.agent_type,
            "model": result.model,
            "selected_source": result.selected_source,
            "output_dir": result.output_dir,
            "message": result.message,
        }
        for result in export_results
        if result.status != "ok"
    ]


def write_failure_reports(
    source_rows_by_key: dict[tuple[str, str], list[dict[str, str]]],
    export_results: list[traces.ExportResult],
) -> None:
    trace_failures = trace_export_failure_rows(export_results)
    write_csv(
        TRACE_EXPORT_FAILURES,
        [
            "csv_path",
            "row_index",
            "sample_id",
            "agent_type",
            "model",
            "selected_source",
            "output_dir",
            "message",
        ],
        trace_failures,
    )
    failure_fieldnames = [
        "task_type",
        "agent",
        "id",
        "task_id",
        "entry_name",
        "selected_source",
        "sample_status",
        "effective_failure_reason",
    ]
    write_csv(
        MISSING_FAILURES,
        failure_fieldnames,
        selected_failure_rows(source_rows_by_key, "missing_or_running_counted_failed"),
    )
    write_csv(
        EVALUATION_ERROR_FAILURES,
        [
            "task_type",
            "agent",
            "id",
            "task_id",
            "entry_name",
            "selected_source",
            "sample_status",
            "evaluation_error",
            "effective_failure_reason",
        ],
        evaluation_error_rows(source_rows_by_key),
    )


def update_current_manifest(
    group_summaries: list[dict[str, str]],
    eval_stats: dict[str, object],
    export_results: list[traces.ExportResult],
) -> None:
    manifest = json.loads(results.MANIFEST_JSON.read_text(encoding="utf-8"))
    manifest["reported_execution_trace_export"] = {
        "script": "scripts/export_current_reported_traces.py",
        "trace_root": str(TRACE_ROOT),
        "manifest_csv": str(ROOT_MANIFEST_CSV),
        "manifest_json": str(ROOT_MANIFEST_JSON),
        "trace_export_failures_csv": str(TRACE_EXPORT_FAILURES),
        "missing_or_running_failures_csv": str(MISSING_FAILURES),
        "evaluation_error_failures_csv": str(EVALUATION_ERROR_FAILURES),
        "groups": group_summaries,
        "total_reported_rows": sum(int(row["reported_rows"]) for row in group_summaries),
        "total_exported_ok": sum(int(row["exported_ok"]) for row in group_summaries),
        "total_export_errors": sum(int(row["export_errors"]) for row in group_summaries),
        "total_sample_trace_files": sum(int(row["sample_trace_files"]) for row in group_summaries),
        "eval_loader": eval_stats,
        "non_extractable_rows": trace_export_failure_rows(export_results),
    }
    write_json(results.MANIFEST_JSON, manifest)


def main() -> None:
    source_rows_by_key, eval_stats = current_reported_rows()
    group_summaries: list[dict[str, str]] = []
    all_export_results: list[traces.ExportResult] = []
    for key in GROUPS:
        summary, export_results = export_group(key, source_rows_by_key[key])
        group_summaries.append(summary)
        all_export_results.extend(export_results)

    write_csv(
        ROOT_MANIFEST_CSV,
        [
            "trace_group",
            "task_type",
            "agent",
            "reported_rows",
            "exported_ok",
            "export_errors",
            "sample_trace_files",
            "reported_rows_csv",
            "manifest_csv",
            "manifest_json",
            "error_sample_ids",
        ],
        group_summaries,
    )
    write_json(ROOT_MANIFEST_JSON, group_summaries)
    write_failure_reports(source_rows_by_key, all_export_results)
    update_current_manifest(group_summaries, eval_stats, all_export_results)
    print(json.dumps(group_summaries, indent=2))
    failures = trace_export_failure_rows(all_export_results)
    if failures:
        print(json.dumps({"trace_export_failures": failures}, indent=2))


if __name__ == "__main__":
    main()
