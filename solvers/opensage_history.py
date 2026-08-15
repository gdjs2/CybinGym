from __future__ import annotations

import argparse
import collections
import datetime
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


OUTCOME_SOLVED = "solved"
OUTCOME_UNSOLVED = "unsolved"
OUTCOME_ERROR = "error"
OUTCOME_UNATTEMPTED = "unattempted"

FAILURE_LLM_BUDGET_EXHAUSTED = "llm_budget_exhausted"
FAILURE_SYSTEM_ERROR = "system_error"
FAILURE_TOOLING_ERROR = "tooling_error"
FAILURE_LLM_API_ERROR = "llm_api_error"
FAILURE_AGENT_CAPABILITY = "agent_capability_failure"
FAILURE_INCOMPLETE_OR_CANCELLED = "incomplete_or_cancelled"
FAILURE_UNKNOWN_ERROR = "unknown_error"

FAILURE_CATEGORIES = (
    FAILURE_LLM_BUDGET_EXHAUSTED,
    FAILURE_TOOLING_ERROR,
    FAILURE_LLM_API_ERROR,
    FAILURE_SYSTEM_ERROR,
    FAILURE_INCOMPLETE_OR_CANCELLED,
    FAILURE_AGENT_CAPABILITY,
    FAILURE_UNKNOWN_ERROR,
)

DEFAULT_OPENSAGE_OUTPUT_DIR = Path("evals/opensage_inspect")
DEFAULT_INSPECT_LOG_DIR = Path("logs")


_ERROR_LINE_RE = re.compile(r"\| ERROR\s+\|")
_OPENSAGE_OUTPUT_RE = re.compile(r"^OpenSAGE output:\s*(.+)$", re.MULTILINE)


@dataclass
class ScoreRecord:
    sample_id: str
    value: str
    log_path: str
    model: str | None = None
    provider: str | None = None
    run_dir: str | None = None
    completed: bool | None = None
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "value": self.value,
            "log_path": self.log_path,
            "model": self.model,
            "provider": self.provider,
            "run_dir": self.run_dir,
            "completed": self.completed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


@dataclass
class RunRecord:
    sample_id: str
    run_dir: str
    timestamp: str
    model: str | None = None
    provider: str | None = None
    status: str | None = None
    returncode: int | None = None
    poc_exists: bool | None = None
    result_poc_found: bool | None = None
    error_log_count: int = 0
    error_kinds: dict[str, int] = field(default_factory=dict)
    fatal_reasons: list[str] = field(default_factory=list)
    failure_category: str | None = None
    failure_reasons: list[str] = field(default_factory=list)
    llm_call_budget: dict[str, Any] = field(default_factory=dict)
    mcp_preflight: dict[str, Any] = field(default_factory=dict)
    mcp_runtime: dict[str, Any] = field(default_factory=dict)

    @property
    def has_poc(self) -> bool:
        values = [self.poc_exists, self.result_poc_found]
        return any(value is True for value in values)

    @property
    def clean(self) -> bool:
        return not self.fatal_reasons and self.error_log_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "run_dir": self.run_dir,
            "timestamp": self.timestamp,
            "model": self.model,
            "provider": self.provider,
            "status": self.status,
            "returncode": self.returncode,
            "poc_exists": self.poc_exists,
            "result_poc_found": self.result_poc_found,
            "error_log_count": self.error_log_count,
            "error_kinds": dict(self.error_kinds),
            "fatal_reasons": list(self.fatal_reasons),
            "failure_category": self.failure_category,
            "failure_reasons": list(self.failure_reasons),
            "llm_call_budget": dict(self.llm_call_budget),
            "mcp_preflight": dict(self.mcp_preflight),
            "mcp_runtime": dict(self.mcp_runtime),
            "clean": self.clean,
        }


@dataclass
class SampleHistory:
    sample_id: str
    scores: list[ScoreRecord] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    outcome: str = OUTCOME_UNATTEMPTED
    reasons: list[str] = field(default_factory=list)
    failure_category: str | None = None
    failure_reasons: list[str] = field(default_factory=list)

    @property
    def latest_run(self) -> RunRecord | None:
        if not self.runs:
            return None
        return sorted(self.runs, key=lambda run: run.timestamp)[-1]

    @property
    def latest_score(self) -> ScoreRecord | None:
        if not self.scores:
            return None
        return sorted(
            self.scores,
            key=lambda score: score.completed_at or score.started_at or "",
        )[-1]

    def as_dict(self, *, include_records: bool = False) -> dict[str, Any]:
        latest_run = self.latest_run
        latest_score = self.latest_score
        data: dict[str, Any] = {
            "sample_id": self.sample_id,
            "outcome": self.outcome,
            "reasons": list(self.reasons),
            "latest_score": latest_score.as_dict() if latest_score else None,
            "latest_run": latest_run.as_dict() if latest_run else None,
            "failure_category": self.failure_category,
            "failure_reasons": list(self.failure_reasons),
            "score_count": len(self.scores),
            "run_count": len(self.runs),
        }
        if include_records:
            data["scores"] = [score.as_dict() for score in self.scores]
            data["runs"] = [run.as_dict() for run in self.runs]
        return data


def parse_sample_ids(sample_ids: str | int | None) -> set[str] | None:
    if sample_ids is None:
        return None
    value = str(sample_ids).strip()
    if not value or value.lower() == "all":
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def load_dataset_ids(dataset_path: str | Path = "dataset.json") -> set[str]:
    path = Path(dataset_path)
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {str(row.get("id")) for row in rows if row.get("id") is not None}


