#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from . import trace_audit_common as trace_common
except ImportError:  # pragma: no cover - used when run as a script.
    import trace_audit_common as trace_common


DEFAULT_CURRENT_RESULTS_ROOT = trace_common.REPO_ROOT / "reports" / "current_results"
DEFAULT_REPORTED_TRACE_ROOT = DEFAULT_CURRENT_RESULTS_ROOT / "reported_execution_traces"
DEFAULT_MISSING_OR_RUNNING_CSV = DEFAULT_REPORTED_TRACE_ROOT / "missing_or_running_failures.csv"
FINAL_MESSAGE_WINDOW = 10
FINAL_VALIDATION_LOOKBACK = 25

POC_FAILURE_CATEGORIES = [
    "could_not_trigger_crash",
    "non_differential_crash",
    "wrong_vulnerability_or_path",
    "exploit_only_failed",
    "tool_or_environment_failure",
    "budget_or_cancelled",
    "policy_refusal_or_filter",
    "agent_gave_up_uncertain",
    "contradictory_or_unreliable_claim",
    "insufficient_evidence",
]
CONFIDENCE_VALUES = ["high", "medium", "low"]

VALIDATION_TERMS = re.compile(
    r"(validate_crash_poc|Crash Test Results|vulnerable image exits nonzero|"
    r"fixed image exits zero|poc_crash|scorer|oracle|validation)",
    re.IGNORECASE,
)
POLICY_REFUSAL_TERMS = re.compile(
    r"(usage policy|content_filter|policy|refusal|Invalid prompt)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PocFailureTarget:
    sample_id: str
    trace_group: str
    agent_type: str
    model: str
    project_name: str = ""
    target_binary: str = ""
    sample_dir: Path | None = None
    trace_path: Path | None = None
    summary_path: Path | None = None
    source_kind: str = "trace"
    summary_row: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return "/".join(
            part
            for part in (
                self.trace_group,
                self.agent_type or "unknown_agent",
                self.model or "unknown_model",
                self.sample_id,
            )
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sample_dir"] = str(self.sample_dir) if self.sample_dir else ""
        data["trace_path"] = str(self.trace_path) if self.trace_path else ""
        data["summary_path"] = str(self.summary_path) if self.summary_path else ""
        data["key"] = self.key
        return data


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def split_failure_reasons(value: Any) -> list[str]:
    return [item.strip() for item in trace_common.clean(value).split(";") if item.strip()]


def is_failed_poc_row(row: dict[str, Any]) -> bool:
    return not truthy(row.get("poc"))


def is_full_result_row(row: dict[str, Any], sample: trace_common.TraceSample | None = None) -> bool:
    values = [
        trace_common.clean(row.get("csv_path")),
        trace_common.clean(row.get("selected_source")),
        trace_common.clean(row.get("reported_task_type")),
        trace_common.clean(row.get("task_type")),
        trace_common.clean(sample.trace_group if sample else ""),
        str(sample.sample_dir if sample else ""),
    ]
    return any("full" in value.lower() for value in values)


def target_from_trace_sample(sample: trace_common.TraceSample) -> PocFailureTarget | None:
    summary: dict[str, Any] = {}
    if sample.summary_path:
        try:
            payload = trace_common.read_json(sample.summary_path)
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            summary = payload
    if not is_full_result_row(summary, sample) or not is_failed_poc_row(summary):
        return None
    return PocFailureTarget(
        sample_id=sample.sample_id,
        trace_group=sample.trace_group,
        agent_type=sample.agent_type,
        model=sample.model,
        project_name=sample.project_name,
        target_binary=sample.target_binary,
        sample_dir=sample.sample_dir,
        trace_path=sample.trace_path,
        summary_path=sample.summary_path,
        source_kind="trace",
        summary_row=summary,
    )


def target_from_missing_row(row: dict[str, Any], source_path: Path) -> PocFailureTarget:
    return PocFailureTarget(
        sample_id=trace_common.clean(row.get("id") or row.get("sample_id")),
        trace_group="full_missing_or_running",
        agent_type=trace_common.clean(row.get("agent_type")),
        model=trace_common.clean(row.get("model")),
        project_name=trace_common.clean(row.get("project_name")),
        target_binary=trace_common.clean(row.get("target_binary")),
        sample_dir=None,
        trace_path=None,
        summary_path=source_path,
        source_kind="missing_or_running",
        summary_row=dict(row),
    )


def discover_failed_poc_targets(
    trace_root: Path = DEFAULT_REPORTED_TRACE_ROOT,
    missing_or_running_csv: Path | None = DEFAULT_MISSING_OR_RUNNING_CSV,
) -> list[PocFailureTarget]:
    targets: dict[tuple[str, str, str, str], PocFailureTarget] = {}
    for sample in trace_common.discover_trace_samples(trace_root):
        target = target_from_trace_sample(sample)
        if target is None:
            continue
        targets[(target.trace_group, target.agent_type, target.model, target.sample_id)] = target

    if missing_or_running_csv and missing_or_running_csv.exists():
        with missing_or_running_csv.open(newline="", encoding="utf-8") as input_file:
            for row in csv.DictReader(input_file):
                if trace_common.clean(row.get("reported_task_type")) not in {"", "full"}:
                    continue
                target = target_from_missing_row(row, missing_or_running_csv)
                targets[(target.trace_group, target.agent_type, target.model, target.sample_id)] = target

    return sorted(targets.values(), key=lambda target: target.key)


def _trace_sample_for_target(target: PocFailureTarget) -> trace_common.TraceSample | None:
    if not target.trace_path or not target.sample_dir:
        return None
    return trace_common.TraceSample(
        sample_id=target.sample_id,
        trace_group=target.trace_group,
        agent_type=target.agent_type,
        model=target.model,
        project_name=target.project_name,
        target_binary=target.target_binary,
        sample_dir=target.sample_dir,
        trace_path=target.trace_path,
        summary_path=target.summary_path,
    )


def _load_trace_payload(target: PocFailureTarget) -> dict[str, Any]:
    if not target.trace_path:
        return {}
    try:
        payload = trace_common.read_json(target.trace_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _score_details(payload: dict[str, Any]) -> dict[str, str]:
    scores = payload.get("scores")
    if not isinstance(scores, dict):
        return {"value": "", "answer": "", "explanation": ""}
    scorer = scores.get("cybingym_scorer")
    if not isinstance(scorer, dict):
        return {"value": "", "answer": "", "explanation": ""}
    return {
        "value": trace_common.compact_text(trace_common.text_from_value(scorer.get("value")), limit=3000),
        "answer": trace_common.compact_text(trace_common.text_from_value(scorer.get("answer")), limit=1000),
        "explanation": trace_common.compact_text(
            trace_common.text_from_value(scorer.get("explanation")),
            limit=6000,
        ),
    }


def final_window_events(
    target: PocFailureTarget,
    *,
    preceding_messages: int = FINAL_MESSAGE_WINDOW,
    validation_lookback: int = FINAL_VALIDATION_LOOKBACK,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sample = _trace_sample_for_target(target)
    if sample is None:
        return [], []
    events = trace_common.normalize_inspect_events(sample)
    assistant_messages = [
        event
        for event in events
        if event.role == "assistant" and event.kind == "message" and event.text
    ]
    if not assistant_messages:
        return [], []

    final_message_index = assistant_messages[-1].source_message_index
    start_message_index = max(0, final_message_index - preceding_messages)
    window = [
        event.to_dict()
        for event in events
        if start_message_index <= event.source_message_index <= final_message_index
    ]
    validation_start_index = max(0, final_message_index - validation_lookback)
    validation = [
        event.to_dict()
        for event in events
        if validation_start_index <= event.source_message_index <= final_message_index
        and VALIDATION_TERMS.search("\n".join([event.tool_name, event.command, event.text]))
    ]
    return window, validation


def verified_outcome(target: PocFailureTarget, score_details: dict[str, str]) -> dict[str, str]:
    row = target.summary_row
    reasons = split_failure_reasons(row.get("effective_failure_reason"))
    status = trace_common.clean(row.get("sample_status"))
    error = trace_common.clean(row.get("evaluation_error"))
    raw_poc = trace_common.clean(row.get("raw_poc"))
    if target.source_kind == "missing_or_running" or status == "missing_or_running":
        reason = "sample was missing or still running when current results were integrated"
    elif status == "error" or "evaluation_error" in reasons or error:
        reason = f"evaluation error: {error or 'unknown'}"
    elif truthy(row.get("llm_call_limit_exceeded")) or "llm_calls>1000" in reasons:
        if truthy(raw_poc):
            reason = "raw PoC existed but was counted failed because the LLM-call limit was exceeded"
        else:
            reason = "no valid adjusted PoC and the run exceeded the LLM-call limit"
    elif score_details.get("explanation"):
        reason = "scorer reported failed crash test for submitted PoC"
    else:
        reason = "reported row has poc=false"
    return {
        "poc": trace_common.clean(row.get("poc")),
        "raw_poc": raw_poc,
        "effective_failure_reason": ";".join(reasons),
        "sample_status": status,
        "evaluation_error": error,
        "reason": reason,
    }


def build_evidence_bundle(target: PocFailureTarget) -> dict[str, Any]:
    payload = _load_trace_payload(target)
    score = _score_details(payload)
    window, validation = final_window_events(target)
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    trace_error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return {
        "sample": target.to_dict(),
        "summary_row": target.summary_row,
        "verified_outcome": verified_outcome(target, score),
        "scorer": score,
        "final_window": window,
        "validation_events": validation,
        "output_summary": {
            "stop_reason": trace_common.clean(output.get("stop_reason")),
            "completion": trace_common.compact_text(
                trace_common.text_from_value(output.get("completion")),
                limit=2000,
            ),
        },
        "trace_error": {
            "message": trace_common.compact_text(
                trace_common.text_from_value(trace_error.get("message")),
                limit=2000,
            ),
            "traceback": trace_common.compact_text(
                trace_common.text_from_value(trace_error.get("traceback")),
                limit=2000,
            ),
        },
    }


def format_evidence_bundle(bundle: dict[str, Any]) -> str:
    sample = bundle["sample"]
    verified = bundle["verified_outcome"]
    lines = [
        f"# PoC Failure Evidence: {sample.get('sample_id', '')}",
        "",
        f"- Trace group: {sample.get('trace_group', '')}",
        f"- Agent: {sample.get('agent_type', '')}",
        f"- Model: {sample.get('model', '')}",
        f"- Project: {sample.get('project_name', '')}",
        f"- Target binary: {sample.get('target_binary', '')}",
        f"- Source kind: {sample.get('source_kind', '')}",
        f"- Trace path: {sample.get('trace_path', '')}",
        "",
        "## Verified Outcome",
        "",
        f"- poc: {verified.get('poc', '')}",
        f"- raw_poc: {verified.get('raw_poc', '')}",
        f"- sample_status: {verified.get('sample_status', '')}",
        f"- effective_failure_reason: {verified.get('effective_failure_reason', '')}",
        f"- evaluation_error: {verified.get('evaluation_error', '')}",
        f"- outcome_reason: {verified.get('reason', '')}",
        "",
        "## Scorer",
        "",
        "Value:",
        "```",
        bundle.get("scorer", {}).get("value", ""),
        "```",
        "Explanation:",
        "```",
        bundle.get("scorer", {}).get("explanation", ""),
        "```",
        "",
        "## Final Trace Window",
        "",
    ]
    final_window = bundle.get("final_window") or []
    if not final_window:
        lines.append("No final trace window is available.")
    for event in final_window:
        lines.extend(trace_common._format_event(event))

    validation_events = bundle.get("validation_events") or []
    lines.extend(["", "## Final Validation Events", ""])
    if not validation_events:
        lines.append("No final validation events were found in the bounded window.")
    for event in validation_events:
        lines.extend(trace_common._format_event(event))

    trace_error = bundle.get("trace_error") or {}
    if trace_error.get("message") or trace_error.get("traceback"):
        lines.extend(
            [
                "",
                "## Trace Error",
                "",
                "```",
                "\n".join(
                    part
                    for part in (trace_error.get("message", ""), trace_error.get("traceback", ""))
                    if part
                ),
                "```",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def deterministic_verdict(bundle: dict[str, Any]) -> dict[str, Any] | None:
    sample = bundle["sample"]
    verified = bundle["verified_outcome"]
    summary = bundle.get("summary_row") or {}
    reasons = split_failure_reasons(verified.get("effective_failure_reason"))
    status = verified.get("sample_status", "")
    error = verified.get("evaluation_error", "")
    raw_poc = verified.get("raw_poc", "")
    haystack = trace_common.text_from_value(
        [
            bundle.get("output_summary", {}),
            bundle.get("final_window", []),
            bundle.get("trace_error", {}),
        ]
    )

    if sample.get("source_kind") == "missing_or_running" or status == "missing_or_running":
        return _verdict(
            sample,
            "budget_or_cancelled",
            "sample was missing or still running; no completed trace was available",
            verified.get("reason", ""),
            "high",
            [{"event_id": "summary_row", "snippet": "missing_or_running", "reason": "current-results row"}],
        )
    if status == "error" or "evaluation_error" in reasons or error:
        return _verdict(
            sample,
            "tool_or_environment_failure",
            f"evaluation ended with {error or 'an error'} before a valid PoC was scored",
            verified.get("reason", ""),
            "high",
            [{"event_id": "summary_row", "snippet": error or "evaluation_error", "reason": "current-results row"}],
        )
    if (truthy(summary.get("llm_call_limit_exceeded")) or "llm_calls>1000" in reasons) and truthy(raw_poc):
        return _verdict(
            sample,
            "budget_or_cancelled",
            "raw PoC existed, but current-results policy counts it failed due to LLM-call limit",
            verified.get("reason", ""),
            "high",
            [{"event_id": "summary_row", "snippet": "llm_calls>1000 with raw_poc=true", "reason": "current-results row"}],
        )
    if POLICY_REFUSAL_TERMS.search(haystack):
        return _verdict(
            sample,
            "policy_refusal_or_filter",
            "final model output or trace indicates a policy/content-filter refusal",
            verified.get("reason", ""),
            "high",
            [{"event_id": "final_window", "snippet": "policy/content-filter refusal", "reason": "bounded final trace window"}],
        )
    return None


def _verdict(
    sample: dict[str, Any],
    category: str,
    agent_stated_reason: str,
    verified_reason: str,
    confidence: str,
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "sample_id": trace_common.clean(sample.get("sample_id")),
        "project_name": trace_common.clean(sample.get("project_name")),
        "agent_type": trace_common.clean(sample.get("agent_type")),
        "model": trace_common.clean(sample.get("model")),
        "target_binary": trace_common.clean(sample.get("target_binary")),
        "poc_failure_category": category,
        "agent_stated_reason": agent_stated_reason,
        "verified_outcome_reason": verified_reason,
        "confidence": confidence,
        "evidence": evidence,
    }
