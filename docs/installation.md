# Installation and CLI Guide

This page keeps detailed setup notes out of the README. For most users, Docker is the recommended path because it keeps the SWMM solver and Python environment reproducible. Use local installation when you need to edit skills, run MCP servers, or develop the Python CLI.

## Docker

Install Docker Desktop or Docker Engine, then run:

```bash
mkdir -p agentic-swmm-runs
docker run --rm -v "$PWD/agentic-swmm-runs:/app/runs" ghcr.io/zhonghao1995/agentic-swmm-workflow:v0.5.4 acceptance
```

Artifacts are written to `agentic-swmm-runs`.

The image pins USEPA SWMM to `v5.2.4` for reproducible solver builds.

## PyPI package

Install the Python package:

```bash
pip install aiswmm
```

The package installs both command names:

```bash
aiswmm doctor
agentic-swmm doctor
```

## macOS and Linux

Review the bootstrap script before running it:

```bash
curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh
```

Then run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.sh)"
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
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1'))"
```

If you already cloned the repository, run the local installer from the checkout:

```powershell
cd agentic-swmm-workflow
.\scripts\install.ps1 -Yes
```

To install only user-space Python and MCP dependencies before configuring SWMM:

```powershell
.\scripts\install.ps1 -Yes -SkipSwmm
```

The same option can be passed to the bootstrap script:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/Zhonghao1995/agentic-swmm-workflow/main/scripts/bootstrap.ps1'))) -SkipSwmm"
```

If EPA SWMM is already installed outside PATH, pass the executable explicitly:

```powershell
.\scripts\install.ps1 -Yes -SwmmExe "C:\Path\To\runswmm.exe"
```

By default, the Windows installer downloads the USEPA SWMM `5.2.4` solver zip into `.local\swmm`, matching the Docker image's `SWMM_REF=v5.2.4`. To use Chocolatey instead:

```powershell
.\scripts\install.ps1 -Yes -InstallSystemDeps -SwmmVersion 5.2.4
```

## Unified CLI

The agentic workflow remains centered on Skills, MCP tools, audit records, Obsidian-compatible notes, and modeling memory. The `agentic-swmm` CLI is a stable execution layer for common actions, so users and agent runtimes do not need to remember lower-level script paths.

The local installers install the editable Python package and expose this command inside the repository virtual environment:

```bash
agentic-swmm doctor
```

For an existing checkout or development environment, reinstall the editable package explicitly if the command is missing.

On macOS or Linux:

```bash
python -m pip install -e .
agentic-swmm doctor
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\agentic-swmm.exe doctor
```

Prepared-input example:

```bash
agentic-swmm run --inp examples/tecnopolo/tecnopolo_r1_199401.inp --run-dir runs/tecnopolo-cli --node OUT_0
agentic-swmm audit --run-dir runs/tecnopolo-cli
agentic-swmm plot --run-dir runs/tecnopolo-cli --node OUT_0
agentic-swmm memory --runs-dir runs --out-dir memory/modeling-memory
```

The CLI currently wraps the existing validated scripts. Lower-level scripts and MCP tools remain the right interface for module development, debugging, GIS preprocessing, parameter mapping, network import, calibration, and uncertainty workflows that are not yet exposed through the CLI.