def build_opensage_history(
    *,
    output_dir: str | Path = DEFAULT_OPENSAGE_OUTPUT_DIR,
    log_dir: str | Path = DEFAULT_INSPECT_LOG_DIR,
    sample_ids: set[str] | None = None,
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
) -> dict[str, SampleHistory]:
    histories: dict[str, SampleHistory] = {}
    for sample_id in sample_ids or set():
        histories[str(sample_id)] = SampleHistory(sample_id=str(sample_id))

    score_records = iter_inspect_scores(log_dir)
    score_records.extend(iter_opensage_score_files(output_dir))
    run_context_by_dir = _run_context_by_dir(score_records)

    for score in score_records:
        if sample_ids is not None and score.sample_id not in sample_ids:
            continue
        if not _matches_model(
            score.model,
            score.provider,
            model=model,
            provider=provider,
            include_unknown_model=include_unknown_model,
        ):
            continue
        histories.setdefault(score.sample_id, SampleHistory(score.sample_id)).scores.append(
            score
        )

    for run in iter_opensage_runs(output_dir):
        if not run.model:
            context = _lookup_run_context(run_context_by_dir, run.run_dir)
            if context:
                run.model = context.model
                run.provider = context.provider
        if sample_ids is not None and run.sample_id not in sample_ids:
            continue
        if not _matches_model(
            run.model,
            run.provider,
            model=model,
            provider=provider,
            include_unknown_model=include_unknown_model,
        ):
            continue
        histories.setdefault(run.sample_id, SampleHistory(run.sample_id)).runs.append(run)

    for history in histories.values():
        classify_sample_history(history)

    return dict(sorted(histories.items(), key=lambda item: _sample_sort_key(item[0])))


def discover_history_models(
    *,
    output_dir: str | Path = DEFAULT_OPENSAGE_OUTPUT_DIR,
    log_dir: str | Path = DEFAULT_INSPECT_LOG_DIR,
    sample_ids: set[str] | None = None,
    provider: str | None = None,
) -> list[str]:
    histories = build_opensage_history(
        output_dir=output_dir,
        log_dir=log_dir,
        sample_ids=sample_ids,
        provider=provider,
    )
    models: set[str] = set()
    for history in histories.values():
        for record in [*history.scores, *history.runs]:
            model = _clean_str(record.model)
            if model:
                models.add(model)
    return sorted(models, key=_model_sort_key)


def classify_sample_history(history: SampleHistory) -> None:
    history.failure_category = None
    history.failure_reasons = []
    score_values = [score.value for score in history.scores]
    clean_i_scores = bool(
        any(score.value == "I" for score in history.scores)
        and (any(run.clean and run.has_poc for run in history.runs) or not history.runs)
    )

    if "C" in score_values:
        history.outcome = OUTCOME_SOLVED
        history.reasons = ["at least one Inspect scorer result is C"]
        return

    latest_run = history.latest_run
    latest_score = history.latest_score

    if clean_i_scores:
        history.outcome = OUTCOME_UNSOLVED
        history.reasons = ["Inspect scorer result is I with a clean completed OpenSAGE run"]
        history.failure_category = FAILURE_AGENT_CAPABILITY
        history.failure_reasons = ["clean completed run did not satisfy the scorer"]
        if latest_run and not latest_run.failure_category:
            latest_run.failure_category = FAILURE_AGENT_CAPABILITY
            latest_run.failure_reasons = list(history.failure_reasons)
        return

    if history.scores or history.runs:
        history.outcome = OUTCOME_ERROR
        reasons: list[str] = []
        failure_reasons: list[str] = []
        if latest_score and latest_score.value:
            reasons.append(f"latest Inspect scorer result is {latest_score.value}")
        if latest_score and latest_score.error:
            reasons.append(f"Inspect sample error: {latest_score.error}")
            failure_reasons.append(f"Inspect sample error: {latest_score.error}")
        if latest_run:
            reasons.extend(latest_run.fatal_reasons)
            failure_reasons.extend(latest_run.failure_reasons)
            if latest_run.error_log_count:
                reasons.append(f"{latest_run.error_log_count} OpenSAGE ERROR log lines")
            if not latest_run.has_poc:
                reasons.append("no poc recorded in latest OpenSAGE run")
            history.failure_category = latest_run.failure_category
        if not history.failure_category:
            if latest_score and latest_score.error:
                history.failure_category = FAILURE_SYSTEM_ERROR
            elif latest_run:
                history.failure_category = FAILURE_UNKNOWN_ERROR
            else:
                history.failure_category = FAILURE_UNKNOWN_ERROR
        history.failure_reasons = _dedupe(failure_reasons) or [
            "attempted but no classified failure reason found"
        ]
        history.reasons = _dedupe(reasons) or ["attempted but no clean final result found"]
        return

    history.outcome = OUTCOME_UNATTEMPTED
    history.reasons = ["no Inspect score or OpenSAGE run found"]


def _normalize_failure_category(value: str | None) -> str | None:
    normalized = (value or "").strip().lower().replace("-", "_")
    if not normalized or normalized in {"all", "any", "*"}:
        return None
    if normalized not in FAILURE_CATEGORIES:
        raise ValueError(
            "Unsupported OpenSAGE failure category "
            f"{value!r}; use one of: {', '.join(FAILURE_CATEGORIES)}"
        )
    return normalized


