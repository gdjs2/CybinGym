# CyBinGym Benchmark

CyBinGym is an Inspect benchmark for binary-analysis agents. Each task provides a vulnerable binary, a fixed binary, and a vulnerability description. The benchmark can score either proof-of-crash only or the full proof-of-crash plus exploitability objective.

## Prerequisites

- `uv`
- Docker daemon access
- API keys for the model provider used by Inspect or OpenSAGE
- For OpenSAGE/SageAgent runs: an OpenSAGE checkout or install plus the optional dependencies from this package

Install the base package from this directory:

```bash
uv sync
```

Install the Python dependencies used by the OpenSAGE bridge:

```bash
uv sync --extra opensage
```

Point CyBinGym at an OpenSAGE checkout and a Python environment that can import it:

```bash
export OPENSAGE_SOURCE_DIR=/path/to/opensage-adk
export OPENSAGE_PYTHON=/path/to/python-with-opensage
```

## Standard Inspect Run

Run the default Inspect ReAct-style solver:

```bash
uv run inspect eval cybingym.py --model openai/gpt-4o-mini
```

Select another built-in agent with `agent_type`:

```bash
uv run inspect eval cybingym.py -T agent_type=openai --model openai/gpt-4o-mini
uv run inspect eval cybingym.py -T agent_type=claude_code --model anthropic/claude-opus-4-8
uv run inspect eval cybingym.py -T agent_type=codex --model openai/gpt-5
uv run inspect eval cybingym.py -T agent_type=kimi_code --model moonshot/kimi-k3
```

Choose the evaluation objective with `evaluation_level`:

```bash
# Default: score crash plus exploitability, and include the victim service.
uv run inspect eval cybingym.py -T agent_type=codex -T evaluation_level=full --model openai/gpt-5

# Crash-only: score only /CybinGym_workdir/poc_crash and omit the victim service.
uv run inspect eval cybingym.py -T agent_type=codex -T evaluation_level=crash --model openai/gpt-5
```

`evaluation_level=crash` is currently supported for the CLI-backed agents `claude_code`, `codex`, and `kimi_code`. Those agents receive a restricted `validate_crash_poc` tool that reads only `/CybinGym_workdir/poc_crash` and validates it against the benchmark's hidden vulnerable and fixed images. It does not expose shell access, Docker image names, arbitrary paths, source code, or a victim service.

Run specific samples with Inspect's `--sample-id` selector. Use a comma-separated
list for more than one sample:

```bash
uv run inspect eval cybingym.py \
  --sample-id 10013 \
  -T agent_type=codex \
  -T evaluation_level=crash \
  --model openai/gpt-5 \
  --reasoning-effort high \
  --turn-limit 80

uv run inspect eval cybingym.py \
  --sample-id 10013,14245,19902 \
  -T agent_type=claude_code \
  --model anthropic/claude-opus-4-8 \
  --reasoning-effort high \
  --turn-limit 80
```

For the CLI-backed agents (`claude_code`, `codex`, and `kimi_code`), use
Inspect's `--turn-limit` to cap model turns. Other useful Inspect limits include
`--token-limit`, `--cost-limit`, `--time-limit`, and `--message-limit`.

Configure the native Moonshot provider before running Kimi Code:

```bash
export MOONSHOT_API_KEY=your-moonshot-api-key
uv run inspect eval cybingym.py \
  -T agent_type=kimi_code \
  --model moonshot/kimi-k3
```

The Kimi Code binary defaults to the pinned version `0.29.0`. Override it with
`-T kimi_code_version=<version>` when intentionally testing another release.
Use `inspect trace dump --filter "Kimi Code"` to inspect agent debug traces.

## OpenSAGE / SageAgent Run

Run the OpenSAGE handoff solver on the default smoke set:

```bash
uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  --model openai/gpt-5
```

Run a specific sample or all samples:

```bash
uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=10013 \
  --limit 1 \
  --model openai/gpt-5

uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=all \
  --model openai/gpt-5
```

The OpenSAGE bridge preserves the CyBinGym task contract:

- `/shared/desc.txt` contains the vulnerability description.
- `/out-vul/<target>` is the vulnerable binary.
- `/out-fix/<target>` is the fixed binary.
- `/shared/poc_crash` is copied back into the Inspect sandbox as `poc_crash` for crash scoring.
- The agent must exploit `victim:33333` and report the retrieved `flag{...}` for exploit scoring.

Useful OpenSAGE parameters:

```bash
-T opensage_agent_dir=/path/to/agent_dir
-T opensage_source_dir=/path/to/opensage-adk
-T opensage_python=/path/to/python
-T opensage_output_dir=evals/opensage_inspect
-T opensage_reasoning_effort=high
-T opensage_max_llm_calls=80
-T opensage_max_workers=10
-T opensage_timeout=7200
-T opensage_base_port=20000
-T opensage_port_stride=10
```

`opensage_reasoning_effort` is passed through to SageAgent's LiteLLM model as
`reasoning_effort` for providers/models that support it. Leave it unset to use
the provider default.

## Rerun Filters and History

OpenSAGE runs write local per-sample output under `evals/opensage_inspect/`. To extend one sample from an explicit previous run, start a fresh run for that sample and seed `/shared/previous_run/` with sanitized prior artifacts and summary context:

```bash
uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=10013 \
  -T opensage_extend_from_run_dir=evals/opensage_inspect/10013/260101_000001_000000 \
  --limit 1 \
  --model openai/gpt-5
```

OpenSAGE runs can also be filtered to rerun only unresolved or error cases:

```bash
uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=all \
  -T opensage_history_filter=unresolved \
  -T opensage_history_summary_path=evals/opensage_history_summary.json \
  --model openai/gpt-5
```

Add `opensage_history_failure_category` to narrow reruns to one classified cause, such as `llm_budget_exhausted`, `tooling_error`, `llm_api_error`, `system_error`, `incomplete_or_cancelled`, `agent_capability_failure`, or `unknown_error`:

```bash
uv run --extra opensage inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=all \
  -T opensage_history_filter=all \
  -T opensage_history_failure_category=llm_budget_exhausted \
  --model openai/gpt-5
```

Summarize local history:

```bash
uv run --extra opensage python -m solvers.opensage_history --filter all
uv run --extra opensage python -m solvers.opensage_history --filter errors --write evals/opensage_history_summary.json
uv run --extra opensage python -m solvers.opensage_history --filter all --failure-category llm_budget_exhausted
```

## Dataset Generation

Generate `dataset.json` from a CSV containing an `id` column:

```bash
uv run python help_scripts/gen_dataset_json.py dataset.json \
  --csv ../cybingym_logs/difficulty/exp.none.metadata.check.classification.exploited.sampled150.csv
```

Generate from explicit ids:

```bash
uv run python help_scripts/gen_dataset_json.py dataset.json --ids 10013,10055,10096
```

## Local Artifacts

`evals/`, `logs/`, and `*.eval` archives are local run artifacts and are ignored by git. They are useful for debugging, rerun filtering, and summaries, but they are not required to be pushed with benchmark code changes.
