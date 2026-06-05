param(
    [string]$TargetDir = "agentic-swmm-workflow",
    [string]$Provider = "openai",
    [string]$Model = "gpt-5.5"
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
        throw "Run bootstrap.ps1 from an elevated PowerShell session."
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

# Only escalate to admin + Chocolatey when git is actually missing. A user who
# already has git (the common case) gets a non-admin one-click install, which
# matches the documented design — Administrator is only needed for the explicit
# -InstallSystemDeps Chocolatey path. The bash bootstrap is likewise non-admin.
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Ensure-Admin
    Ensure-Chocolatey
    Refresh-ChocolateyEnvironment
    Write-Step "Installing Git"
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

& (Join-Path $fullTarget 'scripts\install.ps1') -Yes -Provider $Provider -Model $Model
