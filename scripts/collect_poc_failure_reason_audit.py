#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path
from typing import Any

try:
    from . import poc_failure_reason_common as poc_common
    from . import run_poc_failure_reason_audit as runner
    from . import trace_audit_common as trace_common
except ImportError:  # pragma: no cover - used when run as a script.
    import poc_failure_reason_common as poc_common
    import run_poc_failure_reason_audit as runner
    import trace_audit_common as trace_common


DEFAULT_PROMPT_PACK_ROOT = runner.DEFAULT_OUTPUT_DIR
DEFAULT_OUTPUT_DIR = runner.DEFAULT_OUTPUT_DIR
AUDIT_JSON_NAME = "audit.json"
AUDIT_CSV_NAME = "audit.csv"
NEEDS_REVIEW_CSV_NAME = "needs_review.csv"
REASON_COUNTS_CSV_NAME = "reason_counts.csv"

AUDIT_COLUMNS = [
    "sample_id",
    "trace_group",
    "project_name",
    "target_binary",
    "agent_type",
    "model",
    "poc_failure_category",
    "agent_stated_reason",
    "verified_outcome_reason",
    "confidence",
    "evidence_count",
    "evidence_summary",
    "pack_dir",
    "verdict_path",
]
NEEDS_REVIEW_COLUMNS = [
    "sample_id",
    "trace_group",
    "project_name",
    "target_binary",
    "agent_type",
    "model",
    "reason",
    "pack_dir",
    "verdict_path",
]
REASON_COUNT_COLUMNS = [
    "trace_group",
    "agent_type",
    "model",
    "poc_failure_category",
    "count",
]


def discover_pack_dirs(prompt_pack_root: Path) -> list[Path]:
    if prompt_pack_root.is_file():
        prompt_pack_root = prompt_pack_root.parent
    pack_dirs = {path.parent for path in prompt_pack_root.rglob(runner.EVIDENCE_FILE_NAME)}
    return sorted(pack_dirs, key=lambda path: str(path))


def load_json_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def load_pack_sample(pack_dir: Path) -> dict[str, Any]:
    evidence_path = pack_dir / runner.EVIDENCE_FILE_NAME
    try:
        evidence = trace_common.read_json(evidence_path)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(evidence, dict):
        return {}
    sample = evidence.get("sample")
    return sample if isinstance(sample, dict) else {}


def validate_verdict(verdict: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in runner.VERDICT_SCHEMA["required"]:
        if key not in verdict:
            errors.append(f"missing {key}")

    if verdict.get("poc_failure_category") not in poc_common.POC_FAILURE_CATEGORIES:
        errors.append("invalid poc_failure_category")
    if verdict.get("confidence") not in poc_common.CONFIDENCE_VALUES:
        errors.append("invalid confidence")
    for key in ("sample_id", "project_name", "agent_type", "model", "target_binary", "agent_stated_reason", "verified_outcome_reason"):
        if key in verdict and not isinstance(verdict.get(key), str):
            errors.append(f"{key} must be a string")

    evidence = verdict.get("evidence")
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
    else:
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"evidence[{index}] must be an object")
                continue
            for key in ("event_id", "snippet", "reason"):
                if not isinstance(item.get(key), str):
                    errors.append(f"evidence[{index}].{key} must be a string")
    return errors


def _sample_value(sample: dict[str, Any], verdict: dict[str, Any], key: str) -> str:
    value = verdict.get(key)
    if value is None or value == "":
        value = sample.get(key)
    return trace_common.clean(value)


def audit_row(pack_dir: Path, sample: dict[str, Any], verdict: dict[str, Any]) -> dict[str, str]:
    evidence = verdict.get("evidence") if isinstance(verdict.get("evidence"), list) else []
    evidence_summary = " | ".join(
        f"{trace_common.clean(item.get('event_id'))}: {trace_common.clean(item.get('reason'))}"
        for item in evidence
        if isinstance(item, dict)
    )
    return {
        "sample_id": _sample_value(sample, verdict, "sample_id"),
        "trace_group": trace_common.clean(sample.get("trace_group")),
        "project_name": _sample_value(sample, verdict, "project_name"),
        "target_binary": _sample_value(sample, verdict, "target_binary"),
        "agent_type": _sample_value(sample, verdict, "agent_type"),
        "model": _sample_value(sample, verdict, "model"),
        "poc_failure_category": trace_common.clean(verdict.get("poc_failure_category")),
        "agent_stated_reason": trace_common.clean(verdict.get("agent_stated_reason")),
        "verified_outcome_reason": trace_common.clean(verdict.get("verified_outcome_reason")),
        "confidence": trace_common.clean(verdict.get("confidence")),
        "evidence_count": str(len(evidence)),
        "evidence_summary": evidence_summary,
        "pack_dir": str(pack_dir),
        "verdict_path": str(pack_dir / runner.VERDICT_FILE_NAME),
    }


