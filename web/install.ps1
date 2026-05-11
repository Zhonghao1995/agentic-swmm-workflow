param(
    [string]$Ref = $env:AISWMM_INSTALL_REF,
    [string]$Provider = "openai",
    [string]$Model = $env:AISWMM_MODEL,
    [switch]$SkipSwmm,
    [switch]$SkipMcp,
    [switch]$SkipSetup,
    [switch]$InstallSystemDeps,
    [string]$SwmmExe,
    [string]$SwmmVersion = "5.2.4"
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($Ref)) {
    $Ref = 'main'
}
if ([string]::IsNullOrWhiteSpace($Model)) {
    Write-Host ""
    Write-Host "Choose the OpenAI model for Agentic SWMM:"
    Write-Host "  1) gpt-5.5  strongest default"
    Write-Host "  2) gpt-5.4  balanced cost/performance"
    Write-Host "  3) gpt-5.2  older compatible frontier model"
    Write-Host ""
    $choice = Read-Host "Select model [1]"
    switch ($choice) {
        "2" { $Model = "gpt-5.4" }
        "3" { $Model = "gpt-5.2" }
        "gpt-5.4" { $Model = "gpt-5.4" }
        "gpt-5.2" { $Model = "gpt-5.2" }
        "gpt-5.5" { $Model = "gpt-5.5" }
        default { $Model = "gpt-5.5" }
    }
}

$repo = 'Zhonghao1995/agentic-swmm-workflow'
$url = "https://raw.githubusercontent.com/$repo/$Ref/scripts/bootstrap.ps1"
Write-Host "[INFO] Installing Agentic SWMM from $repo ($Ref)"
Write-Host "[INFO] OpenAI model: $Model"

$script = (New-Object System.Net.WebClient).DownloadString($url)
$block = [scriptblock]::Create($script)

$args = @{
    Provider = $Provider
    Model = $Model
    SourceRef = $Ref
}
if ($SkipSwmm) { $args.SkipSwmm = $true }
if ($SkipMcp) { $args.SkipMcp = $true }
if ($SkipSetup) { $args.SkipSetup = $true }
if ($InstallSystemDeps) { $args.InstallSystemDeps = $true }
if ($SwmmExe) { $args.SwmmExe = $SwmmExe }
if ($SwmmVersion) { $args.SwmmVersion = $SwmmVersion }

& $block @args
