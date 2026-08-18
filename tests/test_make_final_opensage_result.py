from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from scripts import make_final_opensage_result as final_result


def _candidate(value: object, completed_at: str, log_name: str) -> final_result.CandidateRow:
    return final_result.CandidateRow(
        row={
            "id": "1",
            "completed": True,
            "completed_at": completed_at,
            "scores": {final_result.DEFAULT_SCORER: {"value": value}},
        },
        log_path=Path(log_name),
        model="openai/gpt-5",
        timestamp=completed_at,
    )


class MakeFinalOpenSageResultTests(unittest.TestCase):
    def test_row_rank_prefers_exploit_success_over_newer_poc_success(self) -> None:
        newer_poc_only = _candidate(
            {"Crash Test": "C", "Exploit Test": "I"},
            "2026-01-02T00:00:00",
            "poc.eval",
        )
        older_exploit = _candidate(
            {"Crash Test": "I", "Exploit Test": "C"},
            "2026-01-01T00:00:00",
            "exploit.eval",
        )

        self.assertGreater(
            final_result._row_rank(older_exploit, final_result.DEFAULT_SCORER),
            final_result._row_rank(newer_poc_only, final_result.DEFAULT_SCORER),
        )

    def test_score_values_maps_legacy_scalar_to_both_columns(self) -> None:
        row = {"scores": {final_result.DEFAULT_SCORER: {"value": "C"}}}

        self.assertEqual(
            final_result._score_values(row, final_result.DEFAULT_SCORER),
            {"poc": "C", "exploit": "C"},
        )

    def test_rewrite_csv_costs_counts_poc_and_exploit_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_csv = Path(tmpdir) / "summary.csv"
            with output_csv.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(
                    output_file,
                    fieldnames=["id", "poc", "exploit", "cost_usd"],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {"id": "1", "poc": "true", "exploit": "false", "cost_usd": "0"},
                        {"id": "2", "poc": "false", "exploit": "true", "cost_usd": "0"},
                        {"id": "3", "poc": "", "exploit": "", "cost_usd": ""},
                    ]
                )

            selected = {
                "1": final_result.CandidateRow(
                    row={"id": "1"},
                    log_path=Path("one.eval"),
                    model="openai/gpt-5",
                    timestamp="",
                    estimated_cost=1.25,
                ),
                "2": final_result.CandidateRow(
                    row={"id": "2"},
                    log_path=Path("two.eval"),
                    model="openai/gpt-5",
                    timestamp="",
                    estimated_cost=None,
                ),
            }

            stats = final_result._rewrite_csv_costs_from_cost_info(
                output_csv=output_csv,
                selected=selected,
            )

            self.assertEqual(stats["matched"], 2)
            self.assertEqual(stats["poc"], 1)
            self.assertEqual(stats["not_poc"], 1)
            self.assertEqual(stats["exploit"], 1)
            self.assertEqual(stats["not_exploit"], 1)
            self.assertEqual(stats["missing_from_log"], 1)
            self.assertEqual(stats["missing_cost_ids"], ["2"])
            self.assertEqual(stats["total_cost_usd"], 1.25)


if __name__ == "__main__":
    unittest.main()
