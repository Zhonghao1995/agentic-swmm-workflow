# Runtime Install Options

The README keeps the public quickstart to the two website installers. These are the other supported paths.

## Docker

Use Docker when you want a reproducible container with the SWMM solver and Python environment bundled:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.0 acceptance
```

Artifacts are written to `agentic-swmm-runs`.

## Python Package

Use the Python package when Python, Node.js, and SWMM are already available, or when you mainly want the `aiswmm` CLI:

```bash
pip install aiswmm
aiswmm setup --provider openai --model gpt-5.5
aiswmm --help
```

You can choose another OpenAI model during setup, such as `gpt-5.4` or `gpt-5.5`.

Set your key outside the conversation before running OpenAI-backed planning. See [API key configuration](api-key-configuration.md) for installer-managed and environment-variable setup.

```bash
export OPENAI_API_KEY="..."
aiswmm
```

Do not paste API keys into `aiswmm` prompts.
