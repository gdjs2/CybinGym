from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import scorer
from inspect_ai.scorer import Target
from inspect_ai.solver import TaskState


class FakeSandbox:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    async def read_file(self, path: str, text: bool = False):
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if text:
            return content.decode("utf-8")
        return content


def make_state() -> TaskState:
    return TaskState(
        model="openai/gpt-5.6",
        sample_id="sample/1",
        epoch=1,
        input="",
        messages=[],
        metadata={
            "target_binary": "bin/target",
            "valid_image_vul": "vul-img",
            "valid_image_fix": "fix-img",
        },
    )


class ScorerPocArtifactTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_poc_artifacts_writes_raw_bytes_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sandbox = FakeSandbox(
                {
                    "/CybinGym_workdir/poc_crash": b"crash-bytes",
                    "/CybinGym_workdir/poc": b"exploit-bytes",
                }
            )

            with patch.object(scorer, "sandbox", return_value=sandbox):
                status = await scorer._save_poc_artifacts(
                    make_state(),
                    poc_artifact_dir=str(root),
                )

            self.assertEqual(status["errors"], [])
            self.assertEqual(status["missing"], [])
            self.assertEqual(len(status["saved"]), 2)

            sample_dir = root / "openai_gpt-5.6" / "sample_1" / "epoch-1"
            self.assertEqual((sample_dir / "poc_crash").read_bytes(), b"crash-bytes")
            self.assertEqual((sample_dir / "poc").read_bytes(), b"exploit-bytes")

            metadata = json.loads(
                (sample_dir / "poc_crash.metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["artifact"], "poc_crash")
            self.assertEqual(metadata["source_path"], "/CybinGym_workdir/poc_crash")
            self.assertEqual(metadata["sample_id"], "sample/1")
            self.assertEqual(metadata["sample_uuid"], "")
            self.assertEqual(metadata["model"], "openai/gpt-5.6")
            self.assertEqual(metadata["size_bytes"], len(b"crash-bytes"))
            self.assertEqual(metadata["target_binary"], "bin/target")

    async def test_save_poc_artifacts_uses_environment_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FakeSandbox({"/CybinGym_workdir/poc_crash": b"crash"})
            with patch.dict(os.environ, {scorer.POC_ARTIFACT_DIR_ENV: tmpdir}), patch.object(
                scorer, "sandbox", return_value=sandbox
            ):
                status = await scorer._save_poc_artifacts(
                    make_state(),
                    artifact_names=("poc_crash",),
                )

            self.assertTrue(status["enabled"])
            self.assertEqual(len(status["saved"]), 1)
            self.assertTrue(
                (
                    Path(tmpdir)
                    / "openai_gpt-5.6"
                    / "sample_1"
                    / "epoch-1"
                    / "poc_crash"
                ).exists()
            )

    async def test_full_scorer_includes_capture_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sandbox = FakeSandbox(
                {
                    "/CybinGym_workdir/poc_crash": b"crash",
                    "/CybinGym_workdir/poc": b"exploit",
                }
            )
            with patch.object(scorer, "sandbox", return_value=sandbox), patch.object(
                scorer, "_score_crash_test", new=AsyncMock(return_value=("C", "crash ok"))
            ):
                score_fn = scorer.cybingym_scorer(poc_artifact_dir=tmpdir)
                score = await score_fn(make_state(), Target("expected-flag"))

            status = score.metadata["poc_artifacts"]
            self.assertEqual(len(status["saved"]), 2)
            self.assertIn("PoC Artifact Capture", score.explanation)
            self.assertIn("poc_crash: saved", score.explanation)
            self.assertIn("poc: saved", score.explanation)


if __name__ == "__main__":
    unittest.main()
