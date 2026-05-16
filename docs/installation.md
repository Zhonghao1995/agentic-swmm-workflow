# Installation and CLI Guide

This page keeps detailed setup notes out of the README. For most users, Docker is the recommended path because it keeps the SWMM solver and Python environment reproducible. Use local installation when you need to edit skills, run MCP servers, or develop the Python CLI.

## Docker

Install Docker Desktop or Docker Engine, then run:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.6.1 acceptance
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

The package installs both command names and includes the Agentic SWMM runtime resources: skills, MCP launchers, public memory files, examples, and integration templates.

```bash
aiswmm setup --provider openai --model gpt-5.5-2026-04-23
aiswmm doctor
aiswmm skill list --registry
aiswmm mcp list --registry
```

The model is configurable. For reproducibility (recommended for any experiment whose results you intend to cite), pin a dated snapshot rather than a rolling alias:

```bash
aiswmm setup --provider openai --model gpt-5.5-2026-04-23
aiswmm setup --provider openai --model gpt-5.4-2026-03-05
```

Aliases like `gpt-5.5` or `gpt-5.4` also work, but they silently roll forward when OpenAI ships a new snapshot under the same alias, which breaks bit-for-bit reproducibility of past runs.

For real OpenAI agent planning, set your key in the local shell before running `aiswmm`:

```bash
export OPENAI_API_KEY="..."
aiswmm --provider openai "Explain what this Agentic SWMM installation can do"
```

`agentic-swmm` remains available as an alias for the same CLI.

## One-Line Runtime Installer

Use this when you want a fuller local runtime setup similar to OpenClaw or Hermes installers.

The installer looks for `python3.12`, `python3.11`, `python3.10`, then `python3`, and only uses an interpreter that satisfies Python 3.10+. If none is available on macOS, it can install Homebrew Python before creating the local virtual environment.

If an older local `.venv` was previously created with Python 3.9, the installer rebuilds that virtual environment before installing `aiswmm`.

During setup, the installer asks for an OpenAI API key. Press Enter to do it later, or paste a key to enable OpenAI-backed `aiswmm` agent planning immediately. On macOS and Linux, the key is stored in `~/.aiswmm/env`; on Windows, it is stored in `~/.aiswmm/env.ps1`. The installed `aiswmm` command loads that file before starting the CLI. See [API key configuration](api-key-configuration.md) for the recommended setup paths.

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
curl -fsSL https://aiswmm.com/install.sh | AISWMM_INSTALL_REF=v0.6.1 bash
```

```powershell
$env:AISWMM_INSTALL_REF = "v0.6.1"
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
