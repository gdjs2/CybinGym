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
            "clean": self.clean,
        }


@dataclass
class SampleHistory:
    sample_id: str
    scores: list[ScoreRecord] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    outcome: str = OUTCOME_UNATTEMPTED
    reasons: list[str] = field(default_factory=list)

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


def classify_sample_history(history: SampleHistory) -> None:
    score_values = [score.value for score in history.scores]
    clean_i_scores = bool(
        any(score.value == "I" for score in history.scores)
        and (any(run.clean and run.has_poc for run in history.runs) or not history.runs)
    )

    if "C" in score_values:
        history.outcome = OUTCOME_SOLVED
        history.reasons = ["at least one Inspect scorer result is C"]
        return

    if clean_i_scores:
        history.outcome = OUTCOME_UNSOLVED
        history.reasons = ["Inspect scorer result is I with a clean completed OpenSAGE run"]
        return

    if history.scores or history.runs:
        history.outcome = OUTCOME_ERROR
        reasons: list[str] = []
        latest_run = history.latest_run
        latest_score = history.latest_score
        if latest_score and latest_score.value:
            reasons.append(f"latest Inspect scorer result is {latest_score.value}")
        if latest_score and latest_score.error:
            reasons.append(f"Inspect sample error: {latest_score.error}")
        if latest_run:
            reasons.extend(latest_run.fatal_reasons)
            if latest_run.error_log_count:
                reasons.append(f"{latest_run.error_log_count} OpenSAGE ERROR log lines")
            if not latest_run.has_poc:
                reasons.append("no poc recorded in latest OpenSAGE run")
        history.reasons = _dedupe(reasons) or ["attempted but no clean final result found"]
        return

    history.outcome = OUTCOME_UNATTEMPTED
    history.reasons = ["no Inspect score or OpenSAGE run found"]


def filter_histories(
    histories: dict[str, SampleHistory],
    mode: str,
) -> set[str]:
    normalized = (mode or "").strip().lower().replace("_", "-")
    if not normalized or normalized in {"all", "selected"}:
        return set(histories)
    if normalized in {"error", "errors", "rerun-errors"}:
        return {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_ERROR
        }
    if normalized in {"unresolved", "errors-or-unattempted"}:
        return {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome in {OUTCOME_ERROR, OUTCOME_UNATTEMPTED}
        }
    if normalized in {"not-solved", "not-solved-or-unattempted"}:
        return {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome != OUTCOME_SOLVED
        }
    if normalized in {"solved"}:
        return {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_SOLVED
        }
    if normalized in {"unsolved", "clean-unsolved"}:
        return {
            sample_id
            for sample_id, history in histories.items()
            if history.outcome == OUTCOME_UNSOLVED
        }
    raise ValueError(
        "Unsupported OpenSAGE history filter "
        f"{mode!r}; use selected, errors, unresolved, not-solved, solved, or unsolved"
    )


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


def format_history_summary(
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
    limit: int = 80,
) -> str:
    counts = collections.Counter(history.outcome for history in histories.values())
    selected_ids = selected_ids or set()
    lines = [
        "OpenSAGE history summary",
        f"Mode: {mode or 'all'}",
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
            lines.append(
                f"- {history.sample_id}: {'; '.join(history.reasons[:4])}{suffix}"
            )

    return "\n".join(lines)


def history_summary_dict(
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
    model: str | None = None,
    provider: str | None = None,
    include_unknown_model: bool = False,
    include_records: bool = False,
) -> dict[str, Any]:
    counts = collections.Counter(history.outcome for history in histories.values())
    return {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "mode": mode or "all",
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
        "selected_ids": sorted(selected_ids or set(), key=_sample_sort_key),
        "samples": {
            sample_id: history.as_dict(include_records=include_records)
            for sample_id, history in histories.items()
        },
    }


def write_history_summary(
    path: str | Path,
    histories: dict[str, SampleHistory],
    *,
    selected_ids: set[str] | None = None,
    mode: str = "all",
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
        fatal_reasons=_dedupe(fatal_reasons),
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
    args = parser.parse_args(argv)

    sample_ids = parse_sample_ids(args.sample_ids)
    if sample_ids is None and args.dataset:
        dataset_path = Path(args.dataset)
        if dataset_path.exists():
            sample_ids = load_dataset_ids(dataset_path)

    histories = build_opensage_history(
        output_dir=args.output_dir,
        log_dir=args.log_dir,
        sample_ids=sample_ids,
        model=args.model or None,
        provider=args.provider or None,
        include_unknown_model=args.include_unknown_model,
    )
    selected_ids = filter_histories(histories, args.filter)
    text = format_history_summary(
        histories,
        selected_ids=selected_ids,
        mode=args.filter,
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
