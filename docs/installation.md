# Installation and CLI Guide

This page keeps detailed setup notes out of the README. For most users, Docker is the recommended path because it keeps the SWMM solver and Python environment reproducible. Use local installation when you need to edit skills, run MCP servers, or develop the Python CLI.

## Docker

Install Docker Desktop or Docker Engine, then run:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.7.0 acceptance
```

Artifacts are written to `agentic-swmm-runs`.

The image pins USEPA SWMM to `v5.2.4` for reproducible solver builds.

## Two Installation Paths

Agentic SWMM supports two local installation paths:

- **Developer / academic Python path:** install the Python package with `pip`, then run `aiswmm setup`.
- **Complete local runtime path:** use the one-line installer to clone the repository, install MCP dependencies, configure SWMM, and run `aiswmm setup`.

## PyPI Package

Use this when Python, Node.js, and SWMM are already available or when you mainly want the `aiswmm` CLI and packaged runtime resources.

`aiswmm` requires Python 3.10 or newer. On macOS, `/usr/bin/python3` may be Python 3.9, so use a newer interpreter when installing directly:

```bash
python3.11 -m pip install aiswmm
```

Install the Python package:

```bash
pip install aiswmm
```

As of v0.7.0 this resolves to a regular, non-prerelease version, so `pip install aiswmm` selects it directly — no `--pre` required. (Packaging note: v0.7.0 is the current point release, not an `a`/`b`/`rc` pre-release. v0.6.4 remains available on PyPI for paper-aligned reproducibility runs. The project itself is still alpha-stage software — see the README status note.) To install a **pre-release** (e.g. an alpha or release-candidate) you must opt in explicitly — `pip install aiswmm` alone never selects an alpha/beta/rc per [PEP 440](https://peps.python.org/pep-0440/):

```bash
pip install aiswmm==0.6.3a1   # pin a specific pre-release
pip install --pre aiswmm      # allow any pre-release
```

The package installs both command names and includes the Agentic SWMM runtime resources: skills, MCP launchers, public memory files, examples, and integration templates.

```bash
aiswmm setup --provider openai --model gpt-5.5-2026-04-23
aiswmm doctor
aiswmm skill list --registry
aiswmm mcp list --registry
```

If you have multiple checkouts of this repo on the same machine (e.g. a release tag plus a development clone), `aiswmm doctor` flags `mcp.json` routing drift — `~/.aiswmm/mcp.json` may still point to a prior install. Re-align it to the currently-active editable install with:

```bash
aiswmm setup --refresh-mcp
```

This only regenerates `~/.aiswmm/mcp.json`; your `config.toml`, `skills.json`, and `memory.json` stay untouched.

The model is configurable. For reproducibility (recommended for any experiment whose results you intend to cite), pin a dated snapshot rather than a rolling alias:

```bash
aiswmm setup --provider openai --model gpt-5.5-2026-04-23
aiswmm setup --provider openai --model gpt-5.4-2026-03-05
```

Aliases like `gpt-5.5` or `gpt-5.4` also work, but they silently roll forward when OpenAI ships a new snapshot under the same alias, which breaks bit-for-bit reproducibility of past runs.

For real OpenAI agent planning, set your key in the local shell before running `aiswmm`:

```bash
export OPENAI_API_KEY="..."
aiswmm agent --provider openai "Explain what this Agentic SWMM installation can do"
```

`agentic-swmm` remains available as an alias for the same CLI.

## One-Line Runtime Installer

Use this when you want a fuller local runtime setup similar to OpenClaw or Hermes installers.

The installer looks for `python3.12`, `python3.11`, `python3.10`, then `python3`, and only uses an interpreter that satisfies Python 3.10+. If none is available on macOS, it can install Homebrew Python before creating the local virtual environment.

If an older local `.venv` was previously created with Python 3.9, the installer rebuilds that virtual environment before installing `aiswmm`.

During setup, the installer asks for an OpenAI API key. Press Enter to do it later, or paste a key to enable OpenAI-backed `aiswmm` agent planning immediately. On macOS and Linux, the key is stored in `~/.aiswmm/env`; on Windows, it is stored in `~/.aiswmm/env.ps1`. The installed `aiswmm` command loads that file before starting the CLI. See [API key configuration](api-key-configuration.md) for the recommended setup paths.

The agent planner can also run on Anthropic instead of OpenAI (both are API-key backends) — see [LLM providers](llm_providers.md) for how to switch backends and authenticate each one.

On macOS and Linux, after publishing `web/install.sh` to your website:

```bash
curl -fsSL https://aiswmm.com/install.sh | bash
```

On Windows PowerShell, after publishing `web/install.ps1`:

```powershell
irm https://aiswmm.com/install.ps1 | iex
```

The Windows entrypoint installs into the current user's local application directory by default instead of `C:\Windows\System32`. If Git is unavailable, it downloads a GitHub source archive. If Python 3.10+ is unavailable, it first tries a user-scope `winget` Python install. It creates a local `.venv`, installs Python requirements and the editable CLI package, installs MCP npm dependencies when Node.js is available, downloads the USEPA SWMM solver zip into `.local\swmm`, and creates a `swmm5` shim under `.local\bin`.

The Windows installer also creates user-level `aiswmm` and `agentic-swmm` command shims in `%LOCALAPPDATA%\AgenticSWMM\bin` and adds that directory to the user PATH for new terminals. The current installer session can use the command immediately. Administrator PowerShell is only needed when you explicitly choose Chocolatey system dependency installation with `-InstallSystemDeps`.

The website scripts are thin stable entrypoints. They download the repository bootstrap scripts from GitHub, then run the local installer. For reproducible installs, pin a release tag before running:

```bash
curl -fsSL https://aiswmm.com/install.sh | AISWMM_INSTALL_REF=v0.7.0 bash
```

```powershell
$env:AISWMM_INSTALL_REF = "v0.7.0"
irm https://aiswmm.com/install.ps1 | iex
```

## After Installation: Interactive Agent Smoke Test

`aiswmm` starts an interactive agent runtime by default:

```bash
aiswmm
```

Useful first prompts:

```text
inspect available skills, then tell me what local tools you can call
```

```text
run tecnopolo_r1_199401.inp, audit it, and plot node OU2
```

The Tecnopolo prompt should produce a date-organized run directory such as `runs/YYYY-MM-DD/HHMMSS_tecnopolo_run/` with SWMM runner outputs, QA summaries, audit records, plot artifacts, and a `final_report.md`.

Inside the interactive shell, use `/new-session` to start a fresh session context without closing the terminal. This clears the active run context so the next task starts cleanly. Session starts are recorded in `runs/YYYY-MM-DD/_sessions.jsonl`.

You can also run the same idea as a one-shot command:

```bash
aiswmm "run tecnopolo_r1_199401.inp, audit it, and plot node OU2"
```

Keep API keys outside the conversation. Configure `OPENAI_API_KEY` during setup or in your shell environment, and revoke or rotate any key that is accidentally pasted into an agent prompt. See [API key configuration](api-key-configuration.md).

## macOS and Linux Checkout Installer

Review the bootstrap script before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh
```

