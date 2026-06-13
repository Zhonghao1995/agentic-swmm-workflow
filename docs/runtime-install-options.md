# Runtime Install Options

There look like four ways in, but they are **three strategies** — the two one-line installers are the same installer, shipped as a macOS/Linux build (`install.sh`) and a Windows build (`install.ps1`). Pick one:

| | One-line installer | Docker | PyPI (`pip`) |
| --- | --- | --- | --- |
| **Command** | `install.sh` (macOS/Linux) · `install.ps1` (Windows) | `docker run … ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.7.3` | `pip install aiswmm` |
| **What you get** | Full stack: venv + `aiswmm` CLI + MCP servers + **swmm5 solver** + API-key prompt | Everything baked into the image — solver, Python env, and dependencies | The `aiswmm` CLI and its Python dependencies |
| **swmm5 / Node / API key** | Provisioned for you | Already in the image | Bring your own |
| **Reproducibility** | Tracks the current rolling build | **Strongest** — pinned and byte-identical across machines | As pinned as you make your own environment |
| **Prerequisites** | Python ≥ 3.10 and Node ≥ 18 (plus a C/C++ toolchain on macOS/Linux to build the solver) | Docker only | Python — *and* Node and SWMM already on your system |
| **Best for** | The fastest route to a complete local runtime | Reproducible, production, or paper-replication runs | Embedding the CLI in a Python environment you already manage |

**One-line decision:**

- **Just want it running fast** → the one-line installer for your OS (the complete, batteries-included path).
- **Need reproducibility, production, or paper replication** → Docker, pinned (`:v0.7.3`; `:v0.6.4` for the companion paper).
- **Already have SWMM and Node, and want `aiswmm` inside your own project** → `pip install aiswmm`, then run `aiswmm setup` and point it at your swmm5 solver and API key.

The README keeps the quickstart to the two website installers; the commands for the Docker and PyPI paths follow.

## Docker

Use Docker when you want a reproducible container with the SWMM solver and Python environment bundled:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.7.3 acceptance
```

Artifacts are written to `agentic-swmm-runs`.

## Python Package

Use the Python package when Python, Node.js, and SWMM are already available, or when you mainly want the `aiswmm` CLI:

```bash
pip install aiswmm
aiswmm setup --provider openai --model gpt-5.5-2026-04-23
aiswmm --help
```

You can choose another OpenAI model snapshot during setup, such as `gpt-5.4-2026-03-05` or `gpt-5.5-2026-04-23`. Alias names like `gpt-5.5` work too but silently roll forward when OpenAI ships a new snapshot under the same alias; pin a dated ID for reproducible experiment runs.

Set your key outside the conversation before running OpenAI-backed planning. See [API key configuration](api-key-configuration.md) for installer-managed and environment-variable setup.

```bash
export OPENAI_API_KEY="..."
aiswmm
```

Do not paste API keys into `aiswmm` prompts.
