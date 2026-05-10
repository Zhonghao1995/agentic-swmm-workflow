param(
    [string]$TargetDir = "agentic-swmm-workflow",
    [switch]$SkipPython,
    [switch]$SkipMcp,
    [switch]$SkipSwmm,
    [switch]$InstallSystemDeps,
    [string]$SwmmExe,
    [string]$SwmmVersion = "5.2.4"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Ensure-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required to install system dependencies with Chocolatey. Re-run from an elevated PowerShell session, or clone the repository and run scripts\install.ps1 -Yes -SkipSwmm for user-space setup."
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

$currentDir = Get-Location
$localInstaller = Join-Path $currentDir 'scripts\install.ps1'
$installArgs = @{ Yes = $true }
if ($SkipPython) { $installArgs.SkipPython = $true }
if ($SkipMcp) { $installArgs.SkipMcp = $true }
if ($SkipSwmm) { $installArgs.SkipSwmm = $true }
if ($InstallSystemDeps) { $installArgs.InstallSystemDeps = $true }
if ($SwmmExe) { $installArgs.SwmmExe = $SwmmExe }
if ($SwmmVersion) { $installArgs.SwmmVersion = $SwmmVersion }

if (Test-Path $localInstaller) {
    Write-Step "Using existing checkout in $currentDir"
    & $localInstaller @installArgs
    exit 0
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Ensure-Chocolatey
    Refresh-ChocolateyEnvironment
    Write-Step "Installing Git"
    Ensure-Admin
    choco upgrade git -y --no-progress
    Refresh-ChocolateyEnvironment
}

$repoUrl = 'https://github.com/Zhonghao1995/agentic-swmm-workflow.git'
$fullTarget = Join-Path (Get-Location) $TargetDir

if (Test-Path (Join-Path $fullTarget '.git')) {
    Write-Step "Updating existing checkout in $fullTarget"
    git -C $fullTarget pull --ff-only
} else {
    Write-Step "Cloning repository into $fullTarget"
    git clone $repoUrl $fullTarget
}

& (Join-Path $fullTarget 'scripts\install.ps1') @installArgs
