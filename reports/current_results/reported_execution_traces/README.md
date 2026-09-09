# Reported Execution Traces

This directory contains collision-free per-table trace exports for the current
reported results.

The `full_source_logs` directory contains complete copies of every unique
`selected_source` log archive referenced by the reported CSV rows. Use this when
you need the full `.eval` source log rather than the extracted per-sample trace.

Trace sets:

- `crash_codex_gpt-5.6`: 100 Codex crash-only rows.
- `crash_kimi_kimi-k3`: 100 Kimi crash-only rows.
- `full_codex_gpt-5.6`: 100 Codex full-evaluation rows.
- `full_kimi_kimi-k3`: 100 Kimi full-evaluation rows.

Every trace set has a `reported_rows.csv` snapshot generated from the final
current-results row selection. Across the four trace sets, 398 of 400 reported
rows have extracted `sample_trace.json` trajectories. The two exceptions are
Kimi full samples 10574 and 17778: they are declared by
`full_source_logs/153487198771__2026-08-30T14-41-15-00-00_cybingym_o6yZBuhrAp5BbCDewHJGMy.eval`
but that archive contains no per-sample trace JSON or summary row for them.
They are represented by `summary_row.json`, `run_header.json`, manifest error
entries, and `trace_export_failures.csv`.

Each trace set has its own `manifest.csv` and `manifest.json`. Each exported
sample directory contains:

- `summary_row.json`: the exact reported CSV row.
- `run_header.json`: run-level metadata when available.
- `sample_trace.json`: the copied per-sample execution trace.

The per-sample `sample_trace.json` files are extracted from the corresponding
full source log under `full_source_logs`. They are convenient evidence bundles
for reported rows, not replacements for the complete source logs.

`manifest.json` at this directory root summarizes the four trace sets.
`trace_export_failures.csv` lists rows whose selected source did not contain an
extractable trajectory. `missing_or_running_failures.csv` and
`evaluation_error_failures.csv` list final full-evaluation failure rows counted
in the current report.

To refresh the exported trajectories after regenerating the current result
tables:

```sh
.venv/bin/python scripts/integrate_full100_overall_results.py
.venv/bin/python scripts/export_current_reported_traces.py
```
