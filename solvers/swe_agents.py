from solvers.react import prompt
from solvers.prompts import codex_prompt

from inspect_ai.tool import bash, tool_with
from inspect_ai.agent import Agent, BridgedToolsSpec
from inspect_ai.solver import Generate, Solver, TaskState, solver

from inspect_swe import claude_code, codex_cli

@solver
def inject_system_to_user(prompt: str) -> Solver:
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        for msg in state.messages:
            if msg.role == "user":
                msg.content = prompt
        return state
    return solve

def swe_bridged_tools() -> list[BridgedToolsSpec]:
    return [
        BridgedToolsSpec(
            name = "bash_tools_for_target_and_defaults",
            tools = [
                tool_with(
                    bash(timeout=120, sandbox="default"),
                    name="default",
                    description="Bash environment of the default docker image. "
                ),
                tool_with(
                    bash(timeout=120, sandbox="target"),
                    name="target",
                    description="Bash environment of the target docker image. "
                )
            ],
        )
    ]

def claude_code_solver() -> list[Solver | Agent]:
    return [
        inject_system_to_user(prompt),
        claude_code(
            disallowed_tools = ["Bash", "WebSearch"],
            bridged_tools = swe_bridged_tools()
        )
    ]

def codex_cli_solver() -> list[Solver | Agent]:
    return [
        inject_system_to_user(codex_prompt),
        codex_cli(
            web_search = "disabled",
            bridged_tools = swe_bridged_tools(),
            model_config = "gpt-5.5"
        )
    ]
