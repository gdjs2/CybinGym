import shutil
import tempfile

from pathlib import Path
from typing import Any
from scorer import cybingym_scorer

from solvers.react import react_solver
from solvers.openai_agent import openai_agent
from solvers.swe_agents import claude_code_solver, codex_cli_solver

from inspect_ai import task, Task
from inspect_ai.util import ComposeBuild, ComposeConfig, ComposeService, SandboxEnvironmentSpec
from inspect_ai.dataset import json_dataset, Sample

MAXIMUM_ATTEMPTS = 1

# Add more agent bridges here as needed
SOLVER_MAP = {
    "basic": react_solver(attempts=MAXIMUM_ATTEMPTS),
    "openai": openai_agent(),
    "claude_code": claude_code_solver(),
    "codex": codex_cli_solver(),
}

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
    source_context = Path(__file__).resolve().parent / "agent_env"
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
        services = {
            "default": ComposeService(
                build = ComposeBuild(
                    context = str(build_context),
                    dockerfile = "Dockerfile.default",
                ),
                init = True,
                command = "tail -f /dev/null",
            ),
            "target": ComposeService(
                build = ComposeBuild(
                    context = str(build_context),
                    dockerfile = "Dockerfile.target",
                ),
                init = True,
                command = "tail -f /dev/null",
            )
        }
    )

    sandbox_spec = SandboxEnvironmentSpec(
        type="docker",
        config=config
    )

    return Sample(
        id=sample_id,
        input=prompt,
        target=target,
        metadata=metadata,
        sandbox=sandbox_spec,
        files=files,
    )

@task
def cybingym(agent_type: str = "basic"):
    def build_sample(record: dict[str, Any]) -> Sample:
        metadata = record.get("metadata") or {}
        analysis_image = metadata.get("analysis_image")
        if not analysis_image:
            raise ValueError("Dataset record is missing metadata.analysis_image")

        return create_binary_sample(
            prompt=record["input"],
            prebuilt_base_image=analysis_image,
            sample_id=record.get("id"),
            target=record.get("target", ""),
            metadata=metadata,
            files=record.get("files"),
        )

    return Task(
        dataset = json_dataset("dataset.json", sample_fields=build_sample),
        solver=SOLVER_MAP.get(agent_type, SOLVER_MAP["basic"]),
        scorer = cybingym_scorer(),
        fail_on_error = False
    )