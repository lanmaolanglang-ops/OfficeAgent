$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root 'vendor'
$installer = Join-Path $vendor 'MicrosoftEdgeWebView2RuntimeInstaller.exe'
$url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'

New-Item -ItemType Directory -Force -Path $vendor | Out-Null
if (-not (Test-Path $installer) -or (Get-Item $installer).Length -lt 1000000) {
  Write-Host 'Downloading Microsoft Edge WebView2 Evergreen Standalone Installer...'
  Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
}
if ((Get-Item $installer).Length -lt 1000000) {
  throw "WebView2 installer is missing or incomplete: $installer"
}
Write-Host "WebView2 installer ready: $installer"
