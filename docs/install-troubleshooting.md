# Install and troubleshooting

One command. What follows is the short list of things that can go wrong on a
real machine, each with the reason and the fix.

## Install

**Windows** (PowerShell):

```powershell
irm https://aiswmm.com/install.ps1 | iex
```

**macOS and Linux**:

```bash
curl -fsSL https://aiswmm.com/install.sh | bash
```

The installer creates a virtualenv, installs the Python dependencies, the MCP
servers, and the pinned EPA SWMM 5.2.4 solver, then hands over to `aiswmm
setup` to pick an AI provider. Roughly 600 MB and 3 to 5 minutes.

Then:

```bash
aiswmm
```

`aiswmm doctor` reports the state of every piece at any time.

## Choosing a provider without an API key

`aiswmm setup` lists ten routes. Three need no API key: a local gateway that
fronts a ChatGPT plan, Ollama, and LM Studio.

For the ChatGPT plan, pick `codex`. The route is keyless because a gateway on
your machine holds the sign-in; aiswmm only speaks HTTP to it and never sees a
credential. The wizard offers to install that gateway, or:

```bash
aiswmm gateway login     # installs if needed, signs in via the browser, serves
```

If requests come back `403 unsafe_example_api_key`, the gateway is up and
refusing: its config still holds the shipped placeholder keys. `aiswmm gateway
restart` repairs it.

## Windows on ARM

The installer handles this from v0.9.3. What it does, and what to do on
earlier versions:

`shapely` and `pyogrio` have never published a `win_arm64` wheel, so an ARM64
Python cannot install the geospatial dependencies at all. pip falls back to
building from source and fails on a missing GDAL:

```
Collecting pyogrio
  Using cached pyogrio-0.13.0.tar.gz
  GDAL_VERSION must be provided as an environment variable
```

The answer is an **x64** Python, which runs under the emulation Windows 11
provides. The installer now finds or installs one. On an older version, do it
by hand:

```powershell
winget install -e --id Python.Python.3.12 --architecture x64 --force
```

`--force` matters: winget keys on the package id, so without it a request for
the x64 build of a version already installed as ARM64 becomes an upgrade check
that installs nothing.

## "Python deps install" fails in seconds

pip reports success from metadata alone, so a virtualenv can hold packages
built for a different Python and look installed. This happens when the
interpreter changes between installs, because `python -m venv` over an
existing directory repoints it without clearing the packages. The symptom is
an import error naming a mismatched build:

```
_multiarray_umath.cp311-win_amd64.pyd ... incompatible with cpython-312
```

From v0.9.3 the installer detects this and rebuilds. Otherwise delete the
virtualenv and re-run:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\agentic-swmm-workflow\.venv"
```

```bash
rm -rf ~/agentic-swmm-workflow/.venv
```

## Linux servers and containers

Root with no `sudo` is normal for a container image, and the installer handles
it from v0.9.3. Earlier versions fail as `sudo: command not found`, and the
SWMM engine step, being non-fatal, then reports a complete install with no
solver. Check with `aiswmm doctor`; the `swmm5 executable` row is the one that
matters.

The installer does not install Python on Linux. Install 3.10 or newer with the
system package manager first.

The bash installer clones into the **current directory**, so run it from where
you want the checkout. The Windows installer always uses `%LOCALAPPDATA%`.

## Canadian municipal networks

`fetch_swmm_from_canada` is off until its URL is set, because enabling it
sends the area you request to a service. `aiswmm setup` offers it, or:

```bash
export AISWMM_SWMMCANADA_URL=https://swmm.h2ox.me
```

```powershell
setx AISWMM_SWMMCANADA_URL "https://swmm.h2ox.me"
```

`aiswmm doctor` then probes the service and reports its health.

## A model that will not run

`ERROR 361: could not open external file used for Time Series <name>` means
the model references a data file that is not beside it. SWMM resolves those
paths relative to the `.inp` file's own directory, which is why copying a lone
`.inp` into a new folder breaks it. The error now names the file and where a
copy of it exists.

## Which platforms are actually tested

Every platform below runs the documented one-liner in CI on a real machine of
that kind, then asserts the solver binary exists and the geospatial stack
imports:

| Platform | Runner |
|---|---|
| Windows x64 | `windows-latest` |
| Windows on ARM | `windows-11-arm` |
| Linux | `ubuntu-latest`, inside a root container with no sudo |
| macOS | `macos-latest` |

One gap worth naming: GitHub's macOS images ship Homebrew, so the branch where
the installer installs Homebrew itself is not exercised. That is the path a
brand-new Mac takes.

## Where a run puts things

```
runs/<date>/<time>_<goal>_run/
├─ README.md          what this run was, and which file is the deliverable
├─ report.docx        the Word deliverable, when one was requested
├─ final_report.md
├─ 06_runner/         model.rpt is the engine's own report
├─ 09_audit/          diagnostics, provenance, hydraulic summary
└─ _agent/            the agent's own record; nothing here is a result
```
