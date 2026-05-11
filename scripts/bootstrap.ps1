param(
    [string]$TargetDir = "",
    [string]$SourceRef = $env:AISWMM_INSTALL_REF,
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

if ([string]::IsNullOrWhiteSpace($SourceRef)) {
    $SourceRef = 'main'
}

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

function Get-DefaultTargetDir {
    $base = $env:LOCALAPPDATA
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = Join-Path $HOME '.aiswmm'
    } else {
        $base = Join-Path $base 'AgenticSWMM'
    }
    return Join-Path $base 'agentic-swmm-workflow'
}

function Resolve-TargetDir {
    if ([string]::IsNullOrWhiteSpace($TargetDir)) {
        return Get-DefaultTargetDir
    }
    if ([System.IO.Path]::IsPathRooted($TargetDir)) {
        return $TargetDir
    }
    return (Join-Path (Get-Location) $TargetDir)
}

function Install-RepositoryZip {
    param([string]$Destination)

    $zipUrl = "https://codeload.github.com/Zhonghao1995/agentic-swmm-workflow/zip/$SourceRef"
    $tmpRoot = Join-Path $env:TEMP "aiswmm-bootstrap-$([guid]::NewGuid().ToString('N'))"
    $zipPath = Join-Path $tmpRoot 'source.zip'
    $extractRoot = Join-Path $tmpRoot 'extract'

    Write-Step "Downloading repository archive from $zipUrl"
    New-Item -ItemType Directory -Force -Path $tmpRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force

    $sourceDir = Get-ChildItem -Path $extractRoot -Directory | Select-Object -First 1
    if (-not $sourceDir) {
        throw "Unable to find extracted repository directory in $extractRoot"
    }

    if (Test-Path $Destination) {
        Remove-Item -Recurse -Force $Destination
    }
    Move-Item -Path $sourceDir.FullName -Destination $Destination
    Remove-Item -Recurse -Force $tmpRoot
}

$currentDir = Get-Location
$localInstaller = Join-Path $currentDir 'scripts\install.ps1'
$installArgs = @{ Yes = $true }
if ($SkipPython) { $installArgs.SkipPython = $true }
if ($SkipMcp) { $installArgs.SkipMcp = $true }
if ($SkipSwmm) { $installArgs.SkipSwmm = $true }
if ($SkipSetup) { $installArgs.SkipSetup = $true }
if ($InstallSystemDeps) { $installArgs.InstallSystemDeps = $true }
if ($SwmmExe) { $installArgs.SwmmExe = $SwmmExe }
if ($Provider) { $installArgs.Provider = $Provider }
if ($Model) { $installArgs.Model = $Model }
if ($SwmmVersion) { $installArgs.SwmmVersion = $SwmmVersion }

if (Test-Path $localInstaller) {
    Write-Step "Using existing checkout in $currentDir"
    & $localInstaller @installArgs
    exit 0
}

$repoUrl = 'https://github.com/Zhonghao1995/agentic-swmm-workflow.git'
$fullTarget = Resolve-TargetDir

if (Test-Path (Join-Path $fullTarget '.git')) {
    Write-Step "Updating existing checkout in $fullTarget"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        git -C $fullTarget pull --ff-only
    } else {
        Write-Step "Git is unavailable; refreshing checkout from archive instead"
        Install-RepositoryZip -Destination $fullTarget
    }
} else {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Step "Cloning repository into $fullTarget"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $fullTarget) | Out-Null
        git clone $repoUrl $fullTarget
    } else {
        Write-Step "Git is unavailable; using a GitHub source archive"
        Install-RepositoryZip -Destination $fullTarget
    }
}

& (Join-Path $fullTarget 'scripts\install.ps1') @installArgs