Then run:

```bash
curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh | bash
```

For an existing checkout, run the local installer directly:

```bash
./scripts/install.sh
```

## Windows PowerShell

Review the bootstrap script before running it:

```powershell
(New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1')
```

Then run:

```powershell
irm https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1 | iex
```

If you already cloned the repository, run the local installer from the checkout:

```powershell
cd agentic-swmm-workflow
.\scripts\install.ps1 -Yes
```

After the installer finishes, open a new PowerShell window or use the current installer session and run:

```powershell
aiswmm doctor
aiswmm demo acceptance --run-id latest
```

To install only user-space Python and MCP dependencies before configuring SWMM:

```powershell
.\scripts\install.ps1 -Yes -SkipSwmm
```

The same option can be passed to the bootstrap script:

```powershell
$script = (New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1')
& ([scriptblock]::Create($script)) -SkipSwmm
```

If EPA SWMM is already installed outside PATH, pass the executable explicitly:

```powershell
.\scripts\install.ps1 -Yes -SwmmExe "C:\Path\To\runswmm.exe"
```

By default, the Windows installer downloads the USEPA SWMM `5.2.4` solver zip into `.local\swmm`, matching the Docker image's `SWMM_REF=v5.2.4`. To use Chocolatey instead:

```powershell
.\scripts\install.ps1 -Yes -InstallSystemDeps -SwmmVersion 5.2.4
```

If you need to pin a different USEPA SWMM solver release through the website entrypoint:

```powershell
& ([scriptblock]::Create((New-Object System.Net.WebClient).DownloadString('https://aiswmm.com/install.ps1'))) -SwmmVersion 5.2.4
```

## Unified CLI

