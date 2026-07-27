from __future__ import annotations

import os
import shutil
import sys
import tempfile

from pathlib import Path
from typing import Any

from scorer import cybingym_scorer
from solvers.opensage_history import (
    build_opensage_history,
    filter_histories,
    format_history_summary,
    history_summary_dict,
    write_history_summary,
)

from inspect_ai import Task, task
from inspect_ai.dataset import Sample, json_dataset
from inspect_ai.util import (
    ComposeBuild,
    ComposeConfig,
    ComposeService,
    SandboxEnvironmentSpec,
)

MAXIMUM_ATTEMPTS = 1
OPENSAGE_SMOKE_SAMPLE_IDS = "10013,10055,10096"
CYBINGYM_DIR = Path(__file__).resolve().parent
DEFAULT_OPENSAGE_AGENT_DIR = str(CYBINGYM_DIR / "solvers" / "ctf_agent")
DEFAULT_OPENSAGE_SOURCE_DIR = os.environ.get(
    "OPENSAGE_SOURCE_DIR",
    str(CYBINGYM_DIR.parent / "opensage-adk-dev"),
)
def _default_opensage_python() -> str:
    configured = os.environ.get("OPENSAGE_PYTHON")
    if configured:
        return configured
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / "bin" / "python"
        if candidate.exists():
            return str(candidate)
    return sys.executable


DEFAULT_OPENSAGE_PYTHON = _default_opensage_python()


def _parse_sample_ids(sample_ids: str | int) -> set[str] | None:
    sample_ids = str(sample_ids).strip()
    if not sample_ids or sample_ids.lower() == "all":
        return None
    return {item.strip() for item in sample_ids.split(",") if item.strip()}


def _clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _infer_provider(model_name: str) -> str:
    model_name = _clean_str(model_name)
    if "/" not in model_name:
        return ""
    return model_name.split("/", 1)[0]


def _current_inspect_eval_model_arg() -> str:
    env_model = _clean_str(os.environ.get("INSPECT_EVAL_MODEL"))
    if env_model:
        return env_model
    for index, arg in enumerate(sys.argv):
        if arg == "--model" and index + 1 < len(sys.argv):
            return _clean_str(sys.argv[index + 1])
        if arg.startswith("--model="):
            return _clean_str(arg.split("=", 1)[1])
    return ""


def _resolve_history_model(*, opensage_history_model: str, opensage_model: str) -> str:
    requested = _clean_str(opensage_history_model) or "same"
    if requested.lower() in {"all", "any", "*", "none"}:
        return ""
    if requested.lower() in {"same", "current"}:
        return _clean_str(opensage_model) or _current_inspect_eval_model_arg()
    return requested


def _select_solver(
    *,
    agent_type: str,
    opensage_agent_dir: str,
    opensage_output_dir: str,
    opensage_max_llm_calls: int,
    opensage_max_workers: int,
    opensage_timeout: int,
    opensage_cleanup_grace: int,
    opensage_llm_retry_timeout: int,
    opensage_llm_retry_count: int,
    opensage_python: str,
    opensage_source_dir: str,
    opensage_model: str,
    opensage_provider: str,
    opensage_base_port: int,
    opensage_port_stride: int,
):
    if agent_type == "openai":
        from solvers.openai_agent import openai_agent

        return openai_agent()
    if agent_type == "claude_code":
        from solvers.swe_agents import claude_code_solver

        return claude_code_solver()
    if agent_type == "codex":
        from solvers.swe_agents import codex_cli_solver

        return codex_cli_solver()
    if agent_type == "opensage":
        from solvers.opensage_agent import opensage_solver

        return opensage_solver(
            opensage_agent_dir=opensage_agent_dir,
            output_dir=opensage_output_dir,
            max_llm_calls=opensage_max_llm_calls,
            max_workers=opensage_max_workers,
            timeout=opensage_timeout,
            cleanup_grace=opensage_cleanup_grace,
            llm_retry_timeout=opensage_llm_retry_timeout,
            llm_retry_count=opensage_llm_retry_count,
            opensage_python=opensage_python,
            opensage_source_dir=opensage_source_dir,
            opensage_model=opensage_model,
            opensage_provider=opensage_provider,
            base_port=opensage_base_port,
            port_stride=opensage_port_stride,
        )
    from solvers.react import react_solver

    return react_solver(attempts=MAXIMUM_ATTEMPTS)


