param(
    [switch]$Yes,
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [switch]$SkipSwmm,
    [switch]$InstallSystemDeps,
    [string]$SwmmExe,
    [string]$SwmmVersion = "5.2.4"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$VenvDir = Join-Path $RepoRoot '.venv'
$ReqFile = Join-Path $ScriptDir 'requirements.txt'

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

function Ensure-WindowsToolchain {
    Ensure-Chocolatey
    Ensure-Admin
    Write-Step "Installing Windows toolchain packages with Chocolatey"
    choco upgrade git python nodejs-lts -y --no-progress
    Refresh-ChocolateyEnvironment
}

function Resolve-PythonCommand {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return 'python'
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return 'py -3'
    }
    Fail "Python is unavailable after installation."
}

function Ensure-Python {
    if ((Get-Command python -ErrorAction SilentlyContinue) -or (Get-Command py -ErrorAction SilentlyContinue)) {
        return
    }
    if (-not $InstallSystemDeps) {
        Fail "Python is not available. Install Python, or re-run with -InstallSystemDeps from an elevated PowerShell session."
    }
    Ensure-WindowsToolchain
}

function Ensure-Node {
    if ((Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        return
    }
    if (-not $InstallSystemDeps) {
        Fail "Node.js/npm are not available. Install Node.js, or re-run with -InstallSystemDeps from an elevated PowerShell session."
    }
    Ensure-WindowsToolchain
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
    $pythonCmd = Resolve-PythonCommand
    Write-Step "Creating virtualenv at $VenvDir"
    Invoke-Expression "$pythonCmd -m venv `"$VenvDir`""
    $venvPython = Join-Path $VenvDir 'Scripts\python.exe'
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r $ReqFile
    & $venvPython -m pip install -e $RepoRoot
}

function Install-McpRequirements {
    Ensure-Node
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
}

if (-not $SkipMcp) {
    Install-McpRequirements
}

Ensure-Swmm

Write-Host ""
Write-Host "Install summary"
Write-Host "- Repo root: $RepoRoot"
Write-Host "- Python setup: $(if ($SkipPython) { 'skipped (--skip-python)' } else { 'installed (.venv + scripts/requirements.txt + agentic-swmm CLI)' })"
Write-Host "- MCP npm setup: $(if ($SkipMcp) { 'skipped (--skip-mcp)' } else { 'installed' })"
Write-Host "- SWMM check: $(Get-SwmmStatus)"
Write-Host ""
Write-Host "Next steps"
Write-Host "1. Activate the virtualenv: .\.venv\Scripts\Activate.ps1"
Write-Host "2. Check the CLI: agentic-swmm doctor"
Write-Host "3. Run acceptance: agentic-swmm demo acceptance --run-id latest"
Write-Host "4. Real-data smoke test: python scripts/real_cases/run_todcreek_minimal.py"
