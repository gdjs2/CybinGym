# CybinGym Benchmark

TODO

## Quick Start

**Prerequisites:**

* `uv` installed on your machine.
* Docker daemon running (required for the default docker sandbox agent).

1. Clone the repository:
```bash
git clone https://github.com/gdjs2/CybinGym.git
cd CybinGym
```
2. Set your API Key
```bash
export OPENAI_API_KEY="your-openai-key"
# You can refer to https://inspect.aisi.org.uk/providers.html for the variable's name of your provider
```
3. Run the Benchmark
```bash
uv run inspect eval cybingym.py --model openai/gpt-4o-mini
# You can refer to https://inspect.aisi.org.uk/providers.html for supported models
```
This command will run CybinGym on inspect_ai's [`react()`](https://inspect.aisi.org.uk/react-agent.html) agent. You can specify the agent to be evaluate through argument `-T agent_type=basic`. 
```bash
uv run inspect eval cybingym.py -T agent_type=basic --model openai/gpt-4o-mini
```

Run with Kimi Code through the native Moonshot provider:
```bash
export MOONSHOT_API_KEY="your-moonshot-api-key"
uv run inspect eval cybingym.py -T agent_type=kimi_code --model moonshot/kimi-k3
```
The Kimi Code binary defaults to version `0.29.0`. Override it with `-T kimi_code_version=<version>` when testing another release.
