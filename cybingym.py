import shutil
import secrets
import tempfile

from pathlib import Path
from typing import Any
from scorer import cybingym_scorer

from solvers.react import react_solver
from solvers.openai_agent import openai_agent
from solvers.swe_agents import claude_code_solver, codex_cli_solver, kimi_code_solver

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

def _select_solver(agent_type: str, kimi_code_version: str = "0.29.0"):
    if agent_type == "kimi_code":
        return kimi_code_solver(version=kimi_code_version)
    return SOLVER_MAP.get(
        agent_type,
        SOLVER_MAP["basic"],
    )

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
    flag = secrets.token_hex(32)
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


    config = ComposeConfig(
        services={
            "default": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.default",
                ),
                init=True,
                command="tail -f /dev/null",

                networks=[
                    "benchmark_net",
                    "proxy_net",
                ],

                environment=proxy_env, # type: ignore

                depends_on=[
                    "proxy",
                ],
            ),

            "target": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.target",
                ),
                init=True,
                command="tail -f /dev/null",

                networks=[
                    "benchmark_net",
                    "proxy_net",
                ],

                environment=proxy_env, # type: ignore

                depends_on=[
                    "proxy",
                ],
            ),

            "victim": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.victim",
                ),
                init=True,

                # Can communicate with default/target,
                # but has no Internet and cannot reach proxy.
                networks=[
                    "benchmark_net",
                ],
            ),

            "proxy": ComposeService(
                build=ComposeBuild(
                    context=str(build_context),
                    dockerfile="Dockerfile.proxy",
                ),
                init=True,

                # One isolated side facing default,
                # one side facing the Internet.
                networks=[
                    "proxy_net",
                    "internet_net",
                ],

                expose=[
                    3128,
                ],
            ),
        },

        networks={
            # default <-> target <-> victim
            # No external network route.
            "benchmark_net": {
                "internal": True,
            },

            # default <-> squid
            # Also no external network route.
            "proxy_net": {
                "internal": True,
            },

            # Only Squid is attached to this network.
            "internet_net": {
                "driver": "bridge",
            },
        },
    )

    # config = ComposeConfig(
    #     services = {
    #         "default": ComposeService(
    #             build = ComposeBuild(
    #                 context = str(build_context),
    #                 dockerfile = "Dockerfile.default",
    #             ),
    #             init = True,
    #             command = "tail -f /dev/null",
    #         ),
    #         "target": ComposeService(
    #             build = ComposeBuild(
    #                 context = str(build_context),
    #                 dockerfile = "Dockerfile.target",
    #             ),
    #             init = True,
    #             command = "tail -f /dev/null",
    #         ),
    #         "victim": ComposeService(
    #             build = ComposeBuild(
    #                 context = str(build_context),
    #                 dockerfile = "Dockerfile.victim",
    #             ),
    #             init = True,
    #         )
    #     }
    # )

    sandbox_spec = SandboxEnvironmentSpec(
        type="docker",
        config=config
    )

    return Sample(
        id=sample_id,
        input=prompt,
        target=flag,
        metadata=metadata,
        sandbox=sandbox_spec,
        files=files,
    )

def create_cybingym_task(
    dataset_file_path: Path,
    agent_type: str,
    kimi_code_version: str = "0.29.0",
) -> Task:
    def build_sample(record: dict[str, Any]) -> Sample:
        metadata = record.get("metadata") or {}
        analysis_image = metadata.get("analysis_image")

        if not analysis_image:
            raise ValueError(
                "Dataset record is missing metadata.analysis_image"
            )

        return create_binary_sample(
            prompt=record["input"],
            prebuilt_base_image=analysis_image,
            sample_id=record.get("id"),
            target=record.get("target", ""),
            metadata=metadata,
            files=record.get("files"),
        )

    return Task(
        dataset=json_dataset(
            str(dataset_file_path),
            sample_fields=build_sample,
        ),
        solver=_select_solver(agent_type, kimi_code_version),
        scorer=cybingym_scorer(),
        fail_on_error=False,
    )

MAIN_SOURCE_DIR = Path(__file__).resolve().parent

@task
def cybingym(agent_type: str = "basic", kimi_code_version: str = "0.29.0") -> Task:
    return create_cybingym_task(
        dataset_file_path=MAIN_SOURCE_DIR / "dataset.json",
        agent_type=agent_type,
        kimi_code_version=kimi_code_version,
    )

@task
def cybingym20(agent_type: str = "basic", kimi_code_version: str = "0.29.0") -> Task:
    return create_cybingym_task(
        dataset_file_path=MAIN_SOURCE_DIR / "dataset20.json",
        agent_type=agent_type,
        kimi_code_version=kimi_code_version,
    )