def filter_histories(
    histories: dict[str, SampleHistory],
    mode: str,
    failure_category: str | None = None,
) -> set[str]:
    normalized = (mode or "").strip().lower().replace("_", "-")
    category = _normalize_failure_category(failure_category)
    if not normalized or normalized in {"all", "selected"}:
        selected = set(histories)
    elif normalized in {"error", "errors", "rerun-errors"}:
        selected = {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_ERROR
        }
    elif normalized in {"unresolved", "errors-or-unattempted"}:
        selected = {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome in {OUTCOME_ERROR, OUTCOME_UNATTEMPTED}
        }
    elif normalized in {"not-solved", "not-solved-or-unattempted"}:
        selected = {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome != OUTCOME_SOLVED
        }
    elif normalized in {"solved"}:
        selected = {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_SOLVED
        }
    elif normalized in {"unsolved", "clean-unsolved"}:
        selected = {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_UNSOLVED
        }
    else:
        raise ValueError(
            "Unsupported OpenSAGE history filter "
            f"{mode!r}; use selected, errors, unresolved, not-solved, solved, or unsolved"
        )

    if category:
        selected = {
            sample_id
            for sample_id in selected
            if histories[sample_id].failure_category == category
        }
    return selected


def iter_inspect_scores(log_dir: str | Path) -> list[ScoreRecord]:
    records: list[ScoreRecord] = []
    root = Path(log_dir)
    if not root.exists():
        return records

    _ensure_zipfile_zstd()
    for log_path in sorted(root.glob("*.eval")):
        try:
            with zipfile.ZipFile(log_path) as archive:
                start = _read_archive_json(archive, "_journal/start.json")
                model_name, provider_name = _eval_model_context(start)
                if "summaries.json" not in archive.namelist():
                    continue
                rows = json.loads(archive.read("summaries.json"))
                for row in rows:
                    sample_id = row.get("id")
                    if sample_id is None:
                        continue
                    sample_record = _read_archive_sample(archive, row)
                    sample_model = _sample_output_model(sample_record)
                    run_dir = _extract_run_dir_from_sample(sample_record)
                    scorer = (row.get("scores") or {}).get("cybingym_scorer") or {}
                    value = scorer.get("value")
                    records.append(
                        ScoreRecord(
                            sample_id=str(sample_id),
                            value=_normalize_score_value(value),
                            log_path=str(log_path),
                            model=model_name or sample_model,
                            provider=provider_name
                            or _infer_provider(model_name or sample_model),
                            run_dir=run_dir,
                            completed=row.get("completed"),
                            started_at=row.get("started_at"),
                            completed_at=row.get("completed_at"),
                            error=_row_error(row),
                        )
                    )
        except (OSError, zipfile.BadZipFile, NotImplementedError, json.JSONDecodeError):
            continue

    return records


def iter_opensage_score_files(output_dir: str | Path) -> list[ScoreRecord]:
    root = Path(output_dir)
    if not root.exists():
        return []

    records: list[ScoreRecord] = []
    for score_path in sorted(root.glob("*/*/inspect_score.json")):
        payload = _read_json(score_path)
        if not payload:
            continue
        sample_id = payload.get("sample_id") or score_path.parents[1].name
        value = payload.get("value") or payload.get("score")
        model_name = _clean_str(payload.get("model"))
        provider_name = _clean_str(payload.get("provider")) or _infer_provider(model_name)
        records.append(
            ScoreRecord(
                sample_id=str(sample_id),
                value=_normalize_score_value(value),
                log_path=str(score_path),
                model=model_name,
                provider=provider_name,
                run_dir=str(score_path.parent),
                completed=payload.get("completed"),
                started_at=payload.get("started_at"),
                completed_at=payload.get("completed_at") or payload.get("scored_at"),
                error=_clean_str(payload.get("error")),
            )
        )
    return records


def iter_opensage_runs(output_dir: str | Path) -> list[RunRecord]:
    root = Path(output_dir)
    if not root.exists():
        return []

    runs: list[RunRecord] = []
    for sample_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        sample_id = sample_dir.name
        for run_dir in sorted(path for path in sample_dir.iterdir() if path.is_dir()):
            if not _looks_like_run_dir(run_dir):
                continue
            runs.append(_read_run_record(sample_id, run_dir))
    return runs


def failure_counts_for_histories(
    histories: dict[str, SampleHistory],
) -> dict[str, int]:
    counts = collections.Counter(
        history.failure_category
        for history in histories.values()
        if history.outcome != OUTCOME_SOLVED and history.failure_category
    )
    return {category: counts[category] for category in FAILURE_CATEGORIES}


def _format_failure_counts(counts: dict[str, int]) -> str:
    shown = [f"{category}={counts.get(category, 0)}" for category in FAILURE_CATEGORIES]
    return " | ".join(shown)


def format_history_summary(
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
    failure_category: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
    limit: int = 80,
) -> str:
    counts = collections.Counter(history.outcome for history in histories.values())
    failure_counts = failure_counts_for_histories(histories)
    selected_ids = selected_ids or set()
    normalized_failure_category = _normalize_failure_category(failure_category)
    lines = [
        "OpenSAGE history summary",
        f"Mode: {mode or 'all'}",
        f"Failure category: {normalized_failure_category or 'all'}",
        f"Model: {model or 'all'}",
        f"Provider: {provider or 'all'}",
        f"Include unknown model records: {include_unknown_model}",
        f"Total tracked samples: {len(histories)}",
        (
            "Solved: {solved} | Clean unsolved: {unsolved} | "
            "Error/rerunnable: {error} | Unattempted: {unattempted}"
        ).format(
            solved=counts[OUTCOME_SOLVED],
            unsolved=counts[OUTCOME_UNSOLVED],
            error=counts[OUTCOME_ERROR],
            unattempted=counts[OUTCOME_UNATTEMPTED],
        ),
        f"Failure categories: {_format_failure_counts(failure_counts)}",
    ]
    if selected_ids:
        lines.append(f"Selected for this filter: {len(selected_ids)}")

    for outcome, label in [
        (OUTCOME_SOLVED, "Solved"),
        (OUTCOME_UNSOLVED, "Clean unsolved"),
        (OUTCOME_ERROR, "Error/rerunnable"),
        (OUTCOME_UNATTEMPTED, "Unattempted"),
    ]:
        ids = [
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == outcome
        ]
        lines.append(f"{label} IDs: {_format_ids(ids, limit=limit)}")

    error_histories = [
        history
        for history in histories.values()
        if history.outcome == OUTCOME_ERROR
    ][:limit]
    if error_histories:
        lines.append("")
        lines.append("Error details:")
        for history in error_histories:
            latest_run = history.latest_run
            suffix = f" ({latest_run.run_dir})" if latest_run else ""
            category = history.failure_category or FAILURE_UNKNOWN_ERROR
            detail = "; ".join((history.failure_reasons or history.reasons)[:4])
            lines.append(f"- {history.sample_id} [{category}]: {detail}{suffix}")

    return "\n".join(lines)


