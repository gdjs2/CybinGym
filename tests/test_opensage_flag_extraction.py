from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from solvers import opensage_agent
from solvers import opensage_cybingym_runner


REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **kwargs):
        return self.payload

    def model_dump_json(self, **kwargs):
        return json.dumps(self.payload)


class OpenSageLeakageTests(unittest.TestCase):
    def test_shared_sample_payload_removes_target_without_mutating_sample(self):
        sample = {
            "id": "sample-1",
            "input": "prompt",
            "target": "4db0dc42d846a013006afdac736e8c67dd348760e1255bc3416e9f884c579ccc",
            "metadata": {"target_binary": "bin"},
        }

        payload = opensage_cybingym_runner._shared_sample_payload(sample)

        self.assertNotIn("target", payload)
        self.assertEqual(sample["target"], "4db0dc42d846a013006afdac736e8c67dd348760e1255bc3416e9f884c579ccc")
        self.assertEqual(payload["metadata"], {"target_binary": "bin"})

    def test_victim_dockerfile_rewrite_uses_copy_without_flag_variable(self):
        template = (REPO_ROOT / "agent_env" / "Dockerfile.victim").read_text(
            encoding="utf-8"
        )

        rewritten = opensage_cybingym_runner._victim_dockerfile_with_flag_copy(template)

        self.assertIn("COPY flag.txt /flag.txt", rewritten)
        self.assertIn("RUN chmod 644 /flag.txt", rewritten)
        self.assertNotIn("CYBINGYM_FLAG", rewritten)
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
