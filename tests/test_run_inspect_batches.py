from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_inspect_batches.py"
SPEC = importlib.util.spec_from_file_location("run_inspect_batches", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
run_inspect_batches = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_inspect_batches)


class RunInspectBatchesTests(unittest.TestCase):
    def test_batches_split_ids(self):
        self.assertEqual(
            run_inspect_batches.batches(["1", "2", "3", "4", "5"], 2),
            [["1", "2"], ["3", "4"], ["5"]],
        )

    def test_split_ids_accepts_commas_and_lines(self):
        self.assertEqual(
            run_inspect_batches.split_ids("1,2\n3\n# comment\n4"),
            ["1", "2", "3", "4"],
        )

    def test_resolve_sample_ids_from_dataset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset = Path(temp_dir) / "dataset.json"
            dataset.write_text('[{"id": 1}, {"id": "2"}]')
            self.assertEqual(
                run_inspect_batches.resolve_sample_ids("all", dataset),
                ["1", "2"],
            )

    def test_build_eval_command(self):
        command = run_inspect_batches.build_eval_command(
            uv="uv",
            uv_extras=["opensage"],
            inspect_bin="inspect",
            task="cybingym.py",
            batch_ids=["1", "2"],
            inspect_args=[
                "-T",
                "agent_type=kimi_code",
                "--model",
                "moonshot/kimi-k3",
            ],
        )
        self.assertEqual(
            command,
            [
                "uv",
                "run",
                "--extra",
                "opensage",
                "inspect",
                "eval",
                "cybingym.py",
                "--sample-id",
                "1,2",
                "-T",
                "agent_type=kimi_code",
                "--model",
                "moonshot/kimi-k3",
            ],
        )


if __name__ == "__main__":
    unittest.main()
