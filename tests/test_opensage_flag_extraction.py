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


class FakeSandbox:
    def __init__(self, files):
        self.files = files

    async def arun_command_in_container(self, command, timeout=None):
        del command, timeout
        return "/shared/run.summary.txt\n/shared/sample.json\n", 0

    async def acopy_file_from_container(self, src_path, dst_path):
        if src_path not in self.files:
            raise FileNotFoundError(src_path)
        Path(dst_path).write_bytes(self.files[src_path])


class FakeSandboxCollection:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def get_sandbox(self, name):
        if name != "main":
            raise KeyError(name)
        return self.sandbox


class FakeOpenSageSession:
    def __init__(self, sandbox):
        self.sandboxes = FakeSandboxCollection(sandbox)


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

    def test_extend_previous_run_seeds_sanitized_previous_run_directory(self):
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

            previous_run = root / "evals" / "opensage_inspect" / "sample-1" / "260101_000001"
            previous_run.mkdir(parents=True)
            (previous_run / "opensage_bridge_status.json").write_text(
                json.dumps(
                    {
                        "sample_id": "sample-1",
                        "status": "finished",
                        "returncode": 0,
                    }
                ),
                encoding="utf-8",
            )
            (previous_run / "cybingym_result.json").write_text(
                json.dumps(
                    {
                        "sample_id": "sample-1",
                        "poc_crash_found": True,
                        "poc_found": True,
                        "submitted_flag": "flag{oldsecret}",
                    }
                ),
                encoding="utf-8",
            )
            (previous_run / "poc_crash").write_bytes(b"crash")
            (previous_run / "poc").write_bytes(b"exploit")
            (previous_run / "poc.response").write_text(
                f"old response flag{{oldsecret}} path {previous_run}",
                encoding="utf-8",
            )
            (previous_run / "live_events.jsonl").write_text(
                json.dumps(
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "Found unchecked length field in parser; "
                                        f"continue from {previous_run} but ignore flag{{oldsecret}}"
                                    )
                                }
                            ]
                        },
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "function_call": {
                                        "name": "run_binary_sandbox_command",
                                        "args": {"command": "check crashing offset"},
                                    }
                                }
                            ]
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report_dir = previous_run / "cybingym_sample-1" / "sandbox_output" / "shared"
            report_dir.mkdir(parents=True)
            (report_dir / "report.txt").write_text(
                "prior report flag{oldsecret}",
                encoding="utf-8",
            )

            evaluation = object.__new__(
                opensage_cybingym_runner.CyBinGymOpenSageEvaluation
            )
            evaluation.cybingym_dir = str(root)
            evaluation.output_dir = str(root / "outputs")
            evaluation.extend_from_run_dir = str(previous_run)

            shared_dir = Path(evaluation._get_initial_data_dir(sample))

            self.assertTrue((shared_dir / "desc.txt").exists())
            self.assertFalse((shared_dir / "poc_crash").exists())
            self.assertFalse((shared_dir / "poc").exists())

            previous_shared = shared_dir / "previous_run"
            self.assertEqual((previous_shared / "poc_crash").read_bytes(), b"crash")
            self.assertEqual((previous_shared / "poc").read_bytes(), b"exploit")
            self.assertNotIn(
                "flag{oldsecret}",
                (previous_shared / "poc.response").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "flag{<redacted_previous_run>}",
                (previous_shared / "report.txt").read_text(encoding="utf-8"),
            )
            context_text = (previous_shared / "previous_run_context.txt").read_text(
                encoding="utf-8"
            )
            analysis_text = (previous_shared / "previous_run_analysis.txt").read_text(
                encoding="utf-8"
            )
            readme_text = (previous_shared / "README.txt").read_text(encoding="utf-8")
            self.assertIn("/shared/previous_run/", context_text)
            self.assertIn("Prior analysis summary from live_events.jsonl", context_text)
            self.assertIn("unchecked length field", context_text)
            self.assertIn("tool call run_binary_sandbox_command", context_text)
            self.assertIn("unchecked length field", analysis_text)
            self.assertNotIn(str(previous_run), context_text)
            self.assertNotIn(str(previous_run), analysis_text)
            self.assertNotIn(str(previous_run), readme_text)
            self.assertTrue((previous_shared / "README.txt").exists())

    def test_extend_previous_run_prompt_redacts_flags_and_validates_sample(self):
        sample = {
            "id": "1",
            "input": "original task prompt",
            "metadata": {"target_binary": "target-bin"},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            previous_run = root / "1" / "260101_000001"
            previous_run.mkdir(parents=True)
            (previous_run / "opensage_bridge_status.json").write_text(
                json.dumps({"sample_id": "1", "status": "error", "returncode": 1}),
                encoding="utf-8",
            )
            (previous_run / "cybingym_result.json").write_text(
                json.dumps({"submitted_flag": "flag{oldsecret}"}),
                encoding="utf-8",
            )
            (previous_run / "report.txt").write_text(
                "found old flag{oldsecret}",
                encoding="utf-8",
            )
            (previous_run / "live_events.jsonl").write_text(
                json.dumps(
                    {
                        "author": "ctf_agent",
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "analysis says stack pivot candidate; "
                                        f"host path {previous_run}; flag{{oldsecret}}"
                                    )
                                }
                            ]
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            evaluation = object.__new__(
                opensage_cybingym_runner.CyBinGymOpenSageEvaluation
            )
            evaluation.extend_from_run_dir = str(previous_run)
            evaluation.max_llm_calls = 0

            prompt = evaluation._get_first_user_message(sample)

            self.assertIn("Previous Run Context", prompt)
            self.assertIn("/shared/previous_run/", prompt)
            self.assertIn("Prior analysis summary from live_events.jsonl", prompt)
            self.assertIn("stack pivot candidate", prompt)
            self.assertIn("flag{<redacted_previous_run>}", prompt)
            self.assertNotIn("flag{oldsecret}", prompt)
            self.assertNotIn(str(previous_run), prompt)

            (previous_run / "opensage_bridge_status.json").write_text(
                json.dumps({"sample_id": "2"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                evaluation._get_first_user_message(sample)

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

    def test_minimal_artifact_collection_copies_required_files_and_small_reports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            task_dir = output_dir / "task"
            task_dir.mkdir(parents=True)
            sandbox = FakeSandbox(
                {
                    "/shared/poc_crash": b"crash",
                    "/shared/poc": b"exploit",
                    "/shared/run.summary.txt": b"summary",
                    "/shared/sample.json": b"leak",
                }
            )
            task = types.SimpleNamespace(
                output_dir=str(task_dir),
                opensage_session=FakeOpenSageSession(sandbox),
            )
            evaluation = object.__new__(
                opensage_cybingym_runner.CyBinGymOpenSageEvaluation
            )
            evaluation.artifact_collection_mode = "minimal"

            status = asyncio.run(evaluation._collect_minimal_shared_outputs(task))

            shared_output = task_dir / "sandbox_output" / "shared"
            self.assertEqual((shared_output / "poc_crash").read_bytes(), b"crash")
            self.assertEqual((shared_output / "poc").read_bytes(), b"exploit")
            self.assertEqual(
                (shared_output / "run.summary.txt").read_bytes(), b"summary"
            )
            self.assertFalse((shared_output / "sample.json").exists())
            self.assertIn("poc.response", status["missing"])

    def test_prompts_require_poc_and_poc_crash(self):
        self.assertIn("/shared/poc_crash", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("/shared/poc", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("Maximum LLM calls", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("arbitrary code execution", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("setuid-root", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("randomized name/path", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIn("installed only in the `victim` container", opensage_cybingym_runner.DEFAULT_PROMPT_TEMPLATE)
        self.assertIsNone(
            opensage_cybingym_runner.CyBinGymOpenSageEvaluation._get_export_dir_in_sandbox(
                None, {}
            )
        )
        evaluation = object.__new__(
            opensage_cybingym_runner.CyBinGymOpenSageEvaluation
        )
        evaluation.artifact_collection_mode = "debug"
        evaluation.max_llm_calls = 700
        prompt = evaluation._get_first_user_message(
            {
                "id": "16969",
                "input": "original task prompt",
                "metadata": {"target_binary": "gstoraster_fuzzer"},
            }
        )
        self.assertIn("Maximum LLM calls for this run: 700 total LLM calls", prompt)
        self.assertIn("shared by the root agent and all subagents", prompt)
        self.assertEqual(evaluation._get_export_dir_in_sandbox({}), "/shared")
        self.assertIn("/CybinGym_workdir/poc_crash", prompts.exploit_prompt)
        self.assertIn("/CybinGym_workdir/poc", prompts.exploit_prompt)

    def test_runner_uses_configured_agent_timeout(self):
        evaluation = object.__new__(
            opensage_cybingym_runner.CyBinGymOpenSageEvaluation
        )

        evaluation.agent_timeout = 300
        self.assertEqual(evaluation._get_agent_timeout(None), 300.0)

        evaluation.agent_timeout = 0
        self.assertIsNone(evaluation._get_agent_timeout(None))

    def test_bridge_passes_timeout_to_runner_agent_timeout(self):
        captured = {}

        class FakeProcess:
            pid = 12345
            returncode = 0

            async def wait(self):
                return self.returncode

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return FakeProcess()

        original_create = opensage_agent.asyncio.create_subprocess_exec
        opensage_agent.asyncio.create_subprocess_exec = fake_create_subprocess_exec
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                returncode, _, _, status = asyncio.run(
                    opensage_agent._run_opensage(
                        sample={"id": "1", "input": "", "target": "", "metadata": {}},
                        output_dir=root / "out",
                        cybingym_dir=root,
                        opensage_agent_dir=root / "agent",
                        opensage_source_dir=root / "opensage",
                        opensage_python=Path("/usr/bin/python3"),
                        opensage_model="openai/gpt-5.6",
                        opensage_provider="openai",
                        opensage_reasoning_effort="max",
                        ports={
                            "gdb_port": 20000,
                            "ida_pro_mcp_port": 20001,
                            "pyghidra_mcp_port": 20002,
                        },
                        max_llm_calls=600,
                        max_workers=1,
                        timeout=300,
                        cleanup_grace=900,
                        llm_retry_timeout=600,
                        llm_retry_count=5,
                        artifact_collection_mode="minimal",
                        extend_from_run_dir=str(root / "previous"),
                    )
                )
        finally:
            opensage_agent.asyncio.create_subprocess_exec = original_create

        self.assertEqual(returncode, 0)
        cmd = list(captured["cmd"])
        self.assertIn("--agent_timeout", cmd)
        self.assertEqual(cmd[cmd.index("--agent_timeout") + 1], "300")
        self.assertEqual(status["agent_timeout_seconds"], 300)
        self.assertEqual(status["bridge_wait_timeout_seconds"], 1200)
        self.assertIn("--extend_from_run_dir", cmd)
        self.assertEqual(
            cmd[cmd.index("--extend_from_run_dir") + 1],
            str(root / "previous"),
        )
        self.assertEqual(status["extend_from_run_dir"], str(root / "previous"))

    def test_victim_dockerfile_rewrite_uses_copy_without_flag_variable(self):
        template = (REPO_ROOT / "agent_env" / "Dockerfile.victim").read_text(
            encoding="utf-8"
        )
        helper_id = "0123456789abcdef"

        rewritten = opensage_cybingym_runner._victim_dockerfile_with_flag_copy(
            template, helper_id
        )

        helper_path = (
            "/opt/cybingym/.helper_${CYBINGYM_CATFLAG_HELPER_ID}/"
            "catflag_${CYBINGYM_CATFLAG_HELPER_ID}"
        )
        self.assertEqual(rewritten.count("AS cybingym_catflag_builder"), 1)
        self.assertIn("COPY catflag_helper.c /tmp/catflag_helper.c", rewritten)
        self.assertIn("ARG CYBINGYM_CATFLAG_HELPER_ID=0123456789abcdef", rewritten)
        self.assertIn("COPY flag.txt /flag.txt", rewritten)
        self.assertIn("RUN chown root:root /flag.txt && chmod 0600 /flag.txt", rewritten)
        self.assertIn(
            "COPY --from=cybingym_catflag_builder "
            "/tmp/catflag_helper /tmp/catflag_helper",
            rewritten,
        )
        self.assertIn(helper_path, rewritten)
        self.assertIn("-m 04755", rewritten)
        self.assertIn("chmod 0755", rewritten)
        self.assertNotIn("chmod 644 /flag.txt", rewritten)
        self.assertNotIn("ARG FLAG", rewritten)
        self.assertNotIn("CYBINGYM_FLAG", rewritten)
        self.assertNotIn("${FLAG}", rewritten)
        self.assertNotIn("flag{", rewritten)

    def test_prepare_victim_build_context_writes_randomized_catflag_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            evaluation = object.__new__(
                opensage_cybingym_runner.CyBinGymOpenSageEvaluation
            )
            evaluation.victim_dockerfile = str(REPO_ROOT / "agent_env" / "Dockerfile.victim")
            task = types.SimpleNamespace(
                output_dir=str(root / "task"),
                sample={"id": "sample-1", "target": "abc123"},
            )

            dockerfile_path = evaluation._prepare_victim_build_context(task)

            build_context = dockerfile_path.parent
            dockerfile = dockerfile_path.read_text(encoding="utf-8")
            helper_source = build_context / opensage_cybingym_runner._CATFLAG_HELPER_SOURCE_NAME
            self.assertTrue(helper_source.exists())
            self.assertIn('open("/flag.txt", O_RDONLY | O_CLOEXEC)', helper_source.read_text(encoding="utf-8"))
            self.assertRegex(
                dockerfile,
                r"ARG CYBINGYM_CATFLAG_HELPER_ID=[a-f0-9]{16}",
            )
            self.assertIn(
                "/opt/cybingym/.helper_${CYBINGYM_CATFLAG_HELPER_ID}/"
                "catflag_${CYBINGYM_CATFLAG_HELPER_ID}",
                dockerfile,
            )
            self.assertIn("-m 04755", dockerfile)
            self.assertIn("chmod 0755", dockerfile)
            self.assertNotIn("flag{abc123}", dockerfile)
            self.assertEqual((build_context / "flag.txt").read_text(encoding="utf-8"), "flag{abc123}\n")

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
            self.assertTrue(summary["mcp_runtime"]["ok"])
            self.assertFalse(summary["mcp_runtime"]["fatal"])
            self.assertEqual(
                summary["mcp_runtime"]["classification"],
                "nonfatal_recovered_artifacts",
            )
            self.assertTrue(summary["poc_crash_found"])
            self.assertEqual(summary["submitted_flag"], "flag{abc123}")

    def test_llm_call_budget_summary_marks_exceeded(self):
        task = type("Task", (), {"opensage_session": None})()

        summary = opensage_cybingym_runner._llm_call_budget_summary(
            configured_max_llm_calls=600,
            cost_info={"num_llm_calls": 601},
            task=task,
        )

        self.assertFalse(summary["ok"])
        self.assertTrue(summary["exceeded"])
        self.assertEqual(summary["completed_llm_calls"], 601)

    def test_llm_call_budget_summary_reads_budget_usage(self):
        task = type("Task", (), {"opensage_session": None})()

        summary = opensage_cybingym_runner._llm_call_budget_summary(
            configured_max_llm_calls=600,
            cost_info={
                "budget": {
                    "per_model_usage": {
                        "model-a": {"calls": 250},
                        "model-b": {"calls": 350},
                    }
                }
            },
            task=task,
        )

        self.assertTrue(summary["ok"])
        self.assertFalse(summary["exceeded"])
        self.assertTrue(summary["exhausted"])
        self.assertEqual(summary["completed_llm_calls"], 600)

    def test_session_llm_call_limit_blocks_after_configured_calls(self):
        try:
            from opensage.llm.budget import BudgetExhaustedError
        except ModuleNotFoundError:
            self.skipTest("OpenSAGE budget module is not installed")

        class FakeBudget:
            budget_exhausted = False
            exhausted_reason = None

            def __init__(self):
                self.original_check_calls = 0

            def check_available(self):
                self.original_check_calls += 1

        budget = FakeBudget()
        session = type("Session", (), {"budget": budget})()

        opensage_cybingym_runner._configure_session_llm_call_limit(session, 2)

        budget.check_available()
        budget.check_available()
        with self.assertRaises(BudgetExhaustedError):
            budget.check_available()

        self.assertEqual(budget.original_check_calls, 3)
        self.assertEqual(budget._cybingym_llm_calls_started, 2)
        self.assertTrue(budget.budget_exhausted)
        self.assertEqual(budget.exhausted_reason, "llm_call_budget_exhausted")



if __name__ == "__main__":
    unittest.main()
