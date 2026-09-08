# Reported Execution Traces

This directory contains collision-free per-table trace exports for the current
reported results.

The `full_source_logs` directory contains complete copies of every unique
`selected_source` log archive referenced by the reported CSV rows. Use this when
you need the full `.eval` source log rather than the extracted per-sample trace.

Trace sets:

- `crash_codex_gpt-5.6`: 100 Codex crash-only rows.
- `crash_kimi_kimi-k3`: 100 Kimi crash-only rows.
- `full_codex_gpt-5.6`: 50 Codex full-evaluation rows.
- `full_kimi_kimi-k3`: 94 recorded Kimi full-evaluation rows from the combined
  100-binary report: 86 scored rows and eight evaluation-error rows.

Each trace set has its own `manifest.csv` and `manifest.json`. Each exported
sample directory contains:

- `summary_row.json`: the exact reported CSV row.
- `run_header.json`: run-level metadata when available.
- `sample_trace.json`: the copied per-sample execution trace.

The per-sample `sample_trace.json` files are extracted from the corresponding
full source log under `full_source_logs`. They are convenient evidence bundles
for reported rows, not replacements for the complete source logs.

The six Kimi full-evaluation samples that were still running did not have
completed trace rows to export. They are listed in
`missing_or_running_failures.csv` and are counted as failures in the report.

The additional 50-binary Kimi archive from September 5 is marked `cancelled`.
It contains 42 scored samples and eight evaluation errors. All 50 have exported
traces; the eight errors are listed in `evaluation_error_failures.csv` and count
as failures. Full Kimi trace manifests and summary rows reference
`../dataset100_full_exploit_summary_kimi_code_moonshot_kimi-k3_llmcall1000_missingfailed.csv`.