def create_binary_sample(
    prompt: str,
    prebuilt_base_image: str,
    *,
    sample_id: str | int | None = None,
    target: str | list[str] = "",
    metadata: dict[str, Any] | None = None,
    files: dict[str, str] | None = None,
) -> Sample:
    build_context = Path(tempfile.mkdtemp(prefix="cybingym-agent-env-"))
    source_context = CYBINGYM_DIR / "agent_env"
    shutil.copytree(source_context, build_context, dirs_exist_ok=True)

    dockerfile_default = build_context / "Dockerfile.default"
    dockerfile_default.write_text(
        dockerfile_default.read_text().replace(
            "COPY --from=${TARGET_BASE_IMAGE} /out-vul /out-vul",
            f"COPY --from={prebuilt_base_image} /out-vul /out-vul",
            1,
        ).replace(
            "COPY --from=${TARGET_BASE_IMAGE} /out-fix /out-fix",
            f"COPY --from={prebuilt_base_image} /out-fix /out-fix",
            1,
        )
    )
    dockerfile_target = build_context / "Dockerfile.target"
    dockerfile_target.write_text(
        dockerfile_target.read_text().replace(
            "FROM ${BASE_IMAGE}",
            f"FROM {prebuilt_base_image}",
            1,
        )
    )

    config = ComposeConfig(
        services={
            "default": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.default",
                ),
                init=True,
                command="tail -f /dev/null",
            ),
            "target": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.target",
                ),
                init=True,
                command="tail -f /dev/null",
            ),
        }
    )

    return Sample(
        id=sample_id,
        input=prompt,
        target=target,
        metadata=metadata,
        sandbox=SandboxEnvironmentSpec(type="docker", config=config),
        files=files,
    )


def create_poc_handoff_sample(
    prompt: str,
    *,
    sample_id: str | int | None = None,
    target: str | list[str] = "",
    metadata: dict[str, Any] | None = None,
) -> Sample:
    config = ComposeConfig(
        services={
            "default": ComposeService(
                image="ubuntu:20.04",
                init=True,
                network_mode="none",
                command="tail -f /dev/null",
            )
        }
    )

    return Sample(
        id=sample_id,
        input=prompt,
        target=target,
        metadata=metadata,
        sandbox=SandboxEnvironmentSpec(type="docker", config=config),
    )