def needs_review_row(
    pack_dir: Path,
    sample: dict[str, Any],
    reason: str,
    verdict_path: Path,
) -> dict[str, str]:
    return {
        "sample_id": trace_common.clean(sample.get("sample_id")),
        "trace_group": trace_common.clean(sample.get("trace_group")),
        "project_name": trace_common.clean(sample.get("project_name")),
        "target_binary": trace_common.clean(sample.get("target_binary")),
        "agent_type": trace_common.clean(sample.get("agent_type")),
        "model": trace_common.clean(sample.get("model")),
        "reason": reason,
        "pack_dir": str(pack_dir),
        "verdict_path": str(verdict_path),
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _reason_count_rows(audit_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    counts: collections.Counter[tuple[str, str, str, str]] = collections.Counter()
    for row in audit_rows:
        counts[
            (
                row["trace_group"],
                row["agent_type"],
                row["model"],
                row["poc_failure_category"],
            )
        ] += 1
    return [
        {
            "trace_group": trace_group,
            "agent_type": agent_type,
            "model": model,
            "poc_failure_category": category,
            "count": str(count),
        }
        for (trace_group, agent_type, model, category), count in sorted(counts.items())
    ]


def collect(prompt_pack_root: Path, output_dir: Path) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_rows: list[dict[str, str]] = []
    audit_json: list[dict[str, Any]] = []
    needs_review_rows: list[dict[str, str]] = []

    pack_dirs = discover_pack_dirs(prompt_pack_root)
    for pack_dir in pack_dirs:
        sample = load_pack_sample(pack_dir)
        verdict_path = pack_dir / runner.VERDICT_FILE_NAME
        if not verdict_path.exists():
            needs_review_rows.append(needs_review_row(pack_dir, sample, "missing verdict.json", verdict_path))
            audit_json.append({"pack_dir": str(pack_dir), "status": "missing", "sample": sample})
            continue

        try:
            verdict = load_json_object(verdict_path)
        except Exception as error:
            needs_review_rows.append(needs_review_row(pack_dir, sample, f"malformed verdict: {error}", verdict_path))
            audit_json.append(
                {
                    "pack_dir": str(pack_dir),
                    "verdict_path": str(verdict_path),
                    "status": "malformed",
                    "sample": sample,
                    "error": str(error),
                }
            )
            continue

        errors = validate_verdict(verdict)
        if errors:
            reason = "; ".join(errors)
            needs_review_rows.append(needs_review_row(pack_dir, sample, reason, verdict_path))
            audit_json.append(
                {
                    "pack_dir": str(pack_dir),
                    "verdict_path": str(verdict_path),
                    "status": "invalid",
                    "sample": sample,
                    "verdict": verdict,
                    "errors": errors,
                }
            )
            continue

        row = audit_row(pack_dir, sample, verdict)
        audit_rows.append(row)
        status = "valid"
        review_reasons: list[str] = []
        if row["confidence"] == "low":
            review_reasons.append("low confidence")
        if row["poc_failure_category"] == "insufficient_evidence":
            review_reasons.append("insufficient evidence")
        if review_reasons:
            needs_review_rows.append(needs_review_row(pack_dir, sample, "; ".join(review_reasons), verdict_path))
            status = "valid_needs_review"

        audit_json.append(
            {
                "pack_dir": str(pack_dir),
                "verdict_path": str(verdict_path),
                "status": status,
                "sample": sample,
                "verdict": verdict,
            }
        )

    reason_rows = _reason_count_rows(audit_rows)
    _write_csv(output_dir / AUDIT_CSV_NAME, AUDIT_COLUMNS, audit_rows)
    trace_common.write_json(output_dir / AUDIT_JSON_NAME, audit_json)
    _write_csv(output_dir / NEEDS_REVIEW_CSV_NAME, NEEDS_REVIEW_COLUMNS, needs_review_rows)
    _write_csv(output_dir / REASON_COUNTS_CSV_NAME, REASON_COUNT_COLUMNS, reason_rows)

    return {
        "packs": len(pack_dirs),
        "valid": len(audit_rows),
        "needs_review": len(needs_review_rows),
        "reason_count_rows": len(reason_rows),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect PoC failure reason audit verdicts into CSV/JSON reports.")
    parser.add_argument("--prompt-pack-root", default=str(DEFAULT_PROMPT_PACK_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = collect(Path(args.prompt_pack_root).expanduser(), Path(args.output).expanduser())
    print(f"packs: {stats['packs']}")
    print(f"valid: {stats['valid']}")
    print(f"needs_review: {stats['needs_review']}")
    print(f"reason_count_rows: {stats['reason_count_rows']}")
    print(f"output: {Path(args.output).expanduser()}")
    if stats["packs"] == 0:
        print("error: no prompt packs found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
