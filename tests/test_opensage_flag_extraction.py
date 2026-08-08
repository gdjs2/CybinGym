from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path


try:
    import opensage.evaluation.base  # noqa: F401
except ModuleNotFoundError as exc:
    if exc.name != "opensage":
        raise

    opensage_module = types.ModuleType("opensage")
    evaluation_module = types.ModuleType("opensage.evaluation")
    base_module = types.ModuleType("opensage.evaluation.base")
    sandbox_module = types.ModuleType("opensage.sandbox")
    native_sandbox_module = types.ModuleType("opensage.sandbox.native_docker_sandbox")

    class Evaluation:
        def __post_init__(self):
            pass

        def _get_config_template_variables(self, task):
            return {}

        async def _prepare_environment(self, task):
            raise RuntimeError("OpenSAGE is not installed")

        async def _collect_outputs(self, task, session):
            return {}

    class EvaluationTask:
        pass

    class NativeDockerSandbox:
        @classmethod
        def _get_helper_image(cls):
            return ""

    native_sandbox_module.NativeDockerSandbox = NativeDockerSandbox
    sandbox_module.native_docker_sandbox = native_sandbox_module
    base_module.Evaluation = Evaluation
    base_module.EvaluationTask = EvaluationTask
    evaluation_module.base = base_module
    opensage_module.evaluation = evaluation_module
    opensage_module.sandbox = sandbox_module
    sys.modules["opensage"] = opensage_module
    sys.modules["opensage.evaluation"] = evaluation_module
    sys.modules["opensage.evaluation.base"] = base_module
    sys.modules["opensage.sandbox"] = sandbox_module
    sys.modules["opensage.sandbox.native_docker_sandbox"] = native_sandbox_module

from solvers import opensage_agent
from solvers import opensage_cybingym_runner
from solvers import prompts


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **kwargs):
        return self.payload

    def model_dump_json(self, **kwargs):
        return json.dumps(self.payload)


