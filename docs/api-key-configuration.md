# API Key Configuration

Agentic SWMM plans the interactive `aiswmm` runtime with one of **two API-key providers**: **`openai`** (the default; `OPENAI_API_KEY`) and **`anthropic`** (opt-in; `ANTHROPIC_API_KEY`). Configure the relevant key during installation or in your shell environment before starting the runtime. For how to switch providers and which models each uses, see [`llm_providers.md`](llm_providers.md).

Do not paste API keys into the `aiswmm` conversation. If a key is exposed in a prompt, revoke or rotate it immediately.

The simplest way to store a key is `aiswmm login --openai` (or `aiswmm login --anthropic`), which writes it to `~/.aiswmm/env` (mode `0600`) and never echoes it. A bare `aiswmm login` targets the current default provider's key.

## Option 1: Configure During Installation

The one-line installer asks for an OpenAI API key during setup.

macOS and Linux store the key in:

```text
~/.aiswmm/env
```

Windows stores the key in:

```text
~/.aiswmm/env.ps1
```

The installed `aiswmm` command loads this file before starting the CLI.

You can press Enter at the prompt to skip key entry and configure it later.

## Option 2: Configure With Environment Variables

macOS and Linux:

```bash
export OPENAI_API_KEY="sk-..."        # default provider
# or, for the opt-in Anthropic provider:
export ANTHROPIC_API_KEY="sk-ant-..."
aiswmm
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "sk-..."        # default provider
# or: $env:ANTHROPIC_API_KEY = "sk-ant-..."
aiswmm
```

Set only the key for the provider you intend to use. To make `anthropic` the
default, run `aiswmm login --anthropic` or set `provider.default = anthropic`
in `~/.aiswmm/config.toml` (see [`llm_providers.md`](llm_providers.md)).

For persistent shell configuration, add the export line to your shell profile, or use the installer-managed `~/.aiswmm/env` / `~/.aiswmm/env.ps1` file.

## Check Configuration

Run:

```bash
aiswmm doctor
```

`aiswmm doctor` (and `aiswmm login --status`) report which provider is active and which keys are present. If the active provider's key is available, the check reports it as configured. If it is missing, deterministic local commands can still work, but LLM-backed interactive planning will not.

## Recommended First Run

After the key is configured:

```bash
aiswmm
```

Then try:

```text
inspect available skills, then tell me what local tools you can call
```

```text
run tecnopolo_r1_199401.inp, audit it, and plot node OU2
```
