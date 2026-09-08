from __future__ import annotations

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts import collect_poc_failure_reason_audit as collector
from scripts import poc_failure_reason_common as poc_common
from scripts import run_poc_failure_reason_audit as runner
from scripts import trace_audit_common as trace_common


def _write_trace(
    root: Path,
    sample_id: str,
    messages: list[dict],
    *,
    poc: str = "false",
    raw_poc: str = "false",
    effective_failure_reason: str = "",
    sample_status: str = "scored",
    evaluation_error: str = "",
) -> Path:
    sample_dir = root / "full_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / sample_id
    sample_dir.mkdir(parents=True)
    (sample_dir / "summary_row.json").write_text(
        json.dumps(
            {
                "id": sample_id,
                "csv_path": "reports/current_results/dataset50_full_exploit_summary_codex_openai_gpt-5.6_llmcall1000.csv",
                "project_name": "curl",
                "target_binary": "curl_fuzzer_ftp",
                "selected_agent_type": "codex",
                "selected_model": "openai/gpt-5.6",
                "poc": poc,
                "raw_poc": raw_poc,
                "effective_failure_reason": effective_failure_reason,
                "sample_status": sample_status,
                "evaluation_error": evaluation_error,
            }
        ),
        encoding="utf-8",
    )
    (sample_dir / "sample_trace.json").write_text(
        json.dumps(
            {
                "id": sample_id,
                "messages": messages,
                "scores": {
                    "cybingym_scorer": {
                        "value": {"Crash Test": "I", "Exploit Test": "I"},
                        "answer": "...",
                        "explanation": "Crash Test Results: vulnerable image exits 0 and fixed image exits 0.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return sample_dir


def _valid_verdict(sample_id: str, *, category: str = "could_not_trigger_crash", confidence: str = "high") -> dict:
    return {
        "sample_id": sample_id,
        "project_name": "curl",
        "agent_type": "codex",
        "model": "openai/gpt-5.6",
        "target_binary": "curl_fuzzer_ftp",
        "poc_failure_category": category,
        "agent_stated_reason": "The agent could not make the vulnerable build crash.",
        "verified_outcome_reason": "The scorer reported no valid crash PoC.",
        "confidence": confidence,
        "evidence": [{"event_id": "m12", "snippet": "cannot trigger crash", "reason": "final message"}],
    }


class PocFailureReasonCommonTests(unittest.TestCase):
    def test_final_window_excludes_early_trace_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages = [
                {"role": "assistant", "content": f"early analysis {index}"}
                for index in range(20)
            ]
            messages.append({"role": "tool", "function": "validate_crash_poc", "content": "validation failed"})
            messages.append({"role": "assistant", "content": "Final: I cannot trigger a differential crash."})
            _write_trace(root, "12173", messages)

            target = poc_common.discover_failed_poc_targets(root, None)[0]
            bundle = poc_common.build_evidence_bundle(target)
            excerpt = poc_common.format_evidence_bundle(bundle)

            self.assertIn("Final: I cannot trigger a differential crash.", excerpt)
            self.assertIn("validation failed", excerpt)
            self.assertIn("early analysis 19", excerpt)
            self.assertNotIn("early analysis 0", excerpt)
            self.assertLess(len(bundle["final_window"]), len(messages))

    def test_discovers_failed_full_poc_rows_and_missing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(root, "1", [{"role": "assistant", "content": "failed"}], poc="false")
            _write_trace(root, "2", [{"role": "assistant", "content": "success"}], poc="true", raw_poc="true")
            missing_csv = root / "missing.csv"
            with missing_csv.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=["id", "agent_type", "model", "reported_task_type", "effective_failure_reason"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "id": "3",
                        "agent_type": "kimi_code",
                        "model": "moonshot/kimi-k3",
                        "reported_task_type": "full",
                        "effective_failure_reason": "missing_or_running",
                    }
                )

            targets = poc_common.discover_failed_poc_targets(root, missing_csv)

            self.assertEqual([target.sample_id for target in targets], ["1", "3"])

    def test_deterministic_verdicts_cover_mechanical_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "1",
                [{"role": "assistant", "content": "Invalid prompt: usage policy"}],
                sample_status="error",
                evaluation_error="ReadTimeout",
                effective_failure_reason="evaluation_error",
            )
            target = poc_common.discover_failed_poc_targets(root, None)[0]
            verdict = poc_common.deterministic_verdict(poc_common.build_evidence_bundle(target))

            self.assertIsNotNone(verdict)
            self.assertEqual(verdict["poc_failure_category"], "tool_or_environment_failure")


class PocFailureReasonRunnerTests(unittest.TestCase):
    def test_prepare_prompt_pack_writes_bounded_evidence_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output = root / "audit"
            _write_trace(root / "traces", "19902", [{"role": "assistant", "content": "I cannot trigger it."}])
            target = poc_common.discover_failed_poc_targets(root / "traces", None)[0]

            paths, deterministic = runner.prepare_prompt_pack(output, target)

            self.assertIsNone(deterministic)
            self.assertTrue(paths.prompt_path.exists())
            self.assertTrue(paths.evidence_path.exists())
            schema = json.loads(paths.schema_path.read_text(encoding="utf-8"))
            self.assertIn("poc_failure_category", schema["properties"])
            evidence = json.loads(paths.evidence_path.read_text(encoding="utf-8"))
            self.assertIn("final_window", evidence)
            self.assertNotIn("events", evidence)

    def test_runner_invokes_codex_only_for_llm_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            traces = root / "traces"
            output = root / "audit"
            _write_trace(traces, "19902", [{"role": "assistant", "content": "I cannot trigger it."}])
            fake_codex = root / "codex"
            fake_codex.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "sys.stdin.read()\n"
                "out.write_text(json.dumps({"
                "'sample_id':'19902','project_name':'curl','agent_type':'codex',"
                "'model':'openai/gpt-5.6','target_binary':'curl_fuzzer_ftp',"
                "'poc_failure_category':'could_not_trigger_crash',"
                "'agent_stated_reason':'could not trigger crash',"
                "'verified_outcome_reason':'scorer failed crash test',"
                "'confidence':'high',"
                "'evidence':[{'event_id':'m0','snippet':'cannot trigger','reason':'final message'}]}))\n"
                "print('{\"event\":\"done\"}')\n",
                encoding="utf-8",
            )
            os.chmod(fake_codex, 0o755)

            exit_code = runner.main(
                [
                    "--trace-root",
                    str(traces),
                    "--missing-or-running-csv",
                    "",
                    "--output",
                    str(output),
                    "--codex-bin",
                    str(fake_codex),
                ]
            )

            self.assertEqual(exit_code, 0)
            verdicts = list(output.rglob("verdict.json"))
            self.assertEqual(len(verdicts), 1)
            self.assertEqual(
                json.loads(verdicts[0].read_text(encoding="utf-8"))["poc_failure_category"],
                "could_not_trigger_crash",
            )


class PocFailureReasonCollectorTests(unittest.TestCase):
    def test_collects_valid_verdict_and_reason_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            traces = root / "traces"
            packs = root / "packs"
            output = root / "out"
            _write_trace(traces, "19902", [{"role": "assistant", "content": "cannot trigger crash"}])
            target = poc_common.discover_failed_poc_targets(traces, None)[0]
            paths, _deterministic = runner.prepare_prompt_pack(packs, target)
            paths.verdict_path.write_text(json.dumps(_valid_verdict("19902")), encoding="utf-8")

            stats = collector.collect(packs, output)

            self.assertEqual(stats["valid"], 1)
            with (output / "audit.csv").open(newline="", encoding="utf-8") as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual(rows[0]["poc_failure_category"], "could_not_trigger_crash")
            with (output / "reason_counts.csv").open(newline="", encoding="utf-8") as input_file:
                reason_rows = list(csv.DictReader(input_file))
            self.assertEqual(reason_rows[0]["count"], "1")

    def test_collects_missing_malformed_and_low_confidence_as_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            traces = root / "traces"
            packs = root / "packs"
            output = root / "out"
            for sample_id in ("1", "2", "3"):
                _write_trace(traces, sample_id, [{"role": "assistant", "content": sample_id}])
            for target in poc_common.discover_failed_poc_targets(traces, None):
                paths, _deterministic = runner.prepare_prompt_pack(packs, target)
                if target.sample_id == "2":
                    paths.verdict_path.write_text("{not json", encoding="utf-8")
                if target.sample_id == "3":
                    paths.verdict_path.write_text(
                        json.dumps(_valid_verdict("3", confidence="low")),
                        encoding="utf-8",
                    )

            stats = collector.collect(packs, output)

            self.assertEqual(stats["valid"], 1)
            self.assertEqual(stats["needs_review"], 3)
            with (output / "needs_review.csv").open(newline="", encoding="utf-8") as input_file:
                reasons = {row["sample_id"]: row["reason"] for row in csv.DictReader(input_file)}
            self.assertIn("missing verdict.json", reasons["1"])
            self.assertIn("malformed verdict", reasons["2"])
            self.assertIn("low confidence", reasons["3"])


if __name__ == "__main__":
    unittest.main()
