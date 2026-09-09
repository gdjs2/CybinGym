from __future__ import annotations

import os
import secrets
import shutil
import sys
import tempfile

from pathlib import Path
from typing import Any

from memory_limits import DEFAULT_SAMPLE_MEMORY_MB, VALIDATION_MEMORY_KEY, sample_memory_limits
from model_costs import register_frontier_model_costs
from scorer import cybingym_crash_scorer, cybingym_scorer
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
from pydantic import ValidationError

MAXIMUM_ATTEMPTS = 1
EVALUATION_LEVELS = {"crash", "full"}
CRASH_ONLY_AGENT_TYPES = {"claude_code", "codex", "kimi_code"}
CLI_AGENT_TYPES = CRASH_ONLY_AGENT_TYPES
OPENSAGE_SMOKE_SAMPLE_IDS = "10013,10055,10096"
CYBINGYM_DIR = Path(__file__).resolve().parent
DEFAULT_OPENSAGE_AGENT_DIR = str(CYBINGYM_DIR / "solvers" / "ctf_agent")
DEFAULT_OPENSAGE_SOURCE_DIR = os.environ.get(
    "OPENSAGE_SOURCE_DIR",
    str(CYBINGYM_DIR.parent / "opensage-adk-dev"),
)


def _coerce_legacy_compose_mem_limits(config: dict[str, Any]) -> dict[str, Any]:
    services = config.get("services")
    if not isinstance(services, dict):
        return config

    updated_services: dict[str, Any] | None = None
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        mem_limit = service.get("mem_limit")
        if isinstance(mem_limit, int) and not isinstance(mem_limit, bool):
            if updated_services is None:
                updated_services = dict(services)
            updated_service = dict(service)
            updated_service["mem_limit"] = str(mem_limit)
            updated_services[name] = updated_service

    if updated_services is None:
        return config

    updated_config = dict(config)
    updated_config["services"] = updated_services
    return updated_config


def _install_legacy_inspect_docker_config_deserializer() -> None:
    from inspect_ai.util._sandbox.docker.docker import DockerSandboxEnvironment

    if "config_deserialize" in DockerSandboxEnvironment.__dict__:
        return

    original_config_deserialize = DockerSandboxEnvironment.config_deserialize

    def config_deserialize(
        _cls: type[DockerSandboxEnvironment], config: dict[str, Any]
    ) -> ComposeConfig:
        try:
            return ComposeConfig.model_validate(
                _coerce_legacy_compose_mem_limits(config)
            )
        except ValidationError:
            return original_config_deserialize(config)

    DockerSandboxEnvironment.config_deserialize = classmethod(config_deserialize)


_install_legacy_inspect_docker_config_deserializer()


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


def _normalize_evaluation_level(evaluation_level: str) -> str:
    normalized = (evaluation_level or "full").strip().lower()
    if normalized not in EVALUATION_LEVELS:
        raise ValueError(
            "evaluation_level must be one of "
            f"{sorted(EVALUATION_LEVELS)}, got {evaluation_level!r}"
        )
    return normalized


def _sample_files_for_vulnerability_description(
    files: dict[str, str] | None,
    *,
    include_vulnerability_description: bool,
) -> dict[str, str] | None:
    if include_vulnerability_description or not files:
        return files
    filtered = {name: path for name, path in files.items() if name != "desc.txt"}
    return filtered or None


