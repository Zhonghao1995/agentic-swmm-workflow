# scripts/install.ps1
#
# Stepped interactive installer for the Agentic Stormwater Modeling
# Workflow (AISWMM) on Windows. Mirrors the bash flow:
#
#   1. Prereq checks (Python >=3.10, Node >=18)
#   2. Risk-warning banner -> Y/n confirm
#   3. Per-step Y/n: venv, python deps, MCP npm, skill files, API key, SWMM engine
#   4. Success summary + next-step hint
#
# Flags:
#   -Auto             Skip all prompts (CI / scripted install).
#   -Yes              Legacy alias for -Auto.
#   -SkipPython       Skip Python venv + Python deps steps.
#   -SkipMcp          Skip MCP server npm install step.
#   -Provider <name>  LLM provider to register (default: openai).
#   -Model <name>     LLM model to register (default: gpt-5.5).
#
# N at any prompt exits 0 with "Installation aborted." Failure at any
# step prints a remediation hint and exits non-zero.

param(
    [switch]$Auto,
    [switch]$Yes,
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [string]$Provider = "openai",
    [string]$Model = "gpt-5.5"
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

# Set true when Node cannot be provisioned; Step 3 (MCP) is then skipped non-fatally.
$script:SkipMcpAuto = $false

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

function Update-SessionPath {
    # winget writes the updated PATH to the registry but does not refresh the
    # current process, so a freshly installed interpreter is invisible until a
    # new shell. Re-read Machine+User PATH and add the well-known winget
    # user-scope Python dir so Resolve-Python can find it in THIS session.
    $parts = @(
        [System.Environment]::GetEnvironmentVariable('PATH', 'Machine')
        [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    )
    $pyBase = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path $pyBase) {
        $pyDirs = Get-ChildItem -Path $pyBase -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName; Join-Path $_.FullName 'Scripts' }
        $parts += $pyDirs
    }
    $env:PATH = (($parts | Where-Object { $_ }) -join ';') + ';' + $env:PATH
}