def history_summary_dict(
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
    failure_category: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
    include_records: bool = False,
) -> dict[str, Any]:
    counts = collections.Counter(history.outcome for history in histories.values())
    normalized_failure_category = _normalize_failure_category(failure_category)
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "mode": mode or "all",
        "failure_category": normalized_failure_category,
        "model": model,
        "provider": provider,
        "include_unknown_model": include_unknown_model,
        "counts": {
            OUTCOME_SOLVED: counts[OUTCOME_SOLVED],
            OUTCOME_UNSOLVED: counts[OUTCOME_UNSOLVED],
            OUTCOME_ERROR: counts[OUTCOME_ERROR],
            OUTCOME_UNATTEMPTED: counts[OUTCOME_UNATTEMPTED],
            "total": len(histories),
        },
        "failure_counts": failure_counts_for_histories(histories),
        "selected_ids": sorted(selected_ids or set(), key=_sample_sort_key),
        "samples": {
            sample_id: history.as_dict(include_records=include_records)
            for sample_id, history in histories.items()
        },
    }


def build_per_model_history_summary(
    *,
    output_dir: str | Path = DEFAULT_OPENSAGE_OUTPUT_DIR,
    log_dir: str | Path = DEFAULT_INSPECT_LOG_DIR,
    sample_ids: set[str] | None = None,
    mode: str = "all",
    failure_category: str | None = None,
    provider: str | None = None,
    include_records: bool = False,
) -> dict[str, Any]:
    model_names = discover_history_models(
        output_dir=output_dir,
        log_dir=log_dir,
        sample_ids=sample_ids,
        provider=provider,
    )
    summaries: dict[str, Any] = {}
    selected_total = 0

    for model_name in model_names:
        histories = build_opensage_history(
            output_dir=output_dir,
            log_dir=log_dir,
            sample_ids=sample_ids,
            model=model_name,
            provider=provider,
        )
        selected_ids = filter_histories(
            histories,
            mode,
            failure_category=failure_category,
        )
        selected_total += len(selected_ids)
        summaries[model_name] = history_summary_dict(
            histories,
            selected_ids=selected_ids,
            mode=mode,
            failure_category=failure_category,
            model=model_name,
            provider=provider,
            include_records=include_records,
        )

    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "mode": mode or "all",
        "failure_category": _normalize_failure_category(failure_category),
        "provider": provider,
        "model_count": len(model_names),
        "selected_total": selected_total,
        "models": summaries,
    }


def format_per_model_history_summary(
    summary: dict[str, Any],
    *,
    limit: int = 80,
) -> str:
    models = summary.get("models") if isinstance(summary, dict) else {}
    if not isinstance(models, dict):
        models = {}

    lines = [
        "OpenSAGE per-model history summary",
        f"Mode: {summary.get('mode') or 'all'}",
        f"Failure category: {summary.get('failure_category') or 'all'}",
        f"Provider: {summary.get('provider') or 'all'}",
        f"Models: {len(models)}",
        f"Selected for this filter across models: {summary.get('selected_total', 0)}",
    ]
    if not models:
        lines.append("No model-scoped history records found.")
        return "\n".join(lines)

    for model_name in sorted(models, key=_model_sort_key):
        model_summary = models[model_name]
        counts = model_summary.get("counts", {})
        selected_ids = model_summary.get("selected_ids", [])
        samples = model_summary.get("samples", {})
        lines.extend(
            [
                "",
                f"Model: {model_name}",
                f"Total tracked samples: {counts.get('total', 0)}",
                (
                    "Solved: {solved} | Clean unsolved: {unsolved} | "
                    "Error/rerunnable: {error} | Unattempted: {unattempted}"
                ).format(
                    solved=counts.get(OUTCOME_SOLVED, 0),
                    unsolved=counts.get(OUTCOME_UNSOLVED, 0),
                    error=counts.get(OUTCOME_ERROR, 0),
                    unattempted=counts.get(OUTCOME_UNATTEMPTED, 0),
                ),
                (
                    "Failure categories: "
                    f"{_format_failure_counts(model_summary.get('failure_counts', {}))}"
                ),
                f"Selected for this filter: {len(selected_ids)}",
            ]
        )

        for outcome, label in [
            (OUTCOME_SOLVED, "Solved"),
            (OUTCOME_UNSOLVED, "Clean unsolved"),
            (OUTCOME_ERROR, "Error/rerunnable"),
            (OUTCOME_UNATTEMPTED, "Unattempted"),
        ]:
            ids = [
                str(sample_id)
                for sample_id, history in samples.items()
                if isinstance(history, dict) and history.get("outcome") == outcome
            ]
            lines.append(f"{label} IDs: {_format_ids(ids, limit=limit)}")

    return "\n".join(lines)


