param(
    [string]$Ref = $env:AISWMM_INSTALL_REF
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = 'Zhonghao1995/agentic-swmm-workflow'

# Default to the latest published release for a reproducible install. Set
# AISWMM_INSTALL_REF to pin a tag (e.g. 'v0.7.2') or 'main' to track development.
if ([string]::IsNullOrWhiteSpace($Ref)) {
    try {
        $rel = Invoke-RestMethod -UseBasicParsing `
            -Headers @{ 'User-Agent' = 'aiswmm-installer' } `
            -Uri "https://api.github.com/repos/$repo/releases/latest"
        $Ref = $rel.tag_name
    } catch {
        $Ref = 'main'
    }
    if ([string]::IsNullOrWhiteSpace($Ref)) { $Ref = 'main' }
}

$url = "https://raw.githubusercontent.com/$repo/$Ref/scripts/bootstrap.ps1"
Write-Host "[INFO] Installing Agentic SWMM from $repo ($Ref)"
Write-Host "[INFO] You'll pick your AI provider (OpenAI or Claude) and model after install."

$scriptText = (New-Object System.Net.WebClient).DownloadString($url)
$block = [scriptblock]::Create($scriptText)

# bootstrap.ps1 clones $Ref and runs scripts/install.ps1. Pass -Ref only when the
# fetched bootstrap actually declares it: older release tags predate that param,
# and binding an unknown parameter would crash the one-liner.
if ($scriptText -match '(?m)^\s*\[string\]\$Ref') {
    & $block -Ref $Ref
} else {
    & $block
}
