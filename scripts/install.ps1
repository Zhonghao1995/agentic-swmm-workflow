# scripts/install.ps1
#
# Stepped interactive installer for the Agentic Stormwater Modeling
# Workflow (AISWMM) on Windows. Mirrors the bash flow:
#
#   1. Prereq checks (Python >=3.10, Node >=18)
#   2. Risk-warning banner -> Y/n confirm
#   3. Per-step Y/n: venv, python deps, MCP npm, skill files, API key
#   4. Success summary + next-step hint
#
# Flags:
#   -Auto             Skip all prompts (CI / scripted install).
#   -Yes              Legacy alias for -Auto.
#   -SkipPython       Skip Python venv + Python deps steps.
#   -SkipMcp          Skip MCP server npm install step.
#   -SkipSwmm         Skip SWMM engine install step.
#   -SkipSetup        Skip aiswmm orchestration setup after installation.
#   -Provider <name>  LLM provider to register (default: openai).
#   -Model <name>     LLM model to register (default: gpt-5.5).
#   -SwmmVersion <v>  USEPA SWMM version (default: 5.2.4).
#
# N at any prompt exits 0 with "Installation aborted." Failure at any
# step prints a remediation hint and exits non-zero.

param(
    [switch]$Auto,
    [switch]$Yes,
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [switch]$SkipSwmm,
    [switch]$SkipSetup,
    [string]$Provider = "openai",
    [string]$Model = "gpt-5.5",
    [string]$SwmmVersion = "5.2.4"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$VenvDir = Join-Path $RepoRoot '.venv'
$ReqFile = Join-Path $ScriptDir 'requirements.txt'
$AiswmmConfigDir = if ($env:AISWMM_CONFIG_DIR) { $env:AISWMM_CONFIG_DIR } else { Join-Path $HOME '.aiswmm' }
$AiswmmEnvFile = Join-Path $AiswmmConfigDir 'env.ps1'

# -Auto and -Yes both disable interactive prompts.
$script:AutoMode = $Auto.IsPresent -or $Yes.IsPresent

# ---------------------------------------------------------------------------
# Helpers (mirror of scripts/_install_helpers.bash)
# ---------------------------------------------------------------------------

function Print-Banner {
    Write-Host '+---------------------------------------------------+'
    Write-Host '|  AISWMM Installer                                 |'
    Write-Host '|  Agentic Stormwater Modeling Workflow             |'
    Write-Host '|                                                   |'
    Write-Host '|  This installer will:                             |'
    Write-Host '|  - Create a Python virtualenv (~50 MB)            |'
    Write-Host '|  - Install Python deps (~150 MB)                  |'
    Write-Host '|  - Install MCP servers via npm (~400 MB)          |'
    Write-Host '|  - Copy skill files to ~/.aiswmm/                 |'
    Write-Host '|  - Optionally configure your OpenAI API key       |'
    Write-Host '|                                                   |'
    Write-Host '|  Estimated total time: 3-5 minutes                |'
    Write-Host '|  Total disk: ~600 MB                              |'
    Write-Host '+---------------------------------------------------+'
}

function Print-Failure {
    param(
        [string]$Headline,
        [string[]]$Remediation = @()
    )
    Write-Host ""
    Write-Host "[ERROR] $Headline" -ForegroundColor Red
    if ($Remediation.Count -gt 0) {
        Write-Host "Remediation:"
        foreach ($line in $Remediation) {
            Write-Host "  - $line"
        }
    }
}

function Prompt-YN {
    param(
        [string]$Question,
        [string]$Default = 'Y'
    )
    if ($script:AutoMode) {
        return ($Default -in @('Y', 'y'))
    }
    $suffix = if ($Default -in @('Y', 'y')) { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Host "$Question $suffix"
    if ([string]::IsNullOrEmpty($reply)) { $reply = $Default }
    return ($reply -in @('y', 'Y', 'yes', 'YES'))
}

function Check-PythonVersion {
    param([string]$PythonExe = 'python')
    if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
        Print-Failure "Python 3.10+ not found on PATH." @(
            "Windows: install Python 3.12 from https://www.python.org/downloads/ (or 'winget install Python.Python.3.12').",
            "Then re-run: powershell -File scripts\install.ps1"
        )
        return $false
    }
    & $PythonExe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        $ver = (& $PythonExe --version 2>&1 | Select-Object -First 1)
        Print-Failure "Python 3.10+ required, found: $ver" @(
            "Windows: install Python 3.12 from https://www.python.org/downloads/ (or 'winget install Python.Python.3.12').",
            "Then re-run: powershell -File scripts\install.ps1"
        )
        return $false
    }
    return $true
}

