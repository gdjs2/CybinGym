# CyBinGym Benchmark

CyBinGym is an Inspect benchmark for binary-analysis agents. Each task provides a vulnerable binary, a fixed binary, and a vulnerability description. A successful agent writes a PoC file named `poc` that crashes the vulnerable binary while the fixed binary exits successfully.

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
```

## OpenSAGE / SageAgent Run

Run the OpenSAGE handoff solver on the default smoke set:

```bash
uv run inspect eval cybingym.py \
  -T agent_type=opensage \
  --model openai/gpt-5
```

Run a specific sample or all samples:

```bash
uv run inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=10013 \
  --limit 1 \
  --model openai/gpt-5

uv run inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=all \
  --model openai/gpt-5
```

The OpenSAGE bridge preserves the CyBinGym task contract:

- `/shared/desc.txt` contains the vulnerability description.
- `/out-vul/<target>` is the vulnerable binary.
- `/out-fix/<target>` is the fixed binary.
- `/shared/poc` is copied back into the Inspect sandbox as `poc` for scoring.

Useful OpenSAGE parameters:

```bash
-T opensage_agent_dir=/path/to/agent_dir
-T opensage_source_dir=/path/to/opensage-adk
-T opensage_python=/path/to/python
-T opensage_output_dir=evals/opensage_inspect
-T opensage_max_workers=10
-T opensage_timeout=7200
-T opensage_base_port=20000
-T opensage_port_stride=10
```

## Rerun Filters and History

OpenSAGE runs write local per-sample output under `evals/opensage_inspect/`. You can rerun only unresolved or error cases:

```bash
uv run inspect eval cybingym.py \
  -T agent_type=opensage \
  -T opensage_sample_ids=all \
  -T opensage_history_filter=unresolved \
  -T opensage_history_summary_path=evals/opensage_history_summary.json \
  --model openai/gpt-5
```

Summarize local history:

```bash
uv run python -m solvers.opensage_history --filter all
uv run python -m solvers.opensage_history --filter errors --write evals/opensage_history_summary.json
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
