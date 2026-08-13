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
    Write-Host '|  - Optionally store an API key (or pick later)    |'
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

function Get-HostArchitecture {
    # The machine's architecture, not the shell's. An x64 PowerShell on an
    # ARM64 machine reports AMD64 through PROCESSOR_ARCHITECTURE; Windows puts
    # the truth in PROCESSOR_ARCHITEW6432 in exactly that emulated case.
    if ($env:PROCESSOR_ARCHITEW6432) { return $env:PROCESSOR_ARCHITEW6432.ToUpper() }
    if ($env:PROCESSOR_ARCHITECTURE) { return $env:PROCESSOR_ARCHITECTURE.ToUpper() }
    return ''
}

function Get-PythonArchitecture {
    param([string]$Exe)
    try {
        $value = (& $Exe -c "import platform; print(platform.machine())" 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $value) { return $value.Trim().ToUpper() }
    } catch { }
    return ''
}

# Windows on ARM needs an x64 Python, and this is not a preference.
# shapely and pyogrio have never published a win_arm64 wheel: 130 and 21
# releases respectively, zero between them, for every Python from cp310 to
# cp314. On an ARM64 interpreter pip falls back to a source build and dies on
# "GDAL_VERSION must be provided as an environment variable", because building
# pyogrio needs a GDAL nobody has installed. The x64 wheels run fine under the
# emulation Windows 11 provides, which is what every working install on these
# machines has actually been doing.
function Resolve-Python {
    $candidates = @('python3.12', 'python3.11', 'python3.10', 'python3', 'python', 'py')
    $onArm = (Get-HostArchitecture) -eq 'ARM64'
    $fallback = ''
    foreach ($candidate in $candidates) {
        if (-not (Test-RealPython $candidate)) { continue }
        if (-not $onArm) {
            $script:ResolvedPython = $candidate
            return $true
        }
        if ((Get-PythonArchitecture $candidate) -eq 'AMD64') {
            $script:ResolvedPython = $candidate
            return $true
        }
        if (-not $fallback) { $fallback = $candidate }
    }
    if ($onArm -and $fallback) {
        # An ARM64-only Python is worse than none: the install appears to work
        # and then fails on the geospatial dependencies. Report it so the
        # caller can install the x64 build first.
        $script:ResolvedPython = $fallback
        $script:ResolvedPythonIsArm = $true
        return $true
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
    $onArm = (Get-HostArchitecture) -eq 'ARM64'
    $archArgs = @()
    if ($onArm) {
        Write-Host "Windows on ARM detected: installing the x64 Python build."
        Write-Host "  shapely and pyogrio publish no ARM64 wheels, so an ARM64 interpreter"
        Write-Host "  cannot install the geospatial dependencies at all. The x64 build runs"
        Write-Host "  under Windows' emulation and installs everything."
        $archArgs = @('--architecture', 'x64')
    }
    Write-Host "Installing Python 3.12 via winget (user scope, no admin)..."
    try {
        # Out-Host, not the output stream: `& winget` output otherwise lands in
        # this function's return value, making it a truthy array whatever the
        # install did, so the caller's `-not (Install-...)` never fired.
        & winget install -e --id Python.Python.3.12 --scope user --silent `
            @archArgs --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
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
        # Out-Host, not the output stream: `& winget` output otherwise lands in
        # this function's return value, making it a truthy array whatever the
        # install did, so the caller's `-not (Install-...)` never fired.
        & winget install -e --id OpenJS.NodeJS.LTS --silent `
            --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
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
    # Clear the exit code left behind by an earlier native command. $LASTEXITCODE
    # is sticky: a step body that runs no native command (Do-SkillCopy is a bare
    # New-Item) inherits the previous step's npm/pip failure and reports a false
    # [FAIL] in 0s with an empty output block.
    $global:LASTEXITCODE = 0
    $status = 0
    $captured = @()
    try {
        # Capture into a variable, not Tee-Object -> temp file: a terminating
        # error inside $Action tore the pipeline down before Tee flushed, so the
        # failure output was lost exactly when it was needed.
        $captured = @(& $Action *>&1)
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { $status = $LASTEXITCODE }
    } catch {
        $captured += $_.Exception.Message
        $status = 1
    }
    $elapsed = [int]((Get-Date) - $start).TotalSeconds
    if ($status -eq 0) {
        Write-Host ("  [PASS] {0} ({1}s)" -f $Label, $elapsed) -ForegroundColor Green
    } else {
        Write-Host ("  [FAIL] {0} ({1}s)" -f $Label, $elapsed) -ForegroundColor Red
        Write-Host "----- command output -----"
        # Write-Host, never Get-Content or a bare expression: anything this
        # function writes to the OUTPUT stream is appended to its return value,
        # so the caller's `-not (Run-Step ...)` saw a multi-element array (always
        # truthy) and skipped the failure branch. That silently walked past a
        # failed step AND swallowed the diagnostics the user needed.
        $lines = @($captured | ForEach-Object { ($_ | Out-String).TrimEnd() })
        if ($lines.Count -gt 60) {
            Write-Host ("... {0} earlier line(s) omitted ..." -f ($lines.Count - 60))
            $lines = $lines[-60..-1]
        }
        foreach ($line in $lines) { Write-Host $line }
        Write-Host "--------------------------"
    }
    return ($status -eq 0)
}

# ---------------------------------------------------------------------------
# Step bodies
# ---------------------------------------------------------------------------

function Get-VenvPythonVersion {
    # "version = 3.11.9" in pyvenv.cfg records the interpreter that built the
    # venv. Returns "3.11", or "" when there is no venv to ask.
    param([string]$Dir)
    $cfg = Join-Path $Dir 'pyvenv.cfg'
    if (-not (Test-Path $cfg)) { return "" }
    foreach ($line in Get-Content $cfg) {
        if ($line -match '^\s*version(_info)?\s*=\s*(\d+)\.(\d+)') {
            return "$($Matches[2]).$($Matches[3])"
        }
    }
    return ""
}

function Do-PythonVenv {
    # `python -m venv <existing dir>` does NOT rebuild it and does NOT clear
    # site-packages: it repoints pyvenv.cfg and Scripts at the new interpreter
    # and leaves the old packages in place. When the resolved interpreter
    # changes between installs (3.11 present first, winget adds 3.12 later,
    # Resolve-Python prefers 3.12) the result is a 3.12 venv full of cp311
    # binaries, and the first import fails with
    #   _multiarray_umath.cp311-win_amd64.pyd ... incompatible with cpython-312
    # pip does not notice, because the metadata says everything is installed.
    $wanted = (& $script:ResolvedPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Select-Object -First 1)
    $existing = Get-VenvPythonVersion $VenvDir
    if ($existing -and $wanted -and $existing -ne $wanted) {
        Write-Host "Existing venv was built with Python $existing; interpreter is now $wanted. Rebuilding."
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
    }
    & $script:ResolvedPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

function Test-VenvImports {
    # Returns the failure text, or "" when the wheels load. Native stderr is
    # captured deliberately: with $ErrorActionPreference = 'Stop', PowerShell
    # 5.1 turns the FIRST stderr line of a native command into a terminating
    # error, so a traceback printed straight through arrives as one useless
    # line ("Traceback (most recent call last):") and the reason is lost.
    param([string]$VenvPython)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $VenvPython -c "import numpy, matplotlib, pandas" 2>&1 | Out-String
        if ($LASTEXITCODE -eq 0) { return "" }
        return $output.Trim()
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Install-PythonPackages {
    param([string]$VenvPython)
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $VenvPython -m pip install -r $ReqFile
    if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements failed" }
    & $VenvPython -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed" }
}

function Do-PythonDeps {
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { throw "venv python missing at $venvPython" }
    Install-PythonPackages $venvPython

    # Prove the binary wheels actually load in THIS interpreter. pip reports
    # success from metadata alone, so an ABI mismatch stays silent until the
    # first plot fails hours later.
    $failure = Test-VenvImports $venvPython
    if ($failure) {
        # An already-corrupted venv reaches here: pyvenv.cfg agrees with the
        # interpreter (it was repointed, not rebuilt) so the version check in
        # Do-PythonVenv sees nothing wrong, while site-packages still holds
        # wheels built for the previous Python. pip then "succeeds" in seconds
        # because the metadata says installed. Rebuilding the venv is the only
        # thing that clears it, and doing it here means the user does not have
        # to know that: one re-run repairs the install.
        Write-Host "Installed packages do not import; rebuilding the virtualenv from scratch." -ForegroundColor Yellow
        Write-Host $failure
        Remove-Item -Recurse -Force $VenvDir -ErrorAction SilentlyContinue
        & $script:ResolvedPython -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "venv rebuild failed" }
        Install-PythonPackages $venvPython
        $failure = Test-VenvImports $venvPython
        if ($failure) {
            # Rebuilt and still broken: this is not a stale venv, so say what
            # Python actually reported instead of a generic dependency error.
            throw "installed packages still do not import after a clean rebuild:`n$failure"
        }
        Write-Host "Rebuild succeeded; the installed packages import cleanly." -ForegroundColor Green
    }

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
    # mcp/<server>/package.json only. -Recurse descended into node_modules left by
    # a previous install and ran npm in every nested dependency directory (minutes
    # of work, then a failure). Mirror of the bash installer's
    # `find "$REPO_ROOT/mcp" -mindepth 2 -maxdepth 2 -name package.json`.
    Get-ChildItem -Path (Join-Path $RepoRoot 'mcp') -Directory |
        ForEach-Object { Join-Path $_.FullName 'package.json' } |
        Where-Object { Test-Path $_ } |
        Sort-Object |
        ForEach-Object {
            $dir = Split-Path -Parent $_
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

$script:ResolvedPythonIsArm = $false
if ((Resolve-Python) -and $script:ResolvedPythonIsArm) {
    # Found a Python, but the wrong architecture for this machine's wheels.
    Write-Host "The Python on PATH is an ARM64 build; the geospatial dependencies have no" -ForegroundColor Yellow
    Write-Host "ARM64 wheels. Installing the x64 build alongside it." -ForegroundColor Yellow
    $script:ResolvedPythonIsArm = $false
    if (-not (Install-PythonViaWinget) -or $script:ResolvedPythonIsArm) {
        Print-Failure "An x64 Python is required on Windows on ARM." @(
            "shapely and pyogrio publish no win_arm64 wheels, so an ARM64 interpreter",
            "  cannot install them and pip falls back to a source build that needs GDAL.",
            "Install the x64 build and re-run:",
            "  winget install -e --id Python.Python.3.12 --architecture x64",
            "Or download the 'Windows installer (64-bit)' from https://www.python.org/downloads/"
        )
        exit 2
    }
}
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
        Fail-Step "MCP server install" @(
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
    Fail-Step "~/.aiswmm/ initialization" @(
        "Verify $HOME is writable and $AiswmmConfigDir can be created."
    )
}

# Step 5: API key
if (-not (Prompt-YN "Run Step 5/${total} (API key config; skippable)?" 'Y')) {
    Write-Host "Installation aborted at API key step."
    exit 0
}
if (-not (Run-Step 5 $total "OpenAI API key configuration" "10s" { Do-ApiKey })) {
    Fail-Step "API key configuration" @(
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
Write-Host "- AI provider:  choose after install (aiswmm setup)"
Write-Host ""
Write-Host "Next steps"
Write-Host "  1. Open a new shell so PATH updates take effect."
# Mirror of the bash installer's provider guidance: two numbered commands,
# never a menu. `aiswmm setup` is the interactive picker over the whole route
# table and lists the options itself; naming only OpenAI and Claude here hid
# every keyless route, and reprinting the full list here just moved the
# confusion instead of removing it.
# Hand straight over to the picker instead of printing a command and hoping.
# Guards, in order: an explicit opt-out for scripted installs, a real
# interactive console (CI runs this with stdin redirected and would hang on
# the first prompt), a venv to run it from, and nothing configured yet.
$venvAiswmm = Join-Path $VenvDir 'Scripts\aiswmm.exe'
$canPrompt = $false
if ($env:AISWMM_NO_SETUP -ne '1' -and [Environment]::UserInteractive) {
    try { $canPrompt = -not [Console]::IsInputRedirected } catch { $canPrompt = $false }
}
if ($canPrompt -and (Test-Path $venvAiswmm) -and
    -not (Test-Path (Join-Path $AiswmmConfigDir 'setup_state.json'))) {
    Write-Host ""
    Write-Host "Setting up your AI provider now. Pick a route; some need no API key."
    Write-Host ""
    & $venvAiswmm setup
    Write-Host ""
}

if (Test-Path (Join-Path $AiswmmConfigDir 'setup_state.json')) {
    # Already ran the picker. The key-file probe below cannot see this: the
    # keyless routes (codex gateway, ollama, lmstudio) never write one, so a
    # returning codex user was told to go pick a provider they had picked.
    Write-Host "  2. Start: aiswmm            (change provider any time: aiswmm setup)"
} elseif ($Provider -ne 'openai') {
    Write-Host "  2. Store your $Provider API key: aiswmm login --$Provider"
    Write-Host "  3. Start: aiswmm"
} elseif (-not $env:OPENAI_API_KEY -and -not (Test-Path $AiswmmEnvFile)) {
    Write-Host "  2. Run: aiswmm setup      (pick your AI provider; some need no API key)"
    Write-Host "  3. Start: aiswmm"
} else {
    Write-Host "  2. Start: aiswmm            (change provider any time: aiswmm setup)"
}
Write-Host ""
Write-Host "  Something looks wrong? aiswmm doctor"
Write-Host ""

exit 0
