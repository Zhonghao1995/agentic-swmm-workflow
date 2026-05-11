param(
    [switch]$Yes,
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [switch]$SkipSwmm,
    [switch]$SkipSetup,
    [switch]$InstallSystemDeps,
    [string]$SwmmExe,
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
$CliBinDir = if ($env:AISWMM_CLI_BIN_DIR) { $env:AISWMM_CLI_BIN_DIR } else { Join-Path $env:LOCALAPPDATA 'AgenticSWMM\bin' }
$script:PythonExe = $null
$script:PythonArgs = @()

function Write-Step {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Fail {
    param([string]$Message)
    throw $Message
}

function Ensure-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Fail "Administrator privileges are required for system package installation. Re-run from an elevated PowerShell session, or use -SkipSwmm / -SwmmExe for user-space setup."
    }
}

function Ensure-Confirmation {
    if ($Yes) {
        return
    }
    $reply = Read-Host "This will install dependencies for $RepoRoot. Continue? [y/N]"
    if ($reply -notin @('y', 'Y', 'yes', 'YES')) {
        Write-Host 'Aborted.'
        exit 0
    }
}

function Ensure-Chocolatey {
    if (Get-Command choco -ErrorAction SilentlyContinue) {
        return
    }
    Ensure-Admin
    Write-Step "Installing Chocolatey"
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
    Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
}

function Refresh-ChocolateyEnvironment {
    if (-not $env:ChocolateyInstall) {
        $env:ChocolateyInstall = Join-Path $env:ProgramData 'chocolatey'
    }
    $profileModule = Join-Path $env:ChocolateyInstall 'helpers\chocolateyProfile.psm1'
    if (Test-Path $profileModule) {
        Import-Module $profileModule -Force
        refreshenv
    }
}

function ConvertTo-PowerShellSingleQuoted {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Write-OpenAiEnvFile {
    param([string]$ApiKey)
    New-Item -ItemType Directory -Force -Path $AiswmmConfigDir | Out-Null
    @(
        '# Agentic SWMM local secrets. This file is dot-sourced by the installed aiswmm command.'
        "`$env:OPENAI_API_KEY = $(ConvertTo-PowerShellSingleQuoted $ApiKey)"
    ) | Set-Content -Path $AiswmmEnvFile -Encoding ASCII
    Write-Step "Saved OpenAI API key to $AiswmmEnvFile"
}

function Import-AiswmmEnvFile {
    if (Test-Path $AiswmmEnvFile) {
        . $AiswmmEnvFile
    }
}

function Prompt-OpenAiApiKey {
    if ($Provider -ne 'openai' -or $env:OPENAI_API_KEY -or (Test-Path $AiswmmEnvFile)) {
        return
    }

    Write-Host ""
    Write-Host "OpenAI API key"
    Write-Host "  - Paste a key now to enable aiswmm chat immediately."
    Write-Host "  - Press Enter to do it later."
    $apiKey = Read-Host "OpenAI API key [do it later]"
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        Write-Step "OpenAI API key skipped; you can add it later in $AiswmmEnvFile"
        return
    }
    Write-OpenAiEnvFile -ApiKey $apiKey
    $env:OPENAI_API_KEY = $apiKey
}