class OpenSageLeakageTests(unittest.TestCase):
    def test_initial_data_dir_does_not_write_sample_json(self):
        sample = {
            "id": "sample-1",
            "input": "prompt",
            "target": "4db0dc42d846a013006afdac736e8c67dd348760e1255bc3416e9f884c579ccc",
            "metadata": {"target_binary": "bin"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            desc_dir = root / "data" / "sample-1"
            desc_dir.mkdir(parents=True)
            (desc_dir / "desc.txt").write_text("description", encoding="utf-8")

            evaluation = object.__new__(opensage_cybingym_runner.CyBinGymOpenSageEvaluation)
            evaluation.cybingym_dir = str(root)
            evaluation.output_dir = str(root / "outputs")

            shared_dir = Path(evaluation._get_initial_data_dir(sample))

            self.assertTrue((shared_dir / "desc.txt").exists())
            self.assertFalse((shared_dir / "sample.json").exists())

    def test_opensage_runner_collects_poc_and_poc_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "runner"
            task_output = root / "task"
            sandbox_output = task_output / "sandbox_output"
            sandbox_output.mkdir(parents=True)
            (sandbox_output / "poc_crash").write_bytes(b"crash")
            (sandbox_output / "poc").write_bytes(b"exploit")

            evaluation = object.__new__(opensage_cybingym_runner.CyBinGymOpenSageEvaluation)
            evaluation.output_dir = str(output_dir)
            evaluation.reasoning_effort = ""
            evaluation.gdb_port = 1111
            evaluation.ida_pro_mcp_port = 1112
            evaluation.pyghidra_mcp_port = 1113
            task = types.SimpleNamespace(
                id="task-1",
                sample={"id": "sample-1"},
                output_dir=str(task_output),
                session_id="session-1",
            )

            original_collect = opensage_cybingym_runner.Evaluation._collect_outputs

            async def fake_collect(self, task, session):
                return {}

            opensage_cybingym_runner.Evaluation._collect_outputs = fake_collect
            try:
                info = asyncio.run(
                    evaluation._collect_outputs(
                        task,
                        FakeSession(
                            {
                                "events": [
                                    {
                                        "author": "ctf_agent",
                                        "content": {
                                            "parts": [{"text": "flag{abc123}"}]
                                        },
                                    }
                                ]
                            }
                        ),
                    )
                )
            finally:
                opensage_cybingym_runner.Evaluation._collect_outputs = original_collect

            self.assertEqual((output_dir / "poc_crash").read_bytes(), b"crash")
            self.assertEqual((output_dir / "poc").read_bytes(), b"exploit")
            self.assertTrue(info["cybingym"]["poc_crash_found"])
            self.assertTrue(info["cybingym"]["poc_found"])
            self.assertEqual(info["cybingym"]["submitted_flag"], "flag{abc123}")

    def test_prompts_require_poc_and_poc_crash(self):
        self.assertIn("/shared/poc_crash", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("/shared/poc", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertEqual(opensage_cybingym_runner.CyBinGymOpenSageEvaluation._get_export_dir_in_sandbox(None, {}), "/shared")
        self.assertIn("/CybinGym_workdir/poc_crash", prompts.exploit_prompt)
        self.assertIn("/CybinGym_workdir/poc", prompts.exploit_prompt)

    def test_victim_dockerfile_rewrite_uses_copy_without_flag_variable(self):
        template = (REPO_ROOT / "agent_env" / "Dockerfile.victim").read_text(
            encoding="utf-8"
        )

        rewritten = opensage_cybingym_runner._victim_dockerfile_with_flag_copy(template)

        self.assertIn("COPY flag.txt /flag.txt", rewritten)
        self.assertIn("RUN chmod 644 /flag.txt", rewritten)
        self.assertNotIn("ARG FLAG", rewritten)
        self.assertNotIn("CYBINGYM_FLAG", rewritten)
        self.assertNotIn("${FLAG}", rewritten)
        self.assertNotIn("flag{", rewritten)

    def test_opensage_config_does_not_reference_flag_template_variable(self):
        config = (REPO_ROOT / "solvers" / "ctf_agent" / "config.toml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("CYBINGYM_FLAG", config)


class OpenSageFlagExtractionTests(unittest.TestCase):
    def test_runner_ignores_user_prompt_placeholder(self):
        session = FakeSession(
            {
                "events": [
                    {
                        "author": "user",
                        "content": {"parts": [{"text": "format is flag{...}"}]},
                    },
                    {
                        "author": "ctf_agent",
                        "content": {"parts": [{"text": "retrieved flag{abc123}"}]},
                    },
                ]
            }
        )

        self.assertEqual(
            opensage_cybingym_runner._extract_submitted_flag_from_session(session),
            "flag{abc123}",
        )

    def test_runner_returns_empty_for_placeholder_only_session(self):
        session = FakeSession(
            {
                "events": [
                    {
                        "author": "user",
                        "content": {"parts": [{"text": "format is flag{...}"}]},
                    }
                ]
            }
        )

        self.assertEqual(
            opensage_cybingym_runner._extract_submitted_flag_from_session(session),
            "",
        )

    def test_runner_reads_agent_flag_from_persisted_trajectory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_output = Path(tmpdir)
            traj_dir = task_output / "instances" / "root" / "subagent"
            traj_dir.mkdir(parents=True)
            events = {
                "events": [
                    {
                        "author": "user",
                        "content": {"parts": [{"text": "format is flag{...}"}]},
                    },
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "function_call": {
                                        "name": "run_binary_sandbox_command",
                                        "args": {"command": "echo flag{not_submitted}"},
                                    }
                                }
                            ]
                        },
                    },
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "function_response": {
                                        "name": "run_binary_sandbox_command",
                                        "response": {"output": "flag{abc123}\n"},
                                    }
                                }
                            ]
                        },
                    },
                ]
            }
            (traj_dir / "traj.json").write_text(
                json.dumps(events), encoding="utf-8"
            )

            self.assertEqual(
                opensage_cybingym_runner._extract_submitted_flag_from_task_output(
                    task_output
                ),
                "flag{abc123}",
            )

    def test_runner_ignores_persisted_trajectory_function_call_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            task_output = Path(tmpdir)
            traj_dir = task_output / "instances" / "root"
            traj_dir.mkdir(parents=True)
            events = {
                "events": [
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "function_call": {
                                        "name": "run_binary_sandbox_command",
                                        "args": {"command": "echo flag{not_submitted}"},
                                    }
                                }
                            ]
                        },
                    }
                ]
            }
            (traj_dir / "traj.json").write_text(
                json.dumps(events), encoding="utf-8"
            )

            self.assertEqual(
                opensage_cybingym_runner._extract_submitted_flag_from_task_output(
                    task_output
                ),
                "",
            )

    def test_bridge_ignores_placeholder_summary_and_user_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "cybingym_result.json").write_text(
                json.dumps({"submitted_flag": "flag{...}"}),
                encoding="utf-8",
            )
            task_dir = output_dir / "cybingym_1"
            task_dir.mkdir()
            (task_dir / "live_events.jsonl").write_text(
                json.dumps(
                    {
                        "author": "user",
                        "content": {"parts": [{"text": "format is flag{...}"}]},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                opensage_agent._extract_flag_from_opensage_outputs(output_dir),
                "",
            )

    def test_bridge_reads_agent_tool_response_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            task_dir = output_dir / "cybingym_1"
            task_dir.mkdir()
            events = [
                {
                    "author": "user",
                    "content": {"parts": [{"text": "format is flag{...}"}]},
                },
                {
                    "author": "ctf_agent",
                    "content": {
                        "parts": [
                            {
                                "function_response": {
                                    "name": "run_binary_sandbox_command",
                                    "response": {"output": "flag{abc123}\n"},
                                }
                            }
                        ]
                    },
                },
            ]
            (task_dir / "live_events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            self.assertEqual(
                opensage_agent._extract_flag_from_opensage_outputs(output_dir),
                "flag{abc123}",
            )

    def test_bridge_ignores_agent_function_call_arguments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            task_dir = output_dir / "cybingym_1"
            task_dir.mkdir()
            event = {
                "author": "ctf_agent",
                "content": {
                    "parts": [
                        {
                            "function_call": {
                                "name": "run_binary_sandbox_command",
                                "args": {"command": "echo flag{not_submitted}"},
                            }
                        }
                    ]
                },
            }
            (task_dir / "live_events.jsonl").write_text(
                json.dumps(event) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(
                opensage_agent._extract_flag_from_opensage_outputs(output_dir),
                "",
            )


    def test_bridge_requires_poc_for_cancel_suppression(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            self.assertFalse(opensage_agent._has_recoverable_poc_crash(output_dir))

            (output_dir / "poc_crash").write_bytes(b"crash")

            self.assertTrue(opensage_agent._has_recoverable_poc_crash(output_dir))


class OpenSageRuntimeFailureTests(unittest.TestCase):
    def test_sensitive_dependency_debug_loggers_are_suppressed(self):
        for logger_name in opensage_cybingym_runner._SENSITIVE_DEBUG_LOGGERS:
            logging.getLogger(logger_name).setLevel(logging.DEBUG)

        opensage_cybingym_runner._suppress_sensitive_dependency_debug_logs()

        for logger_name in opensage_cybingym_runner._SENSITIVE_DEBUG_LOGGERS:
            self.assertGreaterEqual(
                logging.getLogger(logger_name).getEffectiveLevel(), logging.WARNING
            )

    def test_runtime_mcp_failure_scanner_ignores_embedded_peer_message_text(self):
        line = (
            "2026-08-08 15:05:30 | WARNING  | opensage.evaluation.base:1064 - "
            '{"content":{"parts":[{"text":"[Incoming peer messages]\\n'
            'kind=error: a sub-agent CRASHED. Previous log: '
            '2026-08-08 15:05:29 | WARNING  | opensage.agents.opensage_agent:101 - '
            'MCP tool pyghidra_mcp_decompile_function failed"}]}}'
        )

        self.assertFalse(
            opensage_cybingym_runner._is_required_mcp_runtime_failure(line)
        )

    def test_runtime_mcp_failure_scanner_keeps_actual_mcp_warning(self):
        line = (
            "2026-08-08 15:05:29 | WARNING  | "
            "opensage.agents.opensage_agent:101 - MCP tool "
            "pyghidra_mcp_decompile_function failed: Timed out while waiting"
        )

        self.assertTrue(
            opensage_cybingym_runner._is_required_mcp_runtime_failure(line)
        )

    def test_runner_marks_runtime_mcp_failure_nonfatal_when_poc_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            task_dir = output_dir / "cybingym_1"
            task_dir.mkdir()
            (output_dir / "cybingym_result.json").write_text(
                json.dumps({"poc_crash_found": True, "submitted_flag": "flag{abc123}"}),
                encoding="utf-8",
            )

            evaluation = object.__new__(
                opensage_cybingym_runner.CyBinGymOpenSageEvaluation
            )
            evaluation.output_dir = str(output_dir)
            task = type(
                "Task",
                (),
                {
                    "id": "cybingym_1",
                    "sample": {"id": "1"},
                    "output_dir": str(task_dir),
                    "session_id": "session-1",
                },
            )()
            runtime_path = task_dir / "mcp_runtime_failures.json"

            evaluation._write_mcp_runtime_failure_result(
                task, {"total": 1, "hits": []}, runtime_path, fatal=False
            )

            summary = json.loads((output_dir / "cybingym_result.json").read_text())
            self.assertFalse(summary["mcp_runtime"]["fatal"])
            self.assertTrue(summary["poc_crash_found"])
            self.assertEqual(summary["submitted_flag"], "flag{abc123}")



if __name__ == "__main__":
    unittest.main()
