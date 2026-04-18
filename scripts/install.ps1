param(
    [switch]$Yes,
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [switch]$SkipSwmm
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
        Fail "Run scripts/install.ps1 from an elevated PowerShell session."
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
    if (Get-Command python -ErrorAction SilentlyContinue -or Get-Command py -ErrorAction SilentlyContinue) {
        return
    }
    Ensure-WindowsToolchain
}

function Ensure-Node {
    if ((Get-Command node -ErrorAction SilentlyContinue) -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        return
    }
    Ensure-WindowsToolchain
}

function Ensure-SwmmShim {
    if (Get-Command swmm5 -ErrorAction SilentlyContinue) {
        return
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
        Fail "Unable to locate a SWMM executable after installation."
    }

    if (-not $env:ChocolateyInstall) {
        $env:ChocolateyInstall = Join-Path $env:ProgramData 'chocolatey'
    }
    $shimDir = Join-Path $env:ChocolateyInstall 'bin'
    New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
    $shimPath = Join-Path $shimDir 'swmm5.cmd'
    @(
        '@echo off'
        "`"$target`" %*"
    ) | Set-Content -Path $shimPath -Encoding ASCII

    $env:Path = "$shimDir;$env:Path"
}

function Ensure-Swmm {
    if ($SkipSwmm) {
        return
    }
    if (Get-Command swmm5 -ErrorAction SilentlyContinue) {
        return
    }
    Ensure-Chocolatey
    Write-Step "Installing SWMM with Chocolatey"
    choco upgrade swmm -y --no-progress
    Refresh-ChocolateyEnvironment
    Ensure-SwmmShim
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
}

function Install-McpRequirements {
    Ensure-Node
    Get-ChildItem -Path (Join-Path $RepoRoot 'skills') -Filter package.json -Recurse |
        Where-Object { $_.FullName -like '*\scripts\mcp\package.json' } |
        Sort-Object FullName |
        ForEach-Object {
            $dir = Split-Path -Parent $_.FullName
            Write-Step "Installing MCP deps in $dir"
            if (Test-Path (Join-Path $dir 'package-lock.json')) {
                Push-Location $dir
                npm ci
                Pop-Location
            } else {
                Push-Location $dir
                npm install
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

Ensure-Admin
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
Write-Host "- Python setup: $(if ($SkipPython) { 'skipped (--skip-python)' } else { 'installed (.venv + scripts/requirements.txt)' })"
Write-Host "- MCP npm setup: $(if ($SkipMcp) { 'skipped (--skip-mcp)' } else { 'installed' })"
Write-Host "- SWMM check: $(Get-SwmmStatus)"
Write-Host ""
Write-Host "Next steps"
Write-Host "1. Activate the virtualenv: .\.venv\Scripts\Activate.ps1"
Write-Host "2. Run acceptance: python scripts/acceptance/run_acceptance.py --run-id latest"
Write-Host "3. Real-data smoke test: python scripts/real_cases/run_todcreek_minimal.py"
