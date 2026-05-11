param(
    [string]$Ref = $env:AISWMM_INSTALL_REF,
    [string]$Provider = "openai",
    [string]$Model = "gpt-5.5",
    [switch]$SkipSwmm,
    [switch]$SkipMcp,
    [switch]$SkipSetup,
    [switch]$InstallSystemDeps
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($Ref)) {
    $Ref = 'main'
}

$repo = 'Zhonghao1995/agentic-swmm-workflow'
$url = "https://raw.githubusercontent.com/$repo/$Ref/scripts/bootstrap.ps1"
Write-Host "[INFO] Installing Agentic SWMM from $repo ($Ref)"

$script = (New-Object System.Net.WebClient).DownloadString($url)
$block = [scriptblock]::Create($script)

$args = @{
    Provider = $Provider
    Model = $Model
}
if ($SkipSwmm) { $args.SkipSwmm = $true }
if ($SkipMcp) { $args.SkipMcp = $true }
if ($SkipSetup) { $args.SkipSetup = $true }
if ($InstallSystemDeps) { $args.InstallSystemDeps = $true }

& $block @args
