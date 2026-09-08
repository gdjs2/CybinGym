# Current results

The September 5 Kimi full-evaluation batch covers the other 50 binaries in
`dataset.d/dataset100.selection.csv`, with no overlap with the original batch.
The combined Kimi full-evaluation report is
`dataset100_full_exploit_summary_kimi_code_moonshot_kimi-k3_llmcall1000_missingfailed.csv`.
The original 50-row reports remain available as baseline artifacts.

| Evaluation | Agent | Tasks | PoC successes | Exploit successes |
| --- | --- | ---: | ---: | ---: |
| Crash only | Codex | 100 | 84 (84%) | — |
| Crash only | Kimi Code | 100 | 66 (66%) | — |
| Full | Codex | 50 | 38 (76%) | 9 (18%) |
| Full | Kimi Code | 100 | 54 (54%) | 3 (3%) |

Codex and Kimi full-evaluation totals cover different sample sets. The category
and difficulty tables provide separate task counts for each agent. The addendum
tables, `category_results_additional.tex` and `difficulty_results_additional.tex`,
show the previous 50-sample Kimi full run, the additional 50-sample batch, and
the integrated 100-sample total side by side. The original 50-row Kimi report can
still be compared with Codex on their shared subset.

Full-evaluation samples exceeding 1,000 LLM calls count as failures. The combined
Kimi report has 86 scored samples, six previously missing/running samples, and
eight evaluation errors. The latter two groups also count as failures. The new
archive is marked `cancelled`, with 42 scored samples and eight errors; its raw
PoC successes are 26/50, reduced to 23/50 by the call limit, with no exploit
successes. Combined raw successes are 60 PoCs and three exploits.

Cost and time statistics use scored rows with recorded values, including scored
rows that fail the call limit. Error-row costs and times remain in the per-sample
CSVs but are excluded from the scored-row aggregates. No values are imputed for
the six missing/running rows. Pricing follows `reports/kimi-k3_price_config.json`.

`manifest.json` records source provenance, the new archive's SHA-256, scoring
rules, and output paths. `reported_execution_traces/full_kimi_kimi-k3` contains
the 94 available traces and matching combined-report rows; the complete new
archive is copied under `reported_execution_traces/full_source_logs`.
The older `execution_traces` directory remains a historical export.

To reproduce the integration from the repository root:

```sh
.venv/bin/python scripts/integrate_kimi_current_results.py
```
