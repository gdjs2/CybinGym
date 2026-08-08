from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from solvers import opensage_history


def _provider(model: str) -> str | None:
    return model.split("/", 1)[0] if "/" in model else None


def _write_run(
    output_dir: Path,
    sample_id: str,
    stamp: str,
    *,
    model: str,
    status: str = "finished",
    returncode: int = 0,
    poc_found: bool = True,
) -> Path:
    run_dir = output_dir / sample_id / stamp
    run_dir.mkdir(parents=True)
    (run_dir / "opensage_bridge_status.json").write_text(
        json.dumps(
            {
                "model": model,
                "provider": _provider(model),
                "status": status,
                "returncode": returncode,
                "poc_exists": poc_found,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "cybingym_result.json").write_text(
        json.dumps({"poc_found": poc_found}),
        encoding="utf-8",
    )
    (run_dir / "evaluation_master.log").write_text("", encoding="utf-8")
    return run_dir


def _write_score(run_dir: Path, *, sample_id: str, model: str, value: str) -> None:
    (run_dir / "inspect_score.json").write_text(
        json.dumps(
            {
                "sample_id": sample_id,
                "value": value,
                "model": model,
                "provider": _provider(model),
                "completed": True,
            }
        ),
        encoding="utf-8",
    )


class OpenSageHistoryPerModelTests(unittest.TestCase):
    def test_per_model_summary_separates_model_outcomes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "opensage"
            log_dir = root / "logs"
            log_dir.mkdir()

            solved_run = _write_run(
                output_dir,
                "1",
                "260101_000001",
                model="moonshot/kimi-k3",
            )
            _write_score(
                solved_run,
                sample_id="1",
                model="moonshot/kimi-k3",
                value="C",
            )
            _write_run(
                output_dir,
                "2",
                "260101_000002",
                model="openai/gpt-5",
                status="error",
                returncode=1,
                poc_found=False,
            )

            summary = opensage_history.build_per_model_history_summary(
                output_dir=output_dir,
                log_dir=log_dir,
                sample_ids={"1", "2", "3"},
                mode="errors",
            )

            self.assertEqual(
                set(summary["models"]),
                {"moonshot/kimi-k3", "openai/gpt-5"},
            )
            self.assertEqual(
                summary["models"]["moonshot/kimi-k3"]["counts"]["solved"],
                1,
            )
            self.assertEqual(
                summary["models"]["moonshot/kimi-k3"]["selected_ids"],
                [],
            )
            self.assertEqual(
                summary["models"]["openai/gpt-5"]["counts"]["error"],
                1,
            )
            self.assertEqual(
                summary["models"]["openai/gpt-5"]["selected_ids"],
                ["2"],
            )

            text = opensage_history.format_per_model_history_summary(summary)
            self.assertIn("OpenSAGE per-model history summary", text)
            self.assertIn("Model: moonshot/kimi-k3", text)
            self.assertIn("Model: openai/gpt-5", text)

    def test_cli_writes_per_model_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "opensage"
            log_dir = root / "logs"
            summary_path = root / "summary.json"
            log_dir.mkdir()

            run_dir = _write_run(
                output_dir,
                "1",
                "260101_000001",
                model="moonshot/kimi-k3",
                status="error",
                returncode=1,
                poc_found=False,
            )
            _write_score(
                run_dir,
                sample_id="1",
                model="moonshot/kimi-k3",
                value="I",
            )

            rc = opensage_history.main(
                [
                    "--output-dir",
                    str(output_dir),
                    "--log-dir",
                    str(log_dir),
                    "--sample-ids",
                    "1,2",
                    "--filter",
                    "errors",
                    "--per-model",
                    "--write",
                    str(summary_path),
                ]
            )

            self.assertEqual(rc, 0)
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(data["model_count"], 1)
            self.assertEqual(
                data["models"]["moonshot/kimi-k3"]["selected_ids"],
                ["1"],
            )


if __name__ == "__main__":
    unittest.main()