function Add-UserPathEntry {
    param([string]$PathEntry)
    $currentUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ([string]::IsNullOrWhiteSpace($currentUserPath)) {
        $parts = @()
    } else {
        $parts = $currentUserPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    }
    $alreadyPresent = $false
    foreach ($part in $parts) {
        if ($part.TrimEnd('\') -ieq $PathEntry.TrimEnd('\')) {
            $alreadyPresent = $true
            break
        }
    }
    if (-not $alreadyPresent) {
        $newUserPath = (@($parts) + $PathEntry) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newUserPath, 'User')
        Write-Step "Added $PathEntry to the user PATH for new terminals"
    }
    if (($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') }) -notcontains $PathEntry.TrimEnd('\')) {
        $env:Path = "$PathEntry;$env:Path"
    }
}

function Install-CliShims {
    if ($SkipPython) {
        return
    }
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Fail "Cannot install CLI shims because virtualenv Python is missing: $venvPython"
    }

    New-Item -ItemType Directory -Force -Path $CliBinDir | Out-Null
    New-Item -ItemType Directory -Force -Path $AiswmmConfigDir | Out-Null

    foreach ($name in @('aiswmm', 'agentic-swmm')) {
        $ps1Path = Join-Path $CliBinDir "$name.ps1"
        $cmdPath = Join-Path $CliBinDir "$name.cmd"
        @(
            'param([Parameter(ValueFromRemainingArguments = $true)][string[]]$RemainingArgs)'
            '$ErrorActionPreference = ''Stop'''
            "`$envFile = $(ConvertTo-PowerShellSingleQuoted $AiswmmEnvFile)"
            'if (Test-Path $envFile) { . $envFile }'
            "`$python = $(ConvertTo-PowerShellSingleQuoted $venvPython)"
            '& $python -m agentic_swmm.cli @RemainingArgs'
            'exit $LASTEXITCODE'
        ) | Set-Content -Path $ps1Path -Encoding ASCII
        @(
            '@echo off'
            "powershell -NoProfile -ExecutionPolicy Bypass -File `"$ps1Path`" %*"
        ) | Set-Content -Path $cmdPath -Encoding ASCII
    }

    Add-UserPathEntry -PathEntry $CliBinDir
    Write-Step "Installed CLI shims: $CliBinDir\aiswmm.cmd and $CliBinDir\agentic-swmm.cmd"
}

function Ensure-WindowsToolchain {
    Ensure-Chocolatey
    Ensure-Admin
    Write-Step "Installing Windows toolchain packages with Chocolatey"
    choco upgrade git python nodejs-lts -y --no-progress
    Refresh-ChocolateyEnvironment
}

function Add-UserPythonPath {
    $roots = Get-UserPythonRoots
    foreach ($root in $roots) {
        if (Test-Path $root) {
            $scripts = Join-Path $root 'Scripts'
            $env:Path = "$root;$scripts;$env:Path"
        }
    }
}

function Get-UserPythonRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    $base = Join-Path $env:LOCALAPPDATA 'Programs\Python'

    foreach ($name in @('Python312', 'Python311', 'Python310')) {
        $path = Join-Path $base $name
        if (Test-Path $path) {
            $roots.Add($path)
        }
    }

    if (Test-Path $base) {
        Get-ChildItem -Path $base -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                if (-not $roots.Contains($_.FullName)) {
                    $roots.Add($_.FullName)
                }
            }
    }

    return $roots.ToArray()
}

function Get-PythonCandidates {
    $items = New-Object System.Collections.Generic.List[object]

    foreach ($root in Get-UserPythonRoots) {
        $exe = Join-Path $root 'python.exe'
        if (Test-Path $exe) {
            $items.Add(@{ Exe = $exe; Args = @() })
        }
    }

    foreach ($candidate in @(
        @{ Exe = 'py'; Args = @('-3.12') },
        @{ Exe = 'py'; Args = @('-3.11') },
        @{ Exe = 'py'; Args = @('-3.10') },
        @{ Exe = 'python3.12'; Args = @() },
        @{ Exe = 'python3.11'; Args = @() },
        @{ Exe = 'python3.10'; Args = @() },
        @{ Exe = 'python'; Args = @() }
    )) {
        $items.Add($candidate)
    }

    return $items.ToArray()
}

function Try-InstallPythonWithWinget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        return $false
    }
    Write-Step "Installing Python 3.12 with winget for the current user"
    & winget install --id Python.Python.3.12 --exact --source winget --scope user --accept-package-agreements --accept-source-agreements --silent
    Add-UserPythonPath
    return $true
}

function Resolve-Python310 {
    Add-UserPythonPath
    $candidates = Get-PythonCandidates

    foreach ($candidate in $candidates) {
        if (-not ((Test-Path $candidate.Exe) -or (Get-Command $candidate.Exe -ErrorAction SilentlyContinue))) {
            continue
        }
        $probeArgs = @($candidate.Args) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)')
        try {
            & $candidate.Exe @probeArgs *> $null
            if ($LASTEXITCODE -ne 0) {
                continue
            }
            $versionArgs = @($candidate.Args) + @('--version')
            $version = (& $candidate.Exe @versionArgs 2>&1 | Select-Object -First 1)
            $script:PythonExe = $candidate.Exe
            $script:PythonArgs = @($candidate.Args)
            $suffix = ($candidate.Args -join ' ')
            if ($suffix) {
                Write-Step "Using $version via $($candidate.Exe) $suffix"
            } else {
                Write-Step "Using $version via $($candidate.Exe)"
            }
            return $true
        } catch {
            continue
        }
    }
    return $false
}

function Invoke-ResolvedPython {
    param([string[]]$Arguments)
    if (-not $script:PythonExe) {
        Fail "Python 3.10+ has not been resolved."
    }
    $allArgs = @($script:PythonArgs) + $Arguments
    & $script:PythonExe @allArgs
}

function Ensure-Python {
    if (Resolve-Python310) {
        return
    }
    if ($InstallSystemDeps) {
        Ensure-WindowsToolchain
    } else {
        try {
            if (-not (Try-InstallPythonWithWinget)) {
                Fail "Python 3.10+ is not available. Install Python 3.10+, or re-run with -InstallSystemDeps from an elevated PowerShell session."
            }
        } catch {
            Fail "Python 3.10+ is not available and winget user install failed. Install Python 3.10+, or re-run with -InstallSystemDeps from an elevated PowerShell session. Details: $_"
        }
    }
    if (-not (Resolve-Python310)) {
        Fail "Python 3.10+ is still unavailable after installation."
    }
}

function Ensure-Node {
    if ((Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        return $true
    }
    if (-not $InstallSystemDeps) {
        Write-Warning "Node.js/npm are not available. Skipping MCP npm dependency installation. Install Node.js later and re-run .\scripts\install.ps1 -Yes -SkipPython -SkipSwmm."
        return $false
    }
    Ensure-WindowsToolchain
    return ((Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue))
}

function Add-SwmmShim {
    param([string]$Target)

    if (-not (Test-Path $Target)) {
        Fail "SWMM executable does not exist: $Target"
    }

    $shimDir = Join-Path $RepoRoot '.local\bin'
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    $targetDir = Split-Path -Parent $Target
    if ([System.IO.Path]::GetExtension($Target) -ieq '.exe') {
        Get-ChildItem -Path $targetDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -in @('.exe', '.dll') } |
            ForEach-Object {
                Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $shimDir $_.Name) -Force
            }
        Copy-Item -LiteralPath $Target -Destination (Join-Path $shimDir 'swmm5.exe') -Force
    }

    $shimPath = Join-Path $shimDir 'swmm5.cmd'
    @(
        '@echo off'
        "`"$Target`" %*"
    ) | Set-Content -Path $shimPath -Encoding ASCII

    $env:Path = "$shimDir;$env:Path"
    Write-Step "Created swmm5 shim at $shimPath"
}

function Find-SwmmExecutable {
    if ($SwmmExe) {
        return $SwmmExe
    }

    $cmd = Get-Command swmm5 -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    if (Get-Command swmm5 -ErrorAction SilentlyContinue) {
        return (Get-Command swmm5).Source
    }

    $candidates = @()
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path $root)) {
            continue
        }
        $candidates += Get-ChildItem -Path $root -Filter runswmm.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
        $candidates += Get-ChildItem -Path $root -Filter swmm5.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
        $candidates += Get-ChildItem -Path $root -Filter epaswmm5.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
    }

    $target = $candidates | Select-Object -First 1
    if (-not $target) {
        return $null
    }

    return $target
}