def _analysis_image_for_patched_binary(
    record: dict[str, Any],
    *,
    include_patched_binary: bool,
) -> str:
    metadata = record.get("metadata") or {}
    analysis_image = metadata.get("analysis_image")

    if include_patched_binary:
        if not analysis_image:
            raise ValueError("Dataset record is missing metadata.analysis_image")
        return analysis_image

    sample_id = _clean_str(record.get("id"))
    if not sample_id:
        raise ValueError(
            "include_patched_binary=False requires a dataset record id "
            "to derive the vulnerable-only analysis image"
        )
    return f"lambangaw/cybingym:{sample_id}-vul"


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
    kimi_code_version: str,
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
    opensage_reasoning_effort: str,
    opensage_artifact_collection_mode: str,
    opensage_extend_from_run_dir: str,
    opensage_base_port: int,
    opensage_port_stride: int,
    evaluation_level: str,
    include_vulnerability_description: bool,
    include_patched_binary: bool,
):
    if agent_type == "openai":
        from solvers.openai_agent import openai_agent

        return openai_agent()
    if agent_type == "claude_code":
        from solvers.swe_agents import claude_code_solver

        return claude_code_solver(
            evaluation_level=evaluation_level,
            include_vulnerability_description=include_vulnerability_description,
            include_patched_binary=include_patched_binary,
        )
    if agent_type == "codex":
        from solvers.swe_agents import codex_cli_solver

        return codex_cli_solver(
            evaluation_level=evaluation_level,
            include_vulnerability_description=include_vulnerability_description,
            include_patched_binary=include_patched_binary,
        )
    if agent_type == "kimi_code":
        from solvers.swe_agents import kimi_code_solver

        return kimi_code_solver(
            version=kimi_code_version,
            evaluation_level=evaluation_level,
            include_vulnerability_description=include_vulnerability_description,
            include_patched_binary=include_patched_binary,
        )
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
            opensage_reasoning_effort=opensage_reasoning_effort,
            artifact_collection_mode=opensage_artifact_collection_mode,
            extend_from_run_dir=opensage_extend_from_run_dir,
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
    evaluation_level: str = "full",
    include_patched_binary: bool = True,
    sample_memory_mb: int = DEFAULT_SAMPLE_MEMORY_MB,
) -> Sample:
    evaluation_level = _normalize_evaluation_level(evaluation_level)
    memory_limits = sample_memory_limits(sample_memory_mb)
    metadata = {**(metadata or {}), VALIDATION_MEMORY_KEY: memory_limits["validation"]}
    build_context = Path(tempfile.mkdtemp(prefix="cybingym-agent-env-"))
    source_context = CYBINGYM_DIR / "agent_env"
    shutil.copytree(source_context, build_context, dirs_exist_ok=True)

    dockerfile_default = build_context / "Dockerfile.default"
    dockerfile_default_text = dockerfile_default.read_text().replace(
        "COPY --from=${TARGET_BASE_IMAGE} /out-vul /out-vul",
        f"COPY --from={prebuilt_base_image} /out-vul /out-vul",
        1,
    )
    if include_patched_binary:
        dockerfile_default_text = dockerfile_default_text.replace(
            "COPY --from=${TARGET_BASE_IMAGE} /out-fix /out-fix",
            f"COPY --from={prebuilt_base_image} /out-fix /out-fix",
            1,
        )
    else:
        dockerfile_default_text = dockerfile_default_text.replace(
            "COPY --from=${TARGET_BASE_IMAGE} /out-fix /out-fix\n",
            "",
            1,
        )
    dockerfile_default.write_text(dockerfile_default_text)
    dockerfile_target = build_context / "Dockerfile.target"
    dockerfile_target.write_text(
        dockerfile_target.read_text().replace(
            "FROM ${BASE_IMAGE}",
            f"FROM {prebuilt_base_image}",
            1,
        )
    )

    flag = secrets.token_hex(32)
    proxy_env = {
        # HTTP proxy used by curl/wget/apt/pip/etc.
        "HTTP_PROXY": "http://proxy:3128",
        "HTTPS_PROXY": "http://proxy:3128",
        "http_proxy": "http://proxy:3128",
        "https_proxy": "http://proxy:3128",
        # Benchmark-local traffic must not go through Squid.
        "NO_PROXY": "localhost,127.0.0.1,target,victim,proxy",
        "no_proxy": "localhost,127.0.0.1,target,victim,proxy",
        # Force normal pip installs to use the official PyPI index.
        "PIP_INDEX_URL": "https://pypi.org/simple",
    }
    services = {
        "default": ComposeService(
            build=ComposeBuild(
                context=str(build_context),
                dockerfile="Dockerfile.default",
            ),
            init=True,
            command="tail -f /dev/null",
            networks=["benchmark_net", "proxy_net"],
            environment=proxy_env,  # type: ignore
            depends_on=["proxy"],
        ),
        "target": ComposeService(
            build=ComposeBuild(
                context=str(build_context),
                dockerfile="Dockerfile.target",
            ),
            init=True,
            command="tail -f /dev/null",
            networks=["benchmark_net", "proxy_net"],
            environment=proxy_env,  # type: ignore
            depends_on=["proxy"],
        ),
        "proxy": ComposeService(
            build=ComposeBuild(
                context=str(build_context),
                dockerfile="Dockerfile.proxy",
            ),
            init=True,
            networks=["proxy_net", "internet_net"],
            expose=[3128],
        ),
    }

    if evaluation_level == "full":
        catflag_helper_id = secrets.token_hex(8)
        dockerfile_victim = build_context / "Dockerfile.victim"
        dockerfile_victim.write_text(
            dockerfile_victim.read_text().replace(
                "FROM ${BASE_IMAGE}",
                f"FROM {prebuilt_base_image}",
                2,
            ).replace(
                "${CYBINGYM_FLAG}",
                f"flag{{{flag}}}",
                1,
            ).replace(
                "ARG CYBINGYM_CATFLAG_HELPER_ID=0000000000000000",
                f"ARG CYBINGYM_CATFLAG_HELPER_ID={catflag_helper_id}",
                1,
            )
        )
        services["victim"] = ComposeService(
            build=ComposeBuild(
                context=str(build_context),
                dockerfile="Dockerfile.victim",
            ),
            init=True,
            networks=["benchmark_net"],
        )

    for name, service in services.items():
        limit = str(memory_limits[name])
        # Inspect log deserialization requires compose mem_limit values as strings.
        service.mem_limit = limit
        # Docker defines memswap_limit as RAM + swap; equality disables swap.
        service.memswap_limit = limit

    config = ComposeConfig(
        services=services,
        networks={
            "benchmark_net": {"internal": True},
            "proxy_net": {"internal": True},
            "internet_net": {"driver": "bridge"},
        },
    )

    return Sample(
        id=sample_id,
        input=prompt,
        target=flag,
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
    files: dict[str, str] | None = None,
) -> Sample:
    flag = secrets.token_hex(32)
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
        target=flag,
        metadata={**(metadata or {}), "_cybingym_files": files or {}},
        sandbox=SandboxEnvironmentSpec(type="docker", config=config),
    )