function Test-RealPython {
    param([string]$Exe)
    # Windows ships python.exe/python3.exe "App execution alias" stubs under
    # WindowsApps that sit on PATH even with no Python installed; running one
    # prints "Python was not found" and opens the Store. Reject those by their
    # source path, then probe for a real Python >= 3.10.
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    if ($cmd.Source -and $cmd.Source -like '*\WindowsApps\*') { return $false }
    try {
        & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Resolve-Python {
    foreach ($candidate in @('python3.12', 'python3.11', 'python3.10', 'python3', 'python', 'py')) {
        if (Test-RealPython $candidate) {
            $script:ResolvedPython = $candidate
            return $true
        }
    }
    return $false
}

function Test-NodeOk {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    $raw = (& node --version 2>$null)
    if (-not $raw) { return $false }
    $major = (($raw.TrimStart('v')) -split '\.')[0]
    return (($major -as [int]) -and [int]$major -ge 18)
}

function Install-PythonViaWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    Write-Host "Python 3.10+ not found; installing Python 3.12 via winget (user scope, no admin)..."
    try {
        & winget install -e --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements
    } catch {
        Write-Host "winget Python install raised: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    Update-SessionPath
    return (Resolve-Python)
}

function Install-NodeViaWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    Write-Host "Node 18+ not found; installing Node.js LTS via winget..."
    try {
        & winget install -e --id OpenJS.NodeJS.LTS --silent `
            --accept-package-agreements --accept-source-agreements
    } catch {
        Write-Host "winget Node install raised: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    Update-SessionPath
    return (Test-NodeOk)
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

    # Put the venv's Scripts dir on PATH so `aiswmm` resolves. pip -e drops
    # aiswmm.exe there, but nothing else adds it to PATH — without this the
    # install finishes and `aiswmm` is "not recognized". Persist to the user
    # PATH (new shells) and update this process (current shell works too).
    $venvScripts = Join-Path $VenvDir 'Scripts'
    $userPath = [System.Environment]::GetEnvironmentVariable('PATH', 'User')
    if (($userPath -split ';') -notcontains $venvScripts) {
        $combined = if ([string]::IsNullOrEmpty($userPath)) { $venvScripts } else { "$userPath;$venvScripts" }
        [System.Environment]::SetEnvironmentVariable('PATH', $combined, 'User')
    }
    if (($env:PATH -split ';') -notcontains $venvScripts) { $env:PATH = "$env:PATH;$venvScripts" }
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
        # Other providers are pointed at `aiswmm login` from the always-visible
        # Next steps block; Run-Step hides this step's output on success.
        Write-Host "Provider is $Provider; the OpenAI key step does not apply."
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
    # Restrict ACL on the env file — mirror of `chmod 600` in install.sh.
    # The previous version inherited the default ACL (world-readable on most
    # workstations). Strip inheritance, drop all ACEs, then grant the current
    # user FullControl only. P1-3 in #79.
    try {
        $acl = Get-Acl $AiswmmEnvFile
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($rule in @($acl.Access)) { $null = $acl.RemoveAccessRule($rule) }
        $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $user, 'FullControl', 'Allow'
        )
        $acl.AddAccessRule($rule)
        Set-Acl -Path $AiswmmEnvFile -AclObject $acl
    } catch {
        Write-Host "Warning: could not restrict ACL on $AiswmmEnvFile ($($_.Exception.Message))" -ForegroundColor Yellow
    }
    Write-Host "Saved OpenAI API key to $AiswmmEnvFile"
}

function Do-SwmmEngine {
    # Download the pinned EPA SWMM 5.2.4 Windows solver into $AiswmmConfigDir\swmm
    # so runs use the same 5.2.4 engine. The official release bundles its own
    # MSVC + OpenMP runtime DLLs, and Windows searches the application directory
    # first for DLLs, so co-locating them with runswmm.exe needs no wrapper.
    # resolve_swmm5()/doctor look in this fixed directory.
    $swmmDir = Join-Path $AiswmmConfigDir 'swmm'
    $swmm5 = Join-Path $swmmDir 'swmm5.exe'
    New-Item -ItemType Directory -Force -Path $swmmDir | Out-Null
    if (Test-Path $swmm5) {
        $existing = (& $swmm5 --version 2>$null | Out-String)
        if ($existing -match '5\.2\.4') {
            Write-Host "swmm5 5.2.4 already installed at $swmm5"
            return
        }
    }
    $url = 'https://github.com/USEPA/Stormwater-Management-Model/releases/download/v5.2.4/swmm-solver-5.2.4-win64.zip'
    $work = Join-Path ([System.IO.Path]::GetTempPath()) ('aiswmm-swmm-' + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $zip = Join-Path $work 'swmm.zip'
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $work -Force
        $bin = Get-ChildItem -Path $work -Recurse -Directory -Filter 'bin' | Select-Object -First 1
        if (-not $bin) { throw 'bin/ directory not found in the downloaded SWMM archive' }
        Copy-Item -Path (Join-Path $bin.FullName '*') -Destination $swmmDir -Force
        $runswmm = Join-Path $swmmDir 'runswmm.exe'
        if (-not (Test-Path $runswmm)) { throw 'runswmm.exe not found in the downloaded SWMM archive' }
        # Canonical name the runner/doctor look for first; keep runswmm.exe too.
        Copy-Item -Path $runswmm -Destination $swmm5 -Force
    } finally {
        Remove-Item -Path $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    $ver = (& $swmm5 --version 2>$null | Out-String)
    if ($ver -match '5\.2\.4') {
        Write-Host "Installed swmm5 5.2.4 -> $swmm5"
    } else {
        throw "swmm5 installed but did not report version 5.2.4"
    }
}

# ---------------------------------------------------------------------------
# Prereq gate
# ---------------------------------------------------------------------------

if (-not (Resolve-Python)) {
    if (-not (Install-PythonViaWinget)) {
        Print-Failure "Python 3.10+ is required and could not be installed automatically." @(
            "Install Python 3.12 from https://www.python.org/downloads/ (check 'Add python.exe to PATH'),",
            "or run: winget install -e --id Python.Python.3.12",
            "If typing 'python' opens the Microsoft Store, turn off the python.exe / python3.exe",
            "  App execution aliases (Settings > Apps > Advanced app settings), then re-run."
        )
        exit 2
    }
}

if (-not (Test-NodeOk)) {
    if (-not (Install-NodeViaWinget)) {
        Write-Host "[WARN] Node 18+ unavailable and auto-install failed; MCP servers will be skipped." -ForegroundColor Yellow
        Write-Host "       Core runtime (SWMM run/audit/plot) still installs. Add Node later and re-run for MCP." -ForegroundColor Yellow
        $script:SkipMcpAuto = $true
    }
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

$total = 6

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
if ($SkipMcp -or $script:SkipMcpAuto) {
    Write-Host "Step 3/${total}: MCP servers (skipped)"
} else {
    if (-not (Prompt-YN "Run Step 3/${total} (MCP servers ~2 min, 11 servers)?" 'Y')) {
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

# Step 4: initialize ~/.aiswmm/ (real skill deployment runs via `aiswmm setup`)
if (-not (Prompt-YN "Run Step 4/${total} (Initialize ~/.aiswmm/ ~5s)?" 'Y')) {
    Write-Host "Installation aborted at config-directory step."
    exit 0
}
if (-not (Run-Step 4 $total "Initialize ~/.aiswmm/ directory" "5s" { Do-SkillCopy })) {
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

# Step 6: SWMM solver engine (NON-FATAL). The rest of the install stays usable
# even if the download fails (offline); `aiswmm doctor` reports a missing engine
# with how to fix it, so we warn and continue here instead of aborting.
if (-not (Prompt-YN "Run Step 6/${total} (Download SWMM 5.2.4 engine ~1 min)?" 'Y')) {
    Write-Host "Skipped SWMM engine. Install swmm5 yourself or re-run later; 'aiswmm doctor' confirms status."
} else {
    if (-not (Run-Step 6 $total "SWMM 5.2.4 engine download" "1 min" { Do-SwmmEngine })) {
        Print-Failure "SWMM engine install failed (non-fatal)." @(
            "The rest of the install is fine; this only affects running models locally.",
            "Re-run the installer, or download swmm5 5.2.4 yourself; 'aiswmm doctor' shows status."
        )
    }
}

# ---------------------------------------------------------------------------
# Success summary
# ---------------------------------------------------------------------------

$swmm5Bin = Join-Path (Join-Path $AiswmmConfigDir 'swmm') 'swmm5.exe'
$swmmStatus = if (Test-Path $swmm5Bin) {
    $v = (& $swmm5Bin --version 2>$null | Out-String)
    if ($v -match '5\.2\.4') { 'installed (5.2.4)' } else { 'present (version unknown)' }
} else { "not installed (run 'aiswmm doctor')" }

Write-Host ""
Write-Host "Install complete."
Write-Host ""
Write-Host "Summary"
Write-Host "- Repo root:    $RepoRoot"
Write-Host ("- Python venv:  " + $(if ($SkipPython) { 'skipped' } else { $VenvDir }))
Write-Host ("- MCP servers:  " + $(if ($SkipMcp -or $script:SkipMcpAuto) { 'skipped' } else { 'installed' }))
Write-Host "- SWMM engine:  $swmmStatus"
Write-Host "- Config dir:   $AiswmmConfigDir"
Write-Host "- AI provider:  choose after install (OpenAI or Claude)"
Write-Host ""
Write-Host "Next steps"
Write-Host "  1. Open a new shell so PATH updates take effect."
Write-Host "  2. Run: aiswmm doctor"
Write-Host "  3. Choose your AI provider and store your key (the only manual step):"
Write-Host "       OpenAI:  aiswmm login              (optional: pick a model with 'aiswmm model')"
Write-Host "       Claude:  aiswmm login --anthropic"
Write-Host "  4. Start: aiswmm"
Write-Host ""

exit 0
