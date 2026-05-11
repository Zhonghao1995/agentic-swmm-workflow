# API Key Configuration

Agentic SWMM currently uses OpenAI API-key based planning for the interactive `aiswmm` runtime. Configure the key during installation or in your shell environment before starting the runtime.

Do not paste API keys into the `aiswmm` conversation. If a key is exposed in a prompt, revoke or rotate it immediately.

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
export OPENAI_API_KEY="sk-..."
aiswmm
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = "sk-..."
aiswmm
```

For persistent shell configuration, add the export line to your shell profile, or use the installer-managed `~/.aiswmm/env` / `~/.aiswmm/env.ps1` file.

## Check Configuration

Run:

```bash
aiswmm doctor
```

If the key is available, the OpenAI key check should report it as configured. If it is missing, deterministic local commands can still work, but OpenAI-backed interactive planning will not.

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