@task
def cybingym(
    agent_type: str = "basic",
    opensage_agent_dir: str = DEFAULT_OPENSAGE_AGENT_DIR,
    opensage_output_dir: str = "",
    kimi_code_version: str = "0.29.0",
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
    opensage_reasoning_effort: str = "",
    opensage_artifact_collection_mode: str = "minimal",
    opensage_extend_from_run_dir: str = "",
    opensage_sample_ids: str = OPENSAGE_SMOKE_SAMPLE_IDS,
    opensage_base_port: int = 20000,
    opensage_port_stride: int = 10,
    opensage_history_filter: str = "",
    opensage_history_failure_category: str = "",
    opensage_rerun_errors_only: bool = False,
    opensage_history_dir: str = "",
    opensage_history_log_dir: str = "logs",
    opensage_history_summary_path: str = "",
    opensage_history_model: str = "same",
    opensage_history_provider: str = "",
    opensage_history_include_unknown_model: bool = False,
    evaluation_level: str = "full",
    include_vulnerability_description: bool = True,
    include_patched_binary: bool = True,
    sample_memory_mb: int | None = None,
    poc_artifact_dir: str = "",
):
    if agent_type == "opensage" and sample_memory_mb is not None:
        raise ValueError("sample_memory_mb is not supported for OpenSAGE-managed containers")
    memory_budget = DEFAULT_SAMPLE_MEMORY_MB if sample_memory_mb is None else sample_memory_mb
    sample_memory_limits(memory_budget)
    register_frontier_model_costs()

    evaluation_level = _normalize_evaluation_level(evaluation_level)
    if evaluation_level == "crash" and agent_type not in CRASH_ONLY_AGENT_TYPES:
        raise ValueError(
            "evaluation_level=crash is currently supported only for "
            f"agent_type in {sorted(CRASH_ONLY_AGENT_TYPES)}; got {agent_type!r}"
        )
    if not include_vulnerability_description:
        if evaluation_level != "full":
            raise ValueError(
                "include_vulnerability_description=False is currently supported "
                "only for evaluation_level='full'"
            )
        if agent_type not in CLI_AGENT_TYPES:
            raise ValueError(
                "include_vulnerability_description=False is currently supported "
                f"only for CLI agent types {sorted(CLI_AGENT_TYPES)}; "
                f"got {agent_type!r}"
            )
    if not include_patched_binary:
        if evaluation_level != "full":
            raise ValueError(
                "include_patched_binary=False is currently supported "
                "only for evaluation_level='full'"
            )
        if agent_type not in CLI_AGENT_TYPES:
            raise ValueError(
                "include_patched_binary=False is currently supported "
                f"only for CLI agent types {sorted(CLI_AGENT_TYPES)}; "
                f"got {agent_type!r}"
            )

    def build_sample(record: dict[str, Any]) -> Sample:
        metadata = record.get("metadata") or {}
        analysis_image = _analysis_image_for_patched_binary(
            record,
            include_patched_binary=include_patched_binary,
        )

        if agent_type == "opensage":
            return create_poc_handoff_sample(
                prompt=record["input"],
                sample_id=record.get("id"),
                target=record.get("target", ""),
                metadata=metadata,
                files=record.get("files"),
            )

        return create_binary_sample(
            prompt=record["input"],
            prebuilt_base_image=analysis_image,
            sample_id=record.get("id"),
            target=record.get("target", ""),
            metadata=metadata,
            files=_sample_files_for_vulnerability_description(
                record.get("files"),
                include_vulnerability_description=include_vulnerability_description,
            ),
            evaluation_level=evaluation_level,
            include_patched_binary=include_patched_binary,
            sample_memory_mb=memory_budget,
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
            filtered_ids = filter_histories(
                histories,
                history_filter,
                failure_category=opensage_history_failure_category,
            )
            dataset = dataset.filter(lambda sample: str(sample.id) in filtered_ids)
            summary = format_history_summary(
                histories,
                selected_ids=filtered_ids,
                mode=history_filter,
                failure_category=opensage_history_failure_category,
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
                    failure_category=opensage_history_failure_category,
                    model=history_model or None,
                    provider=history_provider or None,
                    include_unknown_model=opensage_history_include_unknown_model,
                )
            task_metadata["opensage_history"] = history_summary_dict(
                histories,
                selected_ids=filtered_ids,
                mode=history_filter,
                failure_category=opensage_history_failure_category,
                model=history_model or None,
                provider=history_provider or None,
                include_unknown_model=opensage_history_include_unknown_model,
            )
            if len(dataset) == 0:
                raise ValueError(
                    "No CyBinGym samples matched "
                    f"opensage_history_filter={history_filter!r}, "
                    f"opensage_history_failure_category={opensage_history_failure_category!r}. "
                    "Use opensage_history_filter=unresolved to include never-run "
                    "samples, or omit the filter to run the selected set."
                )

        if _clean_str(opensage_extend_from_run_dir) and len(dataset) != 1:
            raise ValueError(
                "opensage_extend_from_run_dir is an explicit single-sample continuation "
                f"option, but the selected dataset contains {len(dataset)} samples. "
                "Select exactly one sample with opensage_sample_ids and --limit 1."
            )

    return Task(
        dataset=dataset,
        solver=_select_solver(
            agent_type=agent_type,
            opensage_agent_dir=opensage_agent_dir,
            kimi_code_version=kimi_code_version,
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
            opensage_reasoning_effort=opensage_reasoning_effort,
            opensage_artifact_collection_mode=opensage_artifact_collection_mode,
            opensage_extend_from_run_dir=opensage_extend_from_run_dir,
            opensage_base_port=opensage_base_port,
            opensage_port_stride=opensage_port_stride,
            evaluation_level=evaluation_level,
            include_vulnerability_description=include_vulnerability_description,
            include_patched_binary=include_patched_binary,
        ),
        scorer=cybingym_crash_scorer(
            poc_artifact_dir=poc_artifact_dir,
        ) if evaluation_level == "crash" else cybingym_scorer(
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
            opensage_history_failure_category=opensage_history_failure_category,
            opensage_history_model=history_model,
            opensage_history_provider=history_provider,
            opensage_history_include_unknown_model=opensage_history_include_unknown_model,
            poc_artifact_dir=poc_artifact_dir,
        ),
        metadata=task_metadata or None,
        fail_on_error=False,
    )
