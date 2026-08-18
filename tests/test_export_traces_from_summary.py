from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import export_traces_from_summary as exporter


def _write_csv(root: Path, rows: list[dict[str, str]]) -> Path:
    csv_path = root / "summary.csv"
    fieldnames = [
        "id",
        "poc",
        "exploit",
        "selected_agent_type",
        "selected_model",
        "selected_source",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


class ExportTracesFromSummaryTests(unittest.TestCase):
    def test_exports_inspect_sample_trace_from_eval_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "run.eval"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(
                    "_journal/start.json",
                    json.dumps({"eval": {"model": "openai/gpt-5.6"}}),
                )
                archive.writestr(
                    "samples/19902_epoch_1.json",
                    json.dumps({"id": "19902", "messages": [{"content": "trace"}]}),
                )
                archive.writestr(
                    "_journal/summaries/1.json",
                    json.dumps([{"id": "19902", "scores": {}}]),
                )
            csv_path = _write_csv(
                root,
                [
                    {
                        "id": "19902",
                        "poc": "true",
                        "exploit": "true",
                        "selected_agent_type": "codex",
                        "selected_model": "openai/gpt-5.6",
                        "selected_source": "run.eval",
                    }
                ],
            )
            output_dir = root / "traces"

            exit_code = exporter.main(
                [
                    "--csv",
                    str(csv_path),
                    "--source-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                ]
            )

            self.assertEqual(exit_code, 0)
            trace_path = output_dir / "codex" / "openai_gpt-5.6" / "19902" / "sample_trace.json"
            self.assertTrue(trace_path.exists())
            self.assertEqual(json.loads(trace_path.read_text(encoding="utf-8"))["id"], "19902")
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest[0]["trace_kind"], "inspect_sample")

    def test_exports_opensage_session_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "evals" / "opensage_inspect" / "11504" / "run"
            task_dir = run_dir / "cybingym_11504"
            task_dir.mkdir(parents=True)
            (run_dir / "inspect_score.json").write_text(
                json.dumps({"sample_id": "11504"}),
                encoding="utf-8",
            )
            (task_dir / "session_trace.json").write_text(
                json.dumps({"events": ["trace"]}),
                encoding="utf-8",
            )
            (task_dir / "execution_debug.log").write_text(
                "large debug log",
                encoding="utf-8",
            )
            (task_dir / "metadata.json").write_text(
                json.dumps({"large": "metadata"}),
                encoding="utf-8",
            )
            (task_dir / "cost_info.json").write_text(
                json.dumps({"estimated_cost": 1.0}),
                encoding="utf-8",
            )
            csv_path = _write_csv(
                root,
                [
                    {
                        "id": "11504",
                        "poc": "true",
                        "exploit": "false",
                        "selected_agent_type": "opensage",
                        "selected_model": "moonshot/kimi-k3",
                        "selected_source": str(run_dir),
                    }
                ],
            )
            output_dir = root / "traces"

            exit_code = exporter.main(
                [
                    "--csv",
                    str(csv_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )

            self.assertEqual(exit_code, 0)
            trace_path = (
                output_dir
                / "opensage"
                / "moonshot_kimi-k3"
                / "11504"
                / "cybingym_11504"
                / "session_trace.json"
            )
            self.assertTrue(trace_path.exists())
            self.assertTrue(
                (
                    output_dir
                    / "opensage"
                    / "moonshot_kimi-k3"
                    / "11504"
                    / "run"
                    / "inspect_score.json"
                ).exists()
            )
            self.assertFalse(
                (
                    output_dir
                    / "opensage"
                    / "moonshot_kimi-k3"
                    / "11504"
                    / "cybingym_11504"
                    / "execution_debug.log"
                ).exists()
            )
            self.assertFalse(
                (
                    output_dir
                    / "opensage"
                    / "moonshot_kimi-k3"
                    / "11504"
                    / "cybingym_11504"
                    / "metadata.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
