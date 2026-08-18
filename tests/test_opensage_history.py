from __future__ import annotations

import contextlib
import io
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
    bridge_extra: dict | None = None,
    result_extra: dict | None = None,
    log_text: str = "",
    task_error: bool = False,
) -> Path:
    run_dir = output_dir / sample_id / stamp
    run_dir.mkdir(parents=True)
    bridge = {
        "model": model,
        "provider": _provider(model),
        "status": status,
        "returncode": returncode,
        "poc_exists": poc_found,
    }
    bridge.update(bridge_extra or {})
    (run_dir / "opensage_bridge_status.json").write_text(
        json.dumps(bridge),
        encoding="utf-8",
    )
    result = {"poc_found": poc_found}
    result.update(result_extra or {})
    (run_dir / "cybingym_result.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )
    (run_dir / "evaluation_master.log").write_text(log_text, encoding="utf-8")
    if task_error:
        error_dir = run_dir / f"cybingym_{sample_id}"
        error_dir.mkdir()
        (error_dir / "error.json").write_text(
            json.dumps({"error": "sample failed"}),
            encoding="utf-8",
        )
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


class OpenSageFailureCategoryTests(unittest.TestCase):
    def test_history_classifies_failure_categories_and_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "opensage"
            log_dir = root / "logs"
            log_dir.mkdir()
            model = "openai/gpt-5"

            _write_run(
                output_dir,
                "1",
                "260101_000001",
                model=model,
                poc_found=False,
                result_extra={
                    "llm_call_budget": {
                        "configured_max_llm_calls": 600,
                        "started_llm_calls": 600,
                        "exhausted": True,
                        "exhausted_reason": "llm_call_budget_exhausted",
                    }
                },
            )
            _write_run(
                output_dir,
                "2",
                "260101_000002",
                model=model,
                poc_found=False,
                result_extra={
                    "mcp_runtime": {
                        "ok": False,
                        "fatal": True,
                        "classification": "fatal_required_mcp_failure",
                        "total": 1,
                    }
                },
            )
            _write_run(
                output_dir,
                "3",
                "260101_000003",
                model=model,
                poc_found=False,
                log_text="2026-01-01 | ERROR    | LiteLLM api.openai.com failed\n",
            )
            _write_run(
                output_dir,
                "4",
                "260101_000004",
                model=model,
                status="error",
                returncode=1,
                poc_found=False,
            )
            clean_unsolved = _write_run(
                output_dir,
                "5",
                "260101_000005",
                model=model,
                poc_found=True,
            )
            _write_score(clean_unsolved, sample_id="5", model=model, value="I")

            histories = opensage_history.build_opensage_history(
                output_dir=output_dir,
                log_dir=log_dir,
                sample_ids={"1", "2", "3", "4", "5"},
            )

            self.assertEqual(
                histories["1"].failure_category,
                opensage_history.FAILURE_LLM_BUDGET_EXHAUSTED,
            )
            self.assertEqual(
                histories["2"].failure_category,
                opensage_history.FAILURE_TOOLING_ERROR,
            )
            self.assertEqual(
                histories["3"].failure_category,
                opensage_history.FAILURE_LLM_API_ERROR,
            )
            self.assertEqual(
                histories["4"].failure_category,
                opensage_history.FAILURE_SYSTEM_ERROR,
            )
            self.assertEqual(
                histories["5"].outcome,
                opensage_history.OUTCOME_UNSOLVED,
            )
            self.assertEqual(
                histories["5"].failure_category,
                opensage_history.FAILURE_AGENT_CAPABILITY,
            )

            counts = opensage_history.failure_counts_for_histories(histories)
            self.assertEqual(counts[opensage_history.FAILURE_LLM_BUDGET_EXHAUSTED], 1)
            self.assertEqual(counts[opensage_history.FAILURE_TOOLING_ERROR], 1)
            self.assertEqual(counts[opensage_history.FAILURE_LLM_API_ERROR], 1)
            self.assertEqual(counts[opensage_history.FAILURE_SYSTEM_ERROR], 1)
            self.assertEqual(counts[opensage_history.FAILURE_AGENT_CAPABILITY], 1)

            selected = opensage_history.filter_histories(
                histories,
                "all",
                failure_category="llm-budget-exhausted",
            )
            self.assertEqual(selected, {"1"})
            text = opensage_history.format_history_summary(
                histories,
                selected_ids=selected,
                failure_category="llm_budget_exhausted",
            )
            self.assertIn("Failure categories:", text)
            self.assertIn("- 1 [llm_budget_exhausted]", text)

    def test_cli_filters_by_failure_category(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_dir = root / "opensage"
            log_dir = root / "logs"
            summary_path = root / "summary.json"
            log_dir.mkdir()
            model = "openai/gpt-5"

            _write_run(
                output_dir,
                "1",
                "260101_000001",
                model=model,
                poc_found=False,
                result_extra={
                    "llm_call_budget": {
                        "configured_max_llm_calls": 3,
                        "completed_llm_calls": 3,
                    }
                },
            )
            _write_run(
                output_dir,
                "2",
                "260101_000002",
                model=model,
                status="error",
                returncode=1,
                poc_found=False,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                rc = opensage_history.main(
                    [
                        "--output-dir",
                        str(output_dir),
                        "--log-dir",
                        str(log_dir),
                        "--sample-ids",
                        "1,2",
                        "--filter",
                        "all",
                        "--failure-category",
                        "llm_budget_exhausted",
                        "--write",
                        str(summary_path),
                    ]
                )

            self.assertEqual(rc, 0)
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(data["failure_category"], "llm_budget_exhausted")
            self.assertEqual(data["selected_ids"], ["1"])
            self.assertEqual(
                data["failure_counts"]["llm_budget_exhausted"],
                1,
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

            with contextlib.redirect_stdout(io.StringIO()):
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