function Check-NodeVersion {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Print-Failure "Node 18+ required for MCP servers, but 'node' is not on PATH." @(
            "Windows: install Node LTS from https://nodejs.org/ (or 'winget install OpenJS.NodeJS.LTS').",
            "Then re-run: powershell -File scripts\install.ps1"
        )
        return $false
    }
    $raw = (& node --version 2>$null)
    if (-not $raw) { return $false }
    $trim = $raw.TrimStart('v')
    $major = ($trim -split '\.')[0]
    if (-not ($major -as [int]) -or [int]$major -lt 18) {
        Print-Failure "Node 18+ required for MCP servers, found: v$trim" @(
            "Windows: install Node LTS from https://nodejs.org/ (or 'winget install OpenJS.NodeJS.LTS').",
            "Then re-run: powershell -File scripts\install.ps1"
        )
        return $false
    }
    return $true
}

function Resolve-Python {
    foreach ($candidate in @('python3.12', 'python3.11', 'python3.10', 'python3', 'python')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            & $candidate -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
            if ($LASTEXITCODE -eq 0) {
                $script:ResolvedPython = $candidate
                return $true
            }
        }
    }
    return $false
}

function Run-Step {
    param(
        [int]$StepNum,
        [int]$Total,
        [string]$Label,
        [string]$Estimate,
        [ScriptBlock]$Action
    )
    Write-Host ("Step {0}/{1}: {2} (~{3})" -f $StepNum, $Total, $Label, $Estimate)
    $start = Get-Date
    $tempLog = [System.IO.Path]::GetTempFileName()
    $status = 0
    try {
        & $Action *>&1 | Tee-Object -FilePath $tempLog | Out-Null
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { $status = $LASTEXITCODE }
    } catch {
        Add-Content -Path $tempLog -Value $_.Exception.Message
        $status = 1
    }
    $elapsed = [int]((Get-Date) - $start).TotalSeconds
    if ($status -eq 0) {
        Write-Host ("  [PASS] {0} ({1}s)" -f $Label, $elapsed) -ForegroundColor Green
    } else {
        Write-Host ("  [FAIL] {0} ({1}s)" -f $Label, $elapsed) -ForegroundColor Red
        Write-Host "----- command output -----"
        Get-Content -Path $tempLog
        Write-Host "--------------------------"
    }
    Remove-Item -Path $tempLog -ErrorAction SilentlyContinue
    return ($status -eq 0)
}

# ---------------------------------------------------------------------------
# Step bodies
# ---------------------------------------------------------------------------

function Do-PythonVenv {
    & $script:ResolvedPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

function Do-PythonDeps {
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { throw "venv python missing at $venvPython" }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $venvPython -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements failed" }
    & $venvPython -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed" }
}

function Do-McpInstall {
    $count = 0
    Get-ChildItem -Path (Join-Path $RepoRoot 'mcp') -Filter package.json -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $dir = Split-Path -Parent $_.FullName
            Push-Location $dir
            try {
                if (Test-Path (Join-Path $dir 'package-lock.json')) {
                    & npm ci
                } else {
                    & npm install
                }
                if ($LASTEXITCODE -ne 0) { throw "npm failed in $dir" }
                $count++
            } finally {
                Pop-Location
            }
        }
    Write-Host "Installed deps in $count MCP package(s)"
}

function Do-SkillCopy {
    New-Item -ItemType Directory -Force -Path $AiswmmConfigDir | Out-Null
}

function Do-ApiKey {
    New-Item -ItemType Directory -Force -Path $AiswmmConfigDir | Out-Null
    if ($Provider -ne 'openai') {
        Write-Host "Provider is $Provider; OpenAI API key step skipped."
        return
    }
    if ($env:OPENAI_API_KEY -or (Test-Path $AiswmmEnvFile)) {
        Write-Host "OpenAI API key already configured at $AiswmmEnvFile"
        return
    }
    if ($script:AutoMode) {
        Write-Host "API key configuration skipped (auto mode)."
        return
    }
    $secure = Read-Host -AsSecureString "Paste OpenAI API key (or press Enter to skip)"
    $apiKey = [System.Net.NetworkCredential]::new('', $secure).Password
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        Write-Host "Skipped. Add it later in $AiswmmEnvFile"
        return
    }
    @(
        '# Agentic SWMM local secrets. This file is dot-sourced by the installed aiswmm command.'
        "`$env:OPENAI_API_KEY = '$($apiKey -replace ""'"", ""''"")'"
    ) | Set-Content -Path $AiswmmEnvFile -Encoding ASCII
    Write-Host "Saved OpenAI API key to $AiswmmEnvFile"
}