function Install-SwmmReleaseZip {
    $releaseTag = "v$SwmmVersion"
    $archiveName = "swmm-solver-$SwmmVersion-win64.zip"
    $downloadUrl = "https://github.com/USEPA/Stormwater-Management-Model/releases/download/$releaseTag/$archiveName"
    $installRoot = Join-Path $RepoRoot ".local\swmm\$SwmmVersion"
    $archivePath = Join-Path $env:TEMP $archiveName

    Write-Step "Downloading USEPA SWMM $SwmmVersion solver from $downloadUrl"
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath

    Write-Step "Extracting SWMM $SwmmVersion to $installRoot"
    $extractDir = Join-Path $installRoot 'extract'
    if (Test-Path $extractDir) {
        Remove-Item -Recurse -Force $extractDir
    }
    Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force

    $runswmm = Get-ChildItem -Path $extractDir -Filter runswmm.exe -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $runswmm) {
        Fail "Unable to locate runswmm.exe in downloaded SWMM archive: $downloadUrl"
    }

    return $runswmm
}

function Ensure-Swmm {
    if ($SkipSwmm) {
        return
    }
    if (Get-Command swmm5 -ErrorAction SilentlyContinue) {
        return
    }

    $existing = Find-SwmmExecutable
    if ($existing) {
        Add-SwmmShim -Target $existing
        return
    }

    if (-not $InstallSystemDeps) {
        $downloaded = Install-SwmmReleaseZip
        Add-SwmmShim -Target $downloaded
        return
    }

    Ensure-Chocolatey
    Ensure-Admin
    Write-Step "Installing SWMM $SwmmVersion with Chocolatey"
    choco upgrade swmm --version $SwmmVersion -y --no-progress
    Refresh-ChocolateyEnvironment
    $installed = Find-SwmmExecutable
    if (-not $installed) {
        Fail "Unable to locate a SWMM executable after installation. Install EPA SWMM manually and re-run with -SwmmExe `"C:\Path\To\runswmm.exe`"."
    }
    Add-SwmmShim -Target $installed
    if (-not (Get-Command swmm5 -ErrorAction SilentlyContinue)) {
        Fail "SWMM installation completed, but swmm5 is still unavailable."
    }
}

function Install-PythonRequirements {
    Ensure-Python
    if (-not (Test-Path $ReqFile)) {
        Fail "Missing requirements file: $ReqFile"
    }
    Write-Step "Creating virtualenv at $VenvDir"
    Invoke-ResolvedPython -Arguments @('-m', 'venv', $VenvDir)
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Fail "Virtualenv Python was not created at $venvPython"
    }
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $ReqFile
    & $venvPython -m pip install -e $RepoRoot
}

function Install-McpRequirements {
    if (-not (Ensure-Node)) {
        return
    }
    $npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
    if (-not $npmCmd) {
        $npmCmd = (Get-Command npm -ErrorAction SilentlyContinue)
    }
    if (-not $npmCmd) {
        Fail "npm is unavailable."
    }
    Get-ChildItem -Path (Join-Path $RepoRoot 'skills') -Filter package.json -Recurse |
        Where-Object { $_.FullName -like '*\scripts\mcp\package.json' } |
        Sort-Object FullName |
        ForEach-Object {
            $dir = Split-Path -Parent $_.FullName
            Write-Step "Installing MCP deps in $dir"
            Push-Location $dir
            try {
                if (Test-Path (Join-Path $dir 'package-lock.json')) {
                    & $npmCmd.Source ci
                } else {
                    & $npmCmd.Source install
                }
            } finally {
                Pop-Location
            }
        }
}

function Invoke-AiswmmSetup {
    if ($SkipSetup -or $SkipPython) {
        return
    }
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    Write-Step "Configuring Agentic SWMM orchestration layer"
    & $venvPython -m agentic_swmm.cli setup --provider $Provider --model $Model
}

function Get-SwmmStatus {
    if (Get-Command swmm5 -ErrorAction SilentlyContinue) {
        try {
            $ver = (& swmm5 --version 2>$null | Select-Object -First 1)
        } catch {
            $ver = $null
        }
        if ($ver) {
            return "found at $((Get-Command swmm5).Source) ($ver)"
        }
        return "found at $((Get-Command swmm5).Source)"
    }
    return 'missing'
}

Ensure-Confirmation

if (-not $SkipPython) {
    Install-PythonRequirements
    Install-CliShims
}

if (-not $SkipMcp) {
    Install-McpRequirements
}

Ensure-Swmm
Prompt-OpenAiApiKey
Import-AiswmmEnvFile
Invoke-AiswmmSetup

Write-Host ""
Write-Host "Install summary"
Write-Host "- Repo root: $RepoRoot"
Write-Host "- Python setup: $(if ($SkipPython) { 'skipped (--skip-python)' } else { 'installed (.venv + scripts/requirements.txt + agentic-swmm CLI)' })"
Write-Host "- CLI command: $(if ($SkipPython) { 'skipped' } else { "$CliBinDir\aiswmm.cmd" })"
Write-Host "- MCP npm setup: $(if ($SkipMcp) { 'skipped (--skip-mcp)' } else { 'installed' })"
Write-Host "- Agentic SWMM setup: $(if ($SkipSetup -or $SkipPython) { 'skipped' } else { "registered provider=$Provider model=$Model skills/MCP/memory" })"
Write-Host "- OpenAI API key: $(if ($env:OPENAI_API_KEY -or (Test-Path $AiswmmEnvFile)) { 'configured' } else { 'not configured' })"
Write-Host "- SWMM check: $(Get-SwmmStatus)"
Write-Host ""
Write-Host "Run now"
Write-Host "1. Check the runtime: aiswmm doctor"
Write-Host "2. Start local orchestration chat: aiswmm chat --provider $Provider `"Explain what this Agentic SWMM installation can do`""
Write-Host "3. Run acceptance: aiswmm demo acceptance --run-id latest"
Write-Host "4. Open report: $RepoRoot\runs\acceptance\latest\acceptance_report.md"
