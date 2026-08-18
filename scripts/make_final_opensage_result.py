#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_METADATA_CSV = (
    REPO_ROOT
    / ".."
    / "cybingym_logs"
    / "difficulty"
    / "exp.none.metadata.check.classification.exploited.sampled150.csv"
)
DEFAULT_SUMMARY_SCRIPT = REPO_ROOT / "scripts" / "make_exploit_summary.py"
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"
DEFAULT_EVALS_DIR = REPO_ROOT / "evals"
DEFAULT_SCORER = "cybingym_scorer"
CRASH_TEST_KEY = "Crash Test"
EXPLOIT_TEST_KEY = "Exploit Test"
SUCCESS_VALUE = "C"
OPENSAGE_OUTPUT_RE = re.compile(r"^OpenSAGE output:\s*(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class CandidateRow:
    row: dict[str, Any]
    log_path: Path
    model: str
    timestamp: str
    run_dir: Path | None = None
    estimated_cost: float | None = None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._" else "_" for char in value)
    return safe.strip("._-") or "model"


def _parse_datetime(value: Any) -> datetime:
    text = _clean(value)
    if not text:
        return datetime.min
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.min


def _score_object(row: dict[str, Any], scorer: str) -> Any:
    return (row.get("scores") or {}).get(scorer) or {}


def _test_score_value(value: Any, test_key: str) -> str:
    if isinstance(value, dict):
        if test_key in value:
            return _clean(value.get(test_key))
        if "value" in value:
            return _test_score_value(value.get("value"), test_key)
    return ""


def _scalar_score_value(value: Any) -> str:
    if isinstance(value, dict):
        if "value" in value:
            return _scalar_score_value(value.get("value"))
        return ""
    return _clean(value)


def _score_values(row: dict[str, Any], scorer: str) -> dict[str, str]:
    score = _score_object(row, scorer)
    poc_value = _test_score_value(score, CRASH_TEST_KEY)
    exploit_value = _test_score_value(score, EXPLOIT_TEST_KEY)
    if not poc_value and not exploit_value:
        scalar_value = _scalar_score_value(score)
        poc_value = scalar_value
        exploit_value = scalar_value
    return {
        "poc": poc_value,
        "exploit": exploit_value,
    }


def _extract_run_dir(sample_record: Any) -> Path | None:
    if not isinstance(sample_record, dict):
        return None
    for message in reversed(sample_record.get("messages") or []):
        content = _clean(message.get("content") if isinstance(message, dict) else "")
        match = OPENSAGE_OUTPUT_RE.search(content)
        if match:
            return Path(match.group(1).strip())
    return None


def _read_cost_info(run_dir: Path | None) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    cost_path = run_dir / f"cybingym_{run_dir.parent.name}" / "cost_info.json"
    if not cost_path.exists():
        matches = sorted(run_dir.glob("*/cost_info.json"))
        if not matches:
            return None
        cost_path = matches[0]
    try:
        payload = json.loads(cost_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _usage_from_cost_info(cost_info: dict[str, Any] | None, model: str) -> dict[str, Any]:
    if not cost_info:
        return {}
    model_key = model.split("/")[-1]
    per_model_usage = ((cost_info.get("budget") or {}).get("per_model_usage") or {})
    usage = per_model_usage.get(model_key)
    if not isinstance(usage, dict):
        usage = next(iter(per_model_usage.values()), {}) if per_model_usage else {}
    if not isinstance(usage, dict):
        usage = {}

    token_usage = cost_info.get("token_usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or token_usage.get("total_input_tokens") or 0)
    output_tokens = int(
        usage.get("completion_tokens") or token_usage.get("total_output_tokens") or 0
    )
    cached_tokens = int(usage.get("cached_tokens") or token_usage.get("total_cached_tokens") or 0)
    total_tokens = int(
        usage.get("total_tokens")
        or token_usage.get("total_tokens")
        or prompt_tokens + output_tokens
    )
    input_tokens = max(prompt_tokens - cached_tokens, 0)
    return {
        model_key: {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_tokens_cache_write": 0,
            "input_tokens_cache_read": cached_tokens,
            "total_tokens": total_tokens,
        }
    }


def _estimated_cost_from_cost_info(cost_info: dict[str, Any] | None) -> float | None:
    if not cost_info:
        return None
    budget = cost_info.get("budget") or {}
    for value in (budget.get("spent_cost"), cost_info.get("estimated_cost")):
        if value is not None:
            return float(value)
    per_model_usage = budget.get("per_model_usage") or {}
    costs = [
        float(usage.get("estimated_cost") or 0)
        for usage in per_model_usage.values()
        if isinstance(usage, dict)
    ]
    if costs:
        return sum(costs)
    return None


def _row_rank(candidate: CandidateRow, scorer: str) -> tuple[int, int, int, datetime, str]:
    row = candidate.row
    values = _score_values(row, scorer)
    is_completed = bool(row.get("completed"))
    completed_at = _parse_datetime(row.get("completed_at") or row.get("started_at"))
    return (
        int(values["exploit"] == SUCCESS_VALUE),
        int(values["poc"] == SUCCESS_VALUE),
        int(is_completed),
        completed_at,
        str(candidate.log_path),
    )


def _ensure_zip_zstd() -> None:
    try:
        import zipfile_zstd  # noqa: F401 - registers ZIP Zstandard support.
    except ImportError:
        raise SystemExit(
            "error: missing Python package 'zipfile_zstd'; install it in the "
            "environment used to run this script"
        )


def _read_archive_json(archive: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(archive.read(name))
    except (KeyError, OSError, json.JSONDecodeError, NotImplementedError):
        return None


def _archive_model(start_json: Any) -> str:
    if not isinstance(start_json, dict):
        return ""
    eval_info = start_json.get("eval") or {}
    if not isinstance(eval_info, dict):
        return ""
    task_args = eval_info.get("task_args") or {}
    if not isinstance(task_args, dict):
        task_args = {}
    return _clean(task_args.get("opensage_model")) or _clean(eval_info.get("model"))


def _load_metadata_ids(metadata_csv: Path) -> list[str]:
    with metadata_csv.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or "id" not in reader.fieldnames:
            raise SystemExit(f"error: metadata CSV must contain an id column: {metadata_csv}")
        ids = [_clean(row.get("id")) for row in reader]
    ids = [sample_id for sample_id in ids if sample_id]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"error: duplicate ids in metadata CSV: {metadata_csv}")
    return ids


def _iter_eval_logs(logs_dir: Path) -> list[Path]:
    if not logs_dir.exists():
        raise SystemExit(f"error: logs directory not found: {logs_dir}")
    return sorted(logs_dir.glob("*.eval"))


def _load_candidates(
    *,
    logs_dir: Path,
    metadata_ids: set[str],
    model: str,
    scorer: str,
) -> tuple[dict[str, CandidateRow], dict[str, int]]:
    _ensure_zip_zstd()
    selected: dict[str, CandidateRow] = {}
    stats = {
        "archives_seen": 0,
        "archives_matching_model": 0,
        "rows_seen": 0,
        "rows_in_metadata": 0,
        "rows_replaced": 0,
        "rows_enriched_with_cost": 0,
    }

    for log_path in _iter_eval_logs(logs_dir):
        stats["archives_seen"] += 1
        try:
            with zipfile.ZipFile(log_path) as archive:
                archive_model = _archive_model(_read_archive_json(archive, "_journal/start.json"))
                if archive_model != model:
                    continue
                stats["archives_matching_model"] += 1
                summaries = _read_archive_json(archive, "summaries.json")
                if not isinstance(summaries, list):
                    continue

                for row in summaries:
                    if not isinstance(row, dict):
                        continue
                    stats["rows_seen"] += 1
                    sample_id = _clean(row.get("id"))
                    if not sample_id or sample_id not in metadata_ids:
                        continue
                    stats["rows_in_metadata"] += 1
                    epoch = row.get("epoch", 1)
                    sample_record = _read_archive_json(
                        archive,
                        f"samples/{sample_id}_epoch_{epoch}.json",
                    )
                    run_dir = _extract_run_dir(sample_record)
                    cost_info = _read_cost_info(run_dir)
                    estimated_cost = _estimated_cost_from_cost_info(cost_info)
                    merged_row = dict(row)
                    if not merged_row.get("model_usage"):
                        model_usage = _usage_from_cost_info(cost_info, model)
                        if model_usage:
                            merged_row["model_usage"] = model_usage
                            stats["rows_enriched_with_cost"] += 1
                    if run_dir is not None:
                        merged_row["_opensage_run_dir"] = str(run_dir)
                    if estimated_cost is not None:
                        merged_row["_opensage_estimated_cost_usd"] = estimated_cost
                    candidate = CandidateRow(
                        row=merged_row,
                        log_path=log_path,
                        model=archive_model,
                        timestamp=_clean(row.get("completed_at") or row.get("started_at")),
                        run_dir=run_dir,
                        estimated_cost=estimated_cost,
                    )
                    previous = selected.get(sample_id)
                    if previous is None or _row_rank(candidate, scorer) > _row_rank(previous, scorer):
                        if previous is not None:
                            stats["rows_replaced"] += 1
                        selected[sample_id] = candidate
        except zipfile.BadZipFile:
            continue

    return selected, stats


def _write_merged_json(
    *,
    output_path: Path,
    metadata_ids: list[str],
    selected: dict[str, CandidateRow],
) -> list[dict[str, Any]]:
    rows = [selected[sample_id].row for sample_id in metadata_ids if sample_id in selected]
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in rows:
        sample_id = _clean(row.get("id"))
        if sample_id in seen:
            duplicates.append(sample_id)
        seen.add(sample_id)
    if duplicates:
        raise SystemExit(f"error: merged output has duplicate ids: {', '.join(duplicates)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return rows


def _run_summary_script(
    *,
    summary_script: Path,
    metadata_csv: Path,
    merged_json: Path,
    output_csv: Path,
    scorer: str,
    input_price: float,
    output_price: float,
    cache_write_price: float,
    cache_read_price: float,
) -> subprocess.CompletedProcess[str]:
    if not summary_script.exists():
        raise SystemExit(f"error: summary script not found: {summary_script}")
    command = [
        sys.executable,
        str(summary_script),
        "--metadata-csv",
        str(metadata_csv),
        "--eval-log",
        str(merged_json),
        "--output",
        str(output_csv),
        "--scorer",
        scorer,
        "--input-price",
        str(input_price),
        "--output-price",
        str(output_price),
        "--cache-write-price",
        str(cache_write_price),
        "--cache-read-price",
        str(cache_read_price),
    ]
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _rewrite_csv_costs_from_cost_info(
    *,
    output_csv: Path,
    selected: dict[str, CandidateRow],
) -> dict[str, Any]:
    with output_csv.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames:
            raise SystemExit(f"error: final CSV has no header: {output_csv}")
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    if "cost_usd" not in fieldnames:
        fieldnames.append("cost_usd")

    cost_rows = 0
    missing_cost_ids: list[str] = []
    total_cost = 0.0
    for row in rows:
        sample_id = _clean(row.get("id"))
        candidate = selected.get(sample_id)
        if candidate is None:
            continue
        if candidate.estimated_cost is None:
            if row.get("poc") != "" or row.get("exploit") != "":
                missing_cost_ids.append(sample_id)
            continue
        row["cost_usd"] = f"{candidate.estimated_cost:.6f}"
        cost_rows += 1
        total_cost += candidate.estimated_cost

    with output_csv.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    matched_rows = [row for row in rows if row.get("poc") != "" or row.get("exploit") != ""]
    poc = sum(1 for row in matched_rows if row.get("poc") == "true")
    exploit = sum(1 for row in matched_rows if row.get("exploit") == "true")
    return {
        "rows": len(rows),
        "matched": len(matched_rows),
        "poc": poc,
        "not_poc": len(matched_rows) - poc,
        "exploit": exploit,
        "not_exploit": len(matched_rows) - exploit,
        "missing_from_log": len(rows) - len(matched_rows),
        "cost_rows": cost_rows,
        "missing_cost_ids": missing_cost_ids,
        "total_cost_usd": total_cost,
        "average_cost_usd": total_cost / cost_rows if cost_rows else 0.0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge multiple Inspect .eval summaries for one OpenSAGE model and "
            "produce a final PoC/exploit summary CSV."
        )
    )
    parser.add_argument("--metadata-csv", default=str(DEFAULT_METADATA_CSV))
    parser.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR))
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", default="")
    parser.add_argument("--merged-json", default="")
    parser.add_argument("--summary-script", default=str(DEFAULT_SUMMARY_SCRIPT))
    parser.add_argument("--scorer", default=DEFAULT_SCORER)
    parser.add_argument("--input-price", type=float, default=5.0)
    parser.add_argument("--output-price", type=float, default=25.0)
    parser.add_argument("--cache-write-price", type=float, default=1.25)
    parser.add_argument("--cache-read-price", type=float, default=0.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    model = _clean(args.model)
    if not model:
        raise SystemExit("error: --model is required")

    model_suffix = _safe_name(model)
    metadata_csv = Path(args.metadata_csv).expanduser()
    logs_dir = Path(args.logs_dir).expanduser()
    merged_json = (
        Path(args.merged_json).expanduser()
        if args.merged_json
        else DEFAULT_EVALS_DIR / f"merged_summaries_{model_suffix}.json"
    )
    output_csv = (
        Path(args.output).expanduser()
        if args.output
        else DEFAULT_EVALS_DIR / f"final_exploit_summary_{model_suffix}.csv"
    )
    summary_script = Path(args.summary_script).expanduser()

    metadata_ids = _load_metadata_ids(metadata_csv)
    selected, stats = _load_candidates(
        logs_dir=logs_dir,
        metadata_ids=set(metadata_ids),
        model=model,
        scorer=args.scorer,
    )
    if not selected:
        raise SystemExit(f"error: no matching eval rows found for model {model!r}")

    merged_rows = _write_merged_json(
        output_path=merged_json,
        metadata_ids=metadata_ids,
        selected=selected,
    )
    summary_result = _run_summary_script(
        summary_script=summary_script,
        metadata_csv=metadata_csv,
        merged_json=merged_json,
        output_csv=output_csv,
        scorer=args.scorer,
        input_price=args.input_price,
        output_price=args.output_price,
        cache_write_price=args.cache_write_price,
        cache_read_price=args.cache_read_price,
    )
    summary_stdout = summary_result.stdout or ""
    if summary_result.stderr:
        print(summary_result.stderr, end="", file=sys.stderr)
    if summary_result.returncode != 0:
        if summary_stdout:
            print(summary_stdout, end="")
        return summary_result.returncode

    final_summary = _rewrite_csv_costs_from_cost_info(
        output_csv=output_csv,
        selected=selected,
    )

    missing = len(metadata_ids) - len(merged_rows)
    merged_score_values = [_score_values(row, args.scorer) for row in merged_rows]
    poc_rows = sum(1 for values in merged_score_values if values["poc"] == SUCCESS_VALUE)
    exploit_rows = sum(
        1 for values in merged_score_values if values["exploit"] == SUCCESS_VALUE
    )
    print(f"wrote: {output_csv}")
    print(f"rows: {final_summary['rows']}")
    print(f"poc: {final_summary['poc']}")
    print(f"not_poc: {final_summary['not_poc']}")
    print(f"exploit: {final_summary['exploit']}")
    print(f"not_exploit: {final_summary['not_exploit']}")
    print(f"missing_from_log: {final_summary['missing_from_log']}")
    print("cost_source: OpenSAGE cost_info.json")
    print(f"cost_rows_from_cost_info: {final_summary['cost_rows']}")
    print(f"missing_cost_rows: {len(final_summary['missing_cost_ids'])}")
    print(f"total_cost_usd: {final_summary['total_cost_usd']:.6f}")
    print(f"average_cost_usd: {final_summary['average_cost_usd']:.6f}")
    print(f"merged_json: {merged_json}")
    print(f"final_csv: {output_csv}")
    print(f"model: {model}")
    print(f"metadata_rows: {len(metadata_ids)}")
    print(f"matched_rows: {len(merged_rows)}")
    print(f"missing_rows: {missing}")
    print(f"poc_rows: {poc_rows}")
    print(f"exploit_rows: {exploit_rows}")
    print(f"archives_seen: {stats['archives_seen']}")
    print(f"archives_matching_model: {stats['archives_matching_model']}")
    print(f"candidate_rows: {stats['rows_in_metadata']}")
    print(f"cost_enriched_rows: {stats['rows_enriched_with_cost']}")
    print(f"duplicate_rows_replaced: {stats['rows_replaced']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