def write_per_model_history_summary(
    path: str | Path,
    summary: dict[str, Any],
) -> None:
    summary_path = Path(path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.suffix.lower() == ".json":
        text = json.dumps(summary, indent=2) + "\n"
    else:
        text = format_per_model_history_summary(summary) + "\n"
    tmp_path = summary_path.with_name(f".{summary_path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(summary_path)


def write_history_summary(
    path: str | Path,
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
    failure_category: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
) -> None:
    summary_path = Path(path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.suffix.lower() == ".json":
        text = (
            json.dumps(
                history_summary_dict(
                    histories,
                    selected_ids=selected_ids,
                    mode=mode,
                    failure_category=failure_category,
                    model=model,
                    provider=provider,
                    include_unknown_model=include_unknown_model,
                ),
                indent=2,
            )
            + "\n"
        )
    else:
        text = (
            format_history_summary(
                histories,
                selected_ids=selected_ids,
                mode=mode,
                failure_category=failure_category,
                model=model,
                provider=provider,
                include_unknown_model=include_unknown_model,
            )
            + "\n"
        )
    tmp_path = summary_path.with_name(f".{summary_path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(summary_path)


def _read_archive_json(
    archive: zipfile.ZipFile,
    name: str,
) -> dict[str, Any]:
    if name not in archive.namelist():
        return {}
    try:
        value = json.loads(archive.read(name))
    except (OSError, KeyError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _eval_model_context(start: dict[str, Any]) -> tuple[str | None, str | None]:
    eval_info = start.get("eval") if isinstance(start, dict) else {}
    if not isinstance(eval_info, dict):
        return None, None
    task_args = eval_info.get("task_args") or {}
    if not isinstance(task_args, dict):
        task_args = {}
    model_name = _clean_str(task_args.get("opensage_model")) or _clean_str(
        eval_info.get("model")
    )
    provider_name = _clean_str(task_args.get("opensage_provider")) or _infer_provider(
        model_name
    )
    return model_name, provider_name


def _read_archive_sample(
    archive: zipfile.ZipFile,
    row: dict[str, Any],
) -> dict[str, Any]:
    sample_id = row.get("id")
    epoch = row.get("epoch", 1)
    if sample_id is None:
        return {}
    return _read_archive_json(archive, f"samples/{sample_id}_epoch_{epoch}.json")


def _sample_output_model(sample: dict[str, Any]) -> str | None:
    output = sample.get("output") if isinstance(sample, dict) else {}
    if not isinstance(output, dict):
        return None
    return _clean_str(output.get("model"))


def _extract_run_dir_from_sample(sample: dict[str, Any]) -> str | None:
    messages = sample.get("messages") if isinstance(sample, dict) else []
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        else:
            text = str(content or "")
        match = _OPENSAGE_OUTPUT_RE.search(text)
        if match:
            return match.group(1).strip()
    return None


def _run_context_by_dir(
    scores: list[ScoreRecord],
) -> dict[str, ScoreRecord]:
    contexts: dict[str, ScoreRecord] = {}
    for score in scores:
        if not score.run_dir or not score.model:
            continue
        for key in _path_keys(score.run_dir):
            contexts[key] = score
    return contexts


def _lookup_run_context(
    contexts: dict[str, ScoreRecord],
    run_dir: str,
) -> ScoreRecord | None:
    for key in _path_keys(run_dir):
        context = contexts.get(key)
        if context:
            return context
    return None


def _path_keys(path: str | Path) -> set[str]:
    value = Path(path)
    keys = {str(value)}
    try:
        keys.add(str(value.resolve()))
    except OSError:
        pass
    return keys


def _matches_model(
    record_model: str | None,
    record_provider: str | None,
    *,
    model: str | None,
    provider: str | None,
    include_unknown_model: bool,
) -> bool:
    model = _clean_str(model)
    provider = _clean_str(provider)
    if not model and not provider:
        return True
    record_model = _clean_str(record_model)
    record_provider = _clean_str(record_provider)
    if model:
        if not record_model:
            return include_unknown_model
        if record_model != model:
            return False
    if provider:
        if not record_provider:
            return include_unknown_model
        if record_provider != provider:
            return False
    return True


def _infer_provider(model_name: str | None) -> str | None:
    model_name = _clean_str(model_name)
    if not model_name:
        return None
    if "/" in model_name:
        return model_name.split("/", 1)[0]
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_score_value(value: Any) -> str:
    if isinstance(value, dict):
        values = {_clean_str(item) for item in value.values()}
        if "C" in values:
            return "C"
        if "I" in values:
            return "I"
        return ""
    text = _clean_str(value)
    return text or ""


def _read_first_json(paths: list[Path]) -> dict[str, Any]:
    for path in paths:
        value = _read_json(path)
        if value:
            return value
    return {}


def _read_first_cost_info(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    candidates: list[Path] = []
    task_output_dir = _clean_str(result.get("task_output_dir"))
    if task_output_dir:
        candidates.append(Path(task_output_dir) / "cost_info.json")
    candidates.extend(sorted(run_dir.glob("*/cost_info.json")))
    return _read_first_json(candidates)


def _read_mcp_preflight(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("mcp_preflight") if isinstance(result, dict) else None
    if isinstance(value, dict) and value:
        return value
    return _read_first_json(sorted(run_dir.glob("*/mcp_preflight.json")))


def _read_mcp_runtime(run_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("mcp_runtime") if isinstance(result, dict) else None
    if isinstance(value, dict) and value:
        return value
    return _read_first_json(sorted(run_dir.glob("*/mcp_runtime_failures.json")))


def _extract_llm_call_budget(
    result: dict[str, Any],
    cost_info: dict[str, Any],
) -> dict[str, Any]:
    for source in (result.get("llm_call_budget"), cost_info.get("llm_call_budget")):
        if isinstance(source, dict) and source:
            return dict(source)

    budget = cost_info.get("budget") if isinstance(cost_info, dict) else None
    if isinstance(budget, dict):
        extracted: dict[str, Any] = {}
        for key in ("exhausted", "exceeded", "exhausted_reason", "budget_exhausted"):
            if key in budget:
                extracted[key] = budget[key]
        per_model_usage = budget.get("per_model_usage")
        if isinstance(per_model_usage, dict):
            calls = 0
            for usage in per_model_usage.values():
                if not isinstance(usage, dict):
                    continue
                try:
                    calls += int(usage.get("calls") or 0)
                except (TypeError, ValueError):
                    pass
            if calls:
                extracted["completed_llm_calls"] = calls
        if extracted:
            return extracted
    return {}


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _llm_budget_exhausted(llm_call_budget: dict[str, Any]) -> bool:
    if not isinstance(llm_call_budget, dict) or not llm_call_budget:
        return False
    exhausted_reason = _clean_str(llm_call_budget.get("exhausted_reason")) or ""
    if exhausted_reason == "llm_call_budget_exhausted":
        return True
    if llm_call_budget.get("exhausted") is True or llm_call_budget.get("exceeded") is True:
        return True
    configured = _int_value(llm_call_budget.get("configured_max_llm_calls"))
    if configured <= 0:
        return False
    used = max(
        _int_value(llm_call_budget.get("started_llm_calls")),
        _int_value(llm_call_budget.get("completed_llm_calls")),
    )
    return used >= configured


def _llm_budget_reason(llm_call_budget: dict[str, Any]) -> str:
    configured = _int_value(llm_call_budget.get("configured_max_llm_calls"))
    started = _int_value(llm_call_budget.get("started_llm_calls"))
    completed = _int_value(llm_call_budget.get("completed_llm_calls"))
    reason = _clean_str(llm_call_budget.get("exhausted_reason")) or ""
    parts = ["LLM call budget exhausted"]
    if configured:
        parts.append(f"configured={configured}")
    if started:
        parts.append(f"started={started}")
    if completed:
        parts.append(f"completed={completed}")
    if reason:
        parts.append(f"reason={reason}")
    return ", ".join(parts)


def _mcp_failure_reasons(
    *,
    mcp_preflight: dict[str, Any],
    mcp_runtime: dict[str, Any],
    error_kinds: dict[str, int],
) -> list[str]:
    reasons: list[str] = []
    if isinstance(mcp_preflight, dict) and mcp_preflight:
        if mcp_preflight.get("ok") is False:
            failed = mcp_preflight.get("failed_checks") or []
            if not failed and isinstance(mcp_preflight.get("checks"), dict):
                failed = [
                    name
                    for name, check in mcp_preflight["checks"].items()
                    if not (isinstance(check, dict) and check.get("ok") is True)
                ]
            detail = f": {failed}" if failed else ""
            reasons.append(f"MCP preflight failed{detail}")
    if isinstance(mcp_runtime, dict) and mcp_runtime:
        fatal = mcp_runtime.get("fatal") is True
        ok = mcp_runtime.get("ok") is False
        classification = _clean_str(mcp_runtime.get("classification")) or ""
        if fatal or classification == "fatal_required_mcp_failure" or ok:
            total = mcp_runtime.get("total")
            suffix = f" ({total} occurrence(s))" if total else ""
            reasons.append(f"MCP runtime failure{suffix}")
    for key in ("mcp_list_tools", "mcp_sse", "sandbox_create"):
        if error_kinds.get(key):
            reasons.append(f"OpenSAGE ERROR log kind {key}: {error_kinds[key]}")
    return _dedupe(reasons)


def _llm_api_failure_reasons(error_kinds: dict[str, int], fatal_reasons: list[str]) -> list[str]:
    reasons: list[str] = []
    if error_kinds.get("litellm_api"):
        reasons.append(f"LLM/API error log lines: {error_kinds['litellm_api']}")
    for reason in fatal_reasons:
        lowered = reason.lower()
        if any(token in lowered for token in ("litellm", "openai", "api", "rate limit")):
            reasons.append(reason)
    return _dedupe(reasons)


def _system_failure_reasons(
    *,
    status: Any,
    returncode: Any,
    error_jsons: list[Path],
    error_kinds: dict[str, int],
    fatal_reasons: list[str],
) -> list[str]:
    reasons: list[str] = []
    if status and status not in {"finished", "cancelled", "cancelled_artifacts_imported"}:
        reasons.append(f"OpenSAGE status is {status}")
    if returncode not in (None, 0):
        reasons.append(f"OpenSAGE returncode is {returncode}")
    if error_jsons:
        reasons.append(f"OpenSAGE task error file exists: {error_jsons[0]}")
    for key in ("dispatcher_turn", "task_failed", "asyncio_task", "bash_completion_watcher"):
        if error_kinds.get(key):
            reasons.append(f"OpenSAGE ERROR log kind {key}: {error_kinds[key]}")
    for reason in fatal_reasons:
        if reason == "OpenSAGE did not produce poc":
            continue
        if reason.startswith("OpenSAGE failed samples:"):
            continue
        if reason not in reasons:
            reasons.append(reason)
    return _dedupe(reasons)


def _incomplete_failure_reasons(*, status: Any, bridge: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if status in {"cancelled", "cancelled_artifacts_imported"}:
        reasons.append(f"OpenSAGE status is {status}")
    if bridge.get("outer_timeout"):
        reasons.append("OpenSAGE bridge hit outer timeout")
    return reasons


def _classify_run_failure(
    *,
    bridge: dict[str, Any],
    result: dict[str, Any],
    llm_call_budget: dict[str, Any],
    mcp_preflight: dict[str, Any],
    mcp_runtime: dict[str, Any],
    error_kinds: dict[str, int],
    fatal_reasons: list[str],
    error_jsons: list[Path],
    has_poc: bool,
) -> tuple[str | None, list[str]]:
    status = bridge.get("status") if isinstance(bridge, dict) else None
    returncode = bridge.get("returncode") if isinstance(bridge, dict) else None

    if _llm_budget_exhausted(llm_call_budget):
        return FAILURE_LLM_BUDGET_EXHAUSTED, [_llm_budget_reason(llm_call_budget)]

    mcp_reasons = _mcp_failure_reasons(
        mcp_preflight=mcp_preflight,
        mcp_runtime=mcp_runtime,
        error_kinds=error_kinds,
    )
    if mcp_reasons:
        return FAILURE_TOOLING_ERROR, mcp_reasons

    api_reasons = _llm_api_failure_reasons(error_kinds, fatal_reasons)
    if api_reasons:
        return FAILURE_LLM_API_ERROR, api_reasons

    incomplete_reasons = _incomplete_failure_reasons(status=status, bridge=bridge)
    if incomplete_reasons:
        return FAILURE_INCOMPLETE_OR_CANCELLED, incomplete_reasons

    system_reasons = _system_failure_reasons(
        status=status,
        returncode=returncode,
        error_jsons=error_jsons,
        error_kinds=error_kinds,
        fatal_reasons=fatal_reasons,
    )
    if system_reasons:
        return FAILURE_SYSTEM_ERROR, system_reasons

    failed = result.get("failed") if isinstance(result, dict) else None
    if failed:
        return FAILURE_AGENT_CAPABILITY, [f"OpenSAGE failed samples: {failed}"]
    if not has_poc:
        return FAILURE_AGENT_CAPABILITY, ["OpenSAGE did not produce poc"]
    if fatal_reasons or error_kinds:
        return FAILURE_UNKNOWN_ERROR, fatal_reasons or ["unclassified OpenSAGE ERROR log lines"]
    return None, []


def _read_run_record(sample_id: str, run_dir: Path) -> RunRecord:
    bridge = _read_json(run_dir / "opensage_bridge_status.json")
    result = _read_json(run_dir / "cybingym_result.json")
    error_count, error_kinds = _scan_error_log(run_dir / "evaluation_master.log")
    fatal_reasons: list[str] = []

    status = bridge.get("status") if isinstance(bridge, dict) else None
    if status and status != "finished":
        fatal_reasons.append(f"OpenSAGE status is {status}")

    returncode = bridge.get("returncode") if isinstance(bridge, dict) else None
    if returncode not in (None, 0):
        fatal_reasons.append(f"OpenSAGE returncode is {returncode}")

    if bridge.get("outer_timeout"):
        fatal_reasons.append("OpenSAGE bridge hit outer timeout")

    poc_exists = bridge.get("poc_exists") if isinstance(bridge, dict) else None
    result_poc_found = result.get("poc_found") if isinstance(result, dict) else None
    if poc_exists is False or result_poc_found is False:
        fatal_reasons.append("OpenSAGE did not produce poc")

    failed = result.get("failed") if isinstance(result, dict) else None
    if failed:
        fatal_reasons.append(f"OpenSAGE failed samples: {failed}")

    error_jsons = sorted(run_dir.glob("cybingym_*/error.json"))
    if error_jsons:
        fatal_reasons.append(f"OpenSAGE task error file exists: {error_jsons[0]}")

    cost_info = _read_first_cost_info(run_dir, result)
    llm_call_budget = _extract_llm_call_budget(result, cost_info)
    mcp_preflight = _read_mcp_preflight(run_dir, result)
    mcp_runtime = _read_mcp_runtime(run_dir, result)
    deduped_fatal_reasons = _dedupe(fatal_reasons)
    has_poc = any(value is True for value in (poc_exists, result_poc_found))
    failure_category, failure_reasons = _classify_run_failure(
        bridge=bridge,
        result=result,
        llm_call_budget=llm_call_budget,
        mcp_preflight=mcp_preflight,
        mcp_runtime=mcp_runtime,
        error_kinds=error_kinds,
        fatal_reasons=deduped_fatal_reasons,
        error_jsons=error_jsons,
        has_poc=has_poc,
    )

    return RunRecord(
        sample_id=sample_id,
        run_dir=str(run_dir),
        timestamp=run_dir.name,
        model=_clean_str(bridge.get("model")),
        provider=_clean_str(bridge.get("provider"))
        or _infer_provider(_clean_str(bridge.get("model"))),
        status=str(status) if status else None,
        returncode=int(returncode) if isinstance(returncode, int) else None,
        poc_exists=poc_exists if isinstance(poc_exists, bool) else None,
        result_poc_found=result_poc_found if isinstance(result_poc_found, bool) else None,
        error_log_count=error_count,
        error_kinds=error_kinds,
        fatal_reasons=deduped_fatal_reasons,
        failure_category=failure_category,
        failure_reasons=failure_reasons,
        llm_call_budget=llm_call_budget,
        mcp_preflight=mcp_preflight,
        mcp_runtime=mcp_runtime,
    )


def _scan_error_log(path: Path) -> tuple[int, dict[str, int]]:
    if not path.exists():
        return 0, {}
    counts: collections.Counter[str] = collections.Counter()
    total = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if not _ERROR_LINE_RE.search(line):
                    continue
                total += 1
                counts[_error_kind(line)] += 1
    except OSError:
        return 0, {}
    return total, dict(counts)


def _error_kind(line: str) -> str:
    if "Failed to get tools from MCP server" in line:
        return "mcp_list_tools"
    if "mcp.client.sse" in line:
        return "mcp_sse"
    if "Dispatcher turn" in line:
        return "dispatcher_turn"
    if "api.openai.com" in line or "LiteLLM" in line:
        return "litellm_api"
    if "Failed to create sandbox" in line:
        return "sandbox_create"
    if "wait_for_subagent failed" in line:
        return "subagent_wait"
    if "Task exception was never retrieved" in line:
        return "asyncio_task"
    if "failed with exception" in line:
        return "task_failed"
    if "Completion watcher failed" in line:
        return "bash_completion_watcher"
    return "other"


def _row_error(row: dict[str, Any]) -> str | None:
    for key in ("error", "exception"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _looks_like_run_dir(path: Path) -> bool:
    return any(
        (path / name).exists()
        for name in (
            "opensage_bridge_status.json",
            "cybingym_result.json",
            "evaluation_master.log",
        )
    )


def _ensure_zipfile_zstd() -> None:
    try:
        import zipfile_zstd  # noqa: F401
    except Exception:
        pass


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _format_ids(ids: list[str], *, limit: int) -> str:
    ordered = sorted(ids, key=_sample_sort_key)
    if not ordered:
        return "-"
    shown = ordered[:limit]
    suffix = "" if len(ordered) <= limit else f" ... (+{len(ordered) - limit})"
    return ",".join(shown) + suffix


def _sample_sort_key(sample_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(sample_id))
    except ValueError:
        return (1, sample_id)


def _model_sort_key(model: str) -> tuple[int, str]:
    provider, has_provider, name = model.partition("/")
    if has_provider:
        return (0, provider, name)
    return (1, model, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize OpenSAGE CyBinGym history.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OPENSAGE_OUTPUT_DIR))
    parser.add_argument("--log-dir", default=str(DEFAULT_INSPECT_LOG_DIR))
    parser.add_argument("--dataset", default="dataset.json")
    parser.add_argument("--sample-ids", default="all")
    parser.add_argument(
        "--filter",
        default="all",
        help="all, errors, unresolved, not-solved, solved, or unsolved",
    )
    parser.add_argument(
        "--failure-category",
        default="",
        help=(
            "Only select histories whose classified failure category matches this "
            "value. Use one of: " + ", ".join(FAILURE_CATEGORIES)
        ),
    )
    parser.add_argument(
        "--model",
        default="",
        help="Only include history records for this OpenSAGE/Inspect model.",
    )
    parser.add_argument(
        "--provider",
        default="",
        help="Only include history records for this provider.",
    )
    parser.add_argument(
        "--include-unknown-model",
        action="store_true",
        help="Include records that do not have model metadata when filtering by model/provider.",
    )
    parser.add_argument("--write", default="", help="Write text or JSON summary to path")
    parser.add_argument(
        "--include-records",
        action="store_true",
        help="When writing JSON, include all score/run records.",
    )
    parser.add_argument(
        "--per-model",
        action="store_true",
        help="Print/write separate history summaries for every model found.",
    )
    args = parser.parse_args(argv)
    try:
        failure_category = _normalize_failure_category(args.failure_category)
    except ValueError as exc:
        parser.error(str(exc))

    if args.per_model and args.model:
        parser.error("--per-model cannot be combined with --model")
    if args.per_model and args.include_unknown_model:
        parser.error("--per-model cannot be combined with --include-unknown-model")

    sample_ids = parse_sample_ids(args.sample_ids)
    if sample_ids is None and args.dataset:
        dataset_path = Path(args.dataset)
        if dataset_path.exists():
            sample_ids = load_dataset_ids(dataset_path)

    if args.per_model:
        summary = build_per_model_history_summary(
            output_dir=args.output_dir,
            log_dir=args.log_dir,
            sample_ids=sample_ids,
            mode=args.filter,
            failure_category=failure_category,
            provider=args.provider or None,
            include_records=args.include_records,
        )
        text = format_per_model_history_summary(summary)
        print(text)
        if args.write:
            write_per_model_history_summary(args.write, summary)
        return 0

    histories = build_opensage_history(
        output_dir=args.output_dir,
        log_dir=args.log_dir,
        sample_ids=sample_ids,
        model=args.model or None,
        provider=args.provider or None,
        include_unknown_model=args.include_unknown_model,
    )
    selected_ids = filter_histories(
        histories,
        args.filter,
        failure_category=failure_category,
    )
    text = format_history_summary(
        histories,
        selected_ids=selected_ids,
        mode=args.filter,
        failure_category=failure_category,
        model=args.model or None,
        provider=args.provider or None,
        include_unknown_model=args.include_unknown_model,
    )
    print(text)

    if args.write:
        write_path = Path(args.write)
        write_path.parent.mkdir(parents=True, exist_ok=True)
        if write_path.suffix.lower() == ".json":
            write_path.write_text(
                json.dumps(
                    history_summary_dict(
                        histories,
                        selected_ids=selected_ids,
                        mode=args.filter,
                        failure_category=failure_category,
                        model=args.model or None,
                        provider=args.provider or None,
                        include_unknown_model=args.include_unknown_model,
                        include_records=args.include_records,
                    ),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        else:
            write_path.write_text(text + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