The agentic workflow remains centered on Skills, MCP tools, audit records, Obsidian-compatible notes, and modeling memory. The `agentic-swmm` CLI is a stable execution layer for common actions, so users and agent runtimes do not need to remember lower-level script paths.

The local installers install the editable Python package and expose this command inside the repository virtual environment:

```bash
aiswmm doctor
```

For an existing checkout or development environment, reinstall the editable package explicitly if the command is missing.

On macOS or Linux:

```bash
python -m pip install -e .
aiswmm doctor
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\aiswmm.exe doctor
```

Prepared-input example:

```bash
aiswmm run --inp examples/tecnopolo/tecnopolo_r1_199401.inp --run-dir runs/tecnopolo-cli --node OUT_0
aiswmm audit --run-dir runs/tecnopolo-cli
aiswmm plot --run-dir runs/tecnopolo-cli --node OUT_0
aiswmm memory --runs-dir runs --out-dir memory/modeling-memory
```

The CLI currently wraps the existing validated scripts. Lower-level scripts and MCP tools remain the right interface for module development, debugging, GIS preprocessing, parameter mapping, network import, calibration, and uncertainty workflows that are not yet exposed through the CLI.

## CLI verbs

The `aiswmm` CLI groups verbs by purpose. Run `aiswmm --help` for the full grouped block, or `aiswmm help <verb>` for any verb's options. Every verb listed below accepts `--example` (prints a copy-pasteable invocation) and most accept `--json` and `--quiet`.

| Verb | Description | Example |
| --- | --- | --- |
| `aiswmm run` | Execute SWMM on an INP, write audit + plots. | `aiswmm run --inp examples/tecnopolo/tecnopolo_r1_199401.inp --run-dir runs/tecnopolo --node OUT_0` |
| `aiswmm audit` | Re-write audit notes for an existing run. | `aiswmm audit --run-dir runs/tecnopolo` |
| `aiswmm plot` | Render rain/runoff/depth plots from a run directory. | `aiswmm plot --run-dir runs/tecnopolo` |
| `aiswmm compare` | Diff continuity/peak/runoff between two runs. | `aiswmm compare --run-a runs/baseline --run-b runs/with-lid --json` |
| `aiswmm cite` | Look up an entry in the citations library by key. | `aiswmm cite huber_dickinson_1988` |
| `aiswmm cite-param` | Reverse-lookup a citation by parameter name + value. | `aiswmm cite-param --name manning_n_overland.asphalt --value 0.013 --json` |
| `aiswmm storm` | Generate a design hyetograph (uniform/triangular/chicago/huff/scs). | `aiswmm storm --shape chicago --depth-mm 25 --duration-min 60 --peak-position 0.4 --out storm.dat` |
| `aiswmm transfer` | Suggest starter parameters for a new case from similar past cases. | `aiswmm transfer --inp examples/saanich/saanich.inp --top-k 3` |
| `aiswmm uncertainty plan` | Plan a SALib uncertainty scan (does not execute SWMM). | `aiswmm uncertainty plan --inp model.inp --param manning_n=0.010,0.018 --method morris --n-samples 50` |
| `aiswmm calibrate` | Calibration loop with checkpoint-aware progress (stub today). | `aiswmm calibrate --inp model.inp --run-id calib_001 --total-iters 100 --param manning_n=0.010,0.018 --run-dir runs/calib_001` |
| `aiswmm bootstrap memory` | Scaffold an empty `memory/modeling-memory/` skeleton. | `aiswmm bootstrap memory --dir memory/modeling-memory` |
| `aiswmm doctor` | Diagnose install, memory stores, and opt-out knobs; optional `--fix`. | `aiswmm doctor --fix --yes` |

The flag convention is shared across verbs: `--inp` for the model input, `--<noun>-path` for path overrides (`--calibration-memory-path`, `--storm-library-path`, ...), `--<noun>-entry` for keys inside a library, `--json` for machine-readable output, and `--quiet` to suppress chrome. Legacy flag spellings (for example `--base-inp`, `--calibration-store`, `--from-library`) still work, but emit a `[deprecated]:` warning to stderr.

`aiswmm bootstrap memory` only creates the empty memory skeleton (`parametric_memory.jsonl`, `calibration_memory.jsonl`, `negative_lessons.jsonl`, `project_overrides.yaml`, `README.md`). The project's `citations.yaml` and `reference_benchmarks.yaml` are separately maintained and are not seeded by this command — they ship with the repository and are user-edited.

For one worked example of each memory-facing verb, see [memory_runtime_cli.md](memory_runtime_cli.md).
