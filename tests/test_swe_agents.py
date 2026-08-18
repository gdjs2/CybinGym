import importlib
import json
import unittest
from unittest.mock import patch

from solvers import swe_agents


class SweAgentConfigurationTests(unittest.TestCase):
    def assert_shared_bridge(self, bridged_tools):
        self.assertEqual(len(bridged_tools), 2)
        bash_bridge = bridged_tools[0]
        self.assertEqual(bash_bridge.name, "bash_tools_for_target_and_defaults")
        self.assertEqual(len(bash_bridge.tools), 2)
        validation_bridge = bridged_tools[1]
        self.assertEqual(validation_bridge.name, "cybingym_crash_validation")
        self.assertEqual(len(validation_bridge.tools), 1)

    def test_codex_configuration_preserves_model_alignment(self):
        sentinel = object()
        with patch.object(swe_agents, "codex_cli", return_value=sentinel) as agent:
            solvers = swe_agents.codex_cli_solver()

        self.assertIs(solvers[1], sentinel)
        kwargs = agent.call_args.kwargs
        self.assertEqual(kwargs["model_config"], "gpt-5.6")
        self.assertEqual(kwargs["web_search"], "disabled")
        self.assertFalse(kwargs["goals"])
        self.assert_shared_bridge(kwargs["bridged_tools"])

    def test_claude_code_uses_shared_bridge(self):
        sentinel = object()
        with patch.object(swe_agents, "claude_code", return_value=sentinel) as agent:
            solvers = swe_agents.claude_code_solver()

        self.assertIs(solvers[1], sentinel)
        kwargs = agent.call_args.kwargs
        self.assertEqual(kwargs["disallowed_tools"], ["Bash", "WebSearch"])
        self.assert_shared_bridge(kwargs["bridged_tools"])

    def test_kimi_code_uses_shared_bridge_and_pinned_version(self):
        sentinel = object()
        with patch.object(swe_agents, "kimi_code", return_value=sentinel) as agent:
            solvers = swe_agents.kimi_code_solver(version="0.29.0")

        self.assertIs(solvers[1], sentinel)
        kwargs = agent.call_args.kwargs
        self.assertEqual(kwargs["version"], "0.29.0")
        self.assertEqual(kwargs["disallowed_tools"], ["WebSearch", "FetchURL"])
        self.assert_shared_bridge(kwargs["bridged_tools"])

    def test_crash_level_selects_crash_prompt(self):
        self.assertIs(
            swe_agents._prompt_for_level("crash"),
            importlib.import_module("solvers.prompts").crash_prompt,
        )


class CrashValidationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_validate_crash_poc_uses_fixed_path_and_metadata(self):
        expected_store_key = swe_agents.CRASH_VALIDATION_STORE_KEY

        class FakeStore:
            def get(self, key):
                assert key == expected_store_key
                return {
                    "valid_image_vul": "vul-img",
                    "valid_image_fix": "fix-img",
                    "target_binary": "bin/target",
                }

        class FakeSandbox:
            async def read_file(self, path, text=False):
                assert path == "/CybinGym_workdir/poc_crash"
                assert text is False
                return b"poc-bytes"

        def fake_validation(image, target_binary, poc_path):
            self.assertEqual(target_binary, "bin/target")
            with open(poc_path, "rb") as handle:
                self.assertEqual(handle.read(), b"poc-bytes")
            if image == "vul-img":
                return {"returncode": 1, "timed_out": False, "explanation": "boom"}
            return {"returncode": 0, "timed_out": False, "explanation": "ok"}

        with patch.object(swe_agents, "store", return_value=FakeStore()), patch.object(
            swe_agents, "sandbox", return_value=FakeSandbox()
        ), patch.object(swe_agents, "run_docker_validation", side_effect=fake_validation):
            result = json.loads(await swe_agents._validate_crash_poc())

        self.assertTrue(result["ok"])
        self.assertEqual(result["poc_path"], "/CybinGym_workdir/poc_crash")
        self.assertEqual(result["vulnerable"]["returncode"], 1)
        self.assertEqual(result["fixed"]["returncode"], 0)


class KimiSolverSelectionTests(unittest.TestCase):
    def test_kimi_agent_type_forwards_binary_version_and_evaluation_level(self):
        cybingym_module = importlib.import_module("cybingym")
        sentinel = object()
        with patch.object(
            swe_agents, "kimi_code_solver", return_value=sentinel
        ) as solver:
            selected = cybingym_module._select_solver(
                agent_type="kimi_code",
                kimi_code_version="0.29.0",
                opensage_agent_dir="",
                opensage_output_dir="",
                opensage_max_llm_calls=0,
                opensage_max_workers=1,
                opensage_timeout=1,
                opensage_cleanup_grace=1,
                opensage_llm_retry_timeout=1,
                opensage_llm_retry_count=1,
                opensage_python="python",
                opensage_source_dir="",
                opensage_model="",
                opensage_provider="",
                opensage_reasoning_effort="",
                opensage_artifact_collection_mode="minimal",
                opensage_extend_from_run_dir="",
                opensage_base_port=20000,
                opensage_port_stride=10,
                evaluation_level="crash",
            )

        self.assertIs(selected, sentinel)
        solver.assert_called_once_with(version="0.29.0", evaluation_level="crash")


class EvaluationLevelTests(unittest.TestCase):
    def test_crash_level_rejects_non_cli_agents_before_dataset_load(self):
        cybingym_module = importlib.import_module("cybingym")
        with self.assertRaisesRegex(ValueError, "supported only"):
            cybingym_module.cybingym(agent_type="basic", evaluation_level="crash")

    def test_crash_sample_omits_victim_service(self):
        cybingym_module = importlib.import_module("cybingym")
        sample = cybingym_module.create_binary_sample(
            prompt="prompt",
            prebuilt_base_image="base-image",
            evaluation_level="crash",
        )
        services = sample.sandbox.config.services
        self.assertEqual(set(services), {"default", "target"})

    def test_full_sample_includes_victim_service(self):
        cybingym_module = importlib.import_module("cybingym")
        sample = cybingym_module.create_binary_sample(
            prompt="prompt",
            prebuilt_base_image="base-image",
            evaluation_level="full",
        )
        services = sample.sandbox.config.services
        self.assertIn("victim", services)


if __name__ == "__main__":
    unittest.main()