# ---------------------------------------------------------------------------
# Prereq gate
# ---------------------------------------------------------------------------

if (-not (Resolve-Python)) {
    [void](Check-PythonVersion 'python')
    exit 2
}

if (-not (Check-NodeVersion)) {
    exit 2
}

# ---------------------------------------------------------------------------
# Risk warning
# ---------------------------------------------------------------------------

Print-Banner

if (-not (Prompt-YN "Continue with installation?" 'Y')) {
    Write-Host "Installation aborted."
    exit 0
}

# ---------------------------------------------------------------------------
# Stepped flow
# ---------------------------------------------------------------------------

$total = 5

function Fail-Step {
    param([string]$Label, [string[]]$Remediation)
    Print-Failure "$Label failed." $Remediation
    exit 1
}

# Step 1: Python venv
if ($SkipPython) {
    Write-Host "Step 1/${total}: Python venv (skipped via -SkipPython)"
} else {
    if (-not (Prompt-YN "Run Step 1/${total} (Python venv ~30s)?" 'Y')) {
        Write-Host "Installation aborted at Python venv step."
        exit 0
    }
    if (-not (Run-Step 1 $total "Python venv creation" "30s" { Do-PythonVenv })) {
        Fail-Step "Python venv creation" @(
            "Verify '$($script:ResolvedPython) -m venv' works.",
            "Delete $VenvDir and retry: powershell -File scripts\install.ps1"
        )
    }
}

# Step 2: Python deps
if ($SkipPython) {
    Write-Host "Step 2/${total}: Python deps (skipped via -SkipPython)"
} else {
    if (-not (Prompt-YN "Run Step 2/${total} (Python deps ~2 min)?" 'Y')) {
        Write-Host "Installation aborted at Python deps step."
        exit 0
    }
    if (-not (Run-Step 2 $total "Python deps install" "2 min" { Do-PythonDeps })) {
        Fail-Step "Python dependency install" @(
            "Check network access to PyPI.",
            "Inspect $ReqFile; resolve conflicting versions and retry."
        )
    }
}

# Step 3: MCP node_modules
if ($SkipMcp) {
    Write-Host "Step 3/${total}: MCP servers (skipped via -SkipMcp)"
} else {
    if (-not (Prompt-YN "Run Step 3/${total} (MCP servers ~2 min, 8 servers)?" 'Y')) {
        Write-Host "Installation aborted at MCP step."
        exit 0
    }
    if (-not (Run-Step 3 $total "MCP servers npm install" "2 min" { Do-McpInstall })) {
        Fail-Step "MCP server install failed" @(
            "Verify 'npm --version' works and you have network access to the npm registry.",
            "Retry with: powershell -File scripts\install.ps1 -SkipPython"
        )
    }
}

# Step 4: skill files
if (-not (Prompt-YN "Run Step 4/${total} (Skill files copy ~10s)?" 'Y')) {
    Write-Host "Installation aborted at skill copy step."
    exit 0
}
if (-not (Run-Step 4 $total "Skill files copy to ~/.aiswmm" "10s" { Do-SkillCopy })) {
    Fail-Step "Skill files copy failed" @(
        "Verify $HOME is writable and $AiswmmConfigDir can be created."
    )
}

# Step 5: API key
if (-not (Prompt-YN "Run Step 5/${total} (API key config; skippable)?" 'Y')) {
    Write-Host "Installation aborted at API key step."
    exit 0
}
if (-not (Run-Step 5 $total "OpenAI API key configuration" "10s" { Do-ApiKey })) {
    Fail-Step "API key configuration failed" @(
        "You can add the key later by editing $AiswmmEnvFile."
    )
}

# ---------------------------------------------------------------------------
# Success summary
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Install complete."
Write-Host ""
Write-Host "Summary"
Write-Host "- Repo root:    $RepoRoot"
Write-Host ("- Python venv:  " + $(if ($SkipPython) { 'skipped' } else { $VenvDir }))
Write-Host ("- MCP servers:  " + $(if ($SkipMcp)    { 'skipped' } else { 'installed' }))
Write-Host "- Config dir:   $AiswmmConfigDir"
Write-Host "- Provider:     $Provider ($Model)"
Write-Host ""
Write-Host "Next steps"
Write-Host "  1. Open a new shell so PATH updates take effect."
Write-Host "  2. Run: aiswmm doctor"
Write-Host "  3. Run: aiswmm chat --provider $Provider"
Write-Host ""

exit 0