@task
def cybingym(
    agent_type: str = "basic",
    opensage_agent_dir: str = DEFAULT_OPENSAGE_AGENT_DIR,
    opensage_output_dir: str = "",
    opensage_max_llm_calls: int = 0,
    opensage_max_workers: int = 10,
    max_workers: int | None = None,
    opensage_timeout: int = 7200,
    opensage_cleanup_grace: int = 900,
    opensage_llm_retry_timeout: int = 600,
    opensage_llm_retry_count: int = 5,
    opensage_python: str = DEFAULT_OPENSAGE_PYTHON,
    opensage_source_dir: str = DEFAULT_OPENSAGE_SOURCE_DIR,
    opensage_model: str = "",
    opensage_provider: str = "",
    opensage_sample_ids: str = OPENSAGE_SMOKE_SAMPLE_IDS,
    opensage_base_port: int = 20000,
    opensage_port_stride: int = 10,
    opensage_history_filter: str = "",
    opensage_rerun_errors_only: bool = False,
    opensage_history_dir: str = "",
    opensage_history_log_dir: str = "logs",
    opensage_history_summary_path: str = "",
    opensage_history_model: str = "same",
    opensage_history_provider: str = "",
    opensage_history_include_unknown_model: bool = False,
):
    def build_sample(record: dict[str, Any]) -> Sample:
        metadata = record.get("metadata") or {}
        analysis_image = metadata.get("analysis_image")
        if not analysis_image:
            raise ValueError("Dataset record is missing metadata.analysis_image")

        if agent_type == "opensage":
            return create_poc_handoff_sample(
                prompt=record["input"],
                sample_id=record.get("id"),
                target=record.get("target", ""),
                metadata=metadata,
            )

        return create_binary_sample(
            prompt=record["input"],
            prebuilt_base_image=analysis_image,
            sample_id=record.get("id"),
            target=record.get("target", ""),
            metadata=metadata,
            files=record.get("files"),
        )

    dataset = json_dataset("dataset.json", sample_fields=build_sample)
    task_metadata: dict[str, Any] = {}
    history_filter = ""
    history_dir = Path(opensage_history_dir or opensage_output_dir or "evals/opensage_inspect")
    history_summary_sample_ids: set[str] | None = None
    history_model = ""
    history_provider = ""

    if agent_type == "opensage":
        selected_ids = _parse_sample_ids(opensage_sample_ids)
        if selected_ids is not None:
            dataset = dataset.filter(lambda sample: str(sample.id) in selected_ids)
            if len(dataset) == 0:
                raise ValueError(
                    f"No CyBinGym samples matched opensage_sample_ids={opensage_sample_ids!r}"
                )

        history_filter = "errors" if opensage_rerun_errors_only else opensage_history_filter
        history_summary_sample_ids = {str(sample.id) for sample in dataset}
        history_model_request = _clean_str(opensage_history_model) or "same"
        history_model_scope_all = history_model_request.lower() in {"all", "any", "*", "none"}
        history_model = _resolve_history_model(
            opensage_history_model=opensage_history_model,
            opensage_model=opensage_model,
        )
        history_provider = (
            _clean_str(opensage_history_provider)
            or _clean_str(opensage_provider)
            or _infer_provider(history_model)
        )
        if (
            (history_filter or opensage_history_summary_path)
            and not history_model
            and not history_model_scope_all
        ):
            raise ValueError(
                "OpenSAGE history merge/rerun needs a model scope to avoid mixing "
                "LLMs. Pass -T opensage_history_model=<model>, set "
                "-T opensage_model=<model>, set INSPECT_EVAL_MODEL, or use "
                "-T opensage_history_model=all to intentionally merge all models."
            )

        if history_filter:
            candidate_ids = history_summary_sample_ids
            histories = build_opensage_history(
                output_dir=history_dir,
                log_dir=Path(opensage_history_log_dir),
                sample_ids=candidate_ids,
                model=history_model or None,
                provider=history_provider or None,
                include_unknown_model=opensage_history_include_unknown_model,
            )
            filtered_ids = filter_histories(histories, history_filter)
            dataset = dataset.filter(lambda sample: str(sample.id) in filtered_ids)
            summary = format_history_summary(
                histories,
                selected_ids=filtered_ids,
                mode=history_filter,
                model=history_model or None,
                provider=history_provider or None,
                include_unknown_model=opensage_history_include_unknown_model,
            )
            print(summary)
            if opensage_history_summary_path:
                write_history_summary(
                    opensage_history_summary_path,
                    histories,
                    selected_ids=filtered_ids,
                    mode=history_filter,
                    model=history_model or None,
                    provider=history_provider or None,
                    include_unknown_model=opensage_history_include_unknown_model,
                )
            task_metadata["opensage_history"] = history_summary_dict(
                histories,
                selected_ids=filtered_ids,
                mode=history_filter,
                model=history_model or None,
                provider=history_provider or None,
                include_unknown_model=opensage_history_include_unknown_model,
            )
            if len(dataset) == 0:
                raise ValueError(
                    "No CyBinGym samples matched "
                    f"opensage_history_filter={history_filter!r}. "
                    "Use opensage_history_filter=unresolved to include never-run "
                    "samples, or omit the filter to run the selected set."
                )

    return Task(
        dataset=dataset,
        solver=_select_solver(
            agent_type=agent_type,
            opensage_agent_dir=opensage_agent_dir,
            opensage_output_dir=opensage_output_dir,
            opensage_max_llm_calls=opensage_max_llm_calls,
            opensage_max_workers=(opensage_max_workers if max_workers is None else max_workers),
            opensage_timeout=opensage_timeout,
            opensage_cleanup_grace=opensage_cleanup_grace,
            opensage_llm_retry_timeout=opensage_llm_retry_timeout,
            opensage_llm_retry_count=opensage_llm_retry_count,
            opensage_python=opensage_python,
            opensage_source_dir=opensage_source_dir,
            opensage_model=opensage_model,
            opensage_provider=opensage_provider,
            opensage_base_port=opensage_base_port,
            opensage_port_stride=opensage_port_stride,
        ),
        scorer=cybingym_scorer(
            opensage_model=history_model or opensage_model,
            opensage_provider=history_provider or opensage_provider,
            opensage_history_summary_path=opensage_history_summary_path,
            opensage_history_dir=str(history_dir),
            opensage_history_log_dir=opensage_history_log_dir,
            opensage_history_sample_ids=(
                sorted(history_summary_sample_ids)
                if history_summary_sample_ids is not None
                else None
            ),
            opensage_history_filter=history_filter or "all",
            opensage_history_model=history_model,
            opensage_history_provider=history_provider,
            opensage_history_include_unknown_model=opensage_history_include_unknown_model,
        ),
        metadata=task_metadata or None,
        fail_on_error=False,
    )
