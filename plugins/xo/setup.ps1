<#
.SYNOPSIS
    XO setup launcher for Windows: finds node and runs setup.js (all logic lives there).
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File <plugin-dir>\setup.ps1 install
.EXAMPLE
    .\setup.ps1 install --target workspace --workspace C:\src\myproject
#>
$ErrorActionPreference = 'Stop'

$node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($node) { $nodeExe = $node.Source }
elseif (Test-Path (Join-Path $env:ProgramFiles 'nodejs\node.exe')) { $nodeExe = Join-Path $env:ProgramFiles 'nodejs\node.exe' }
else {
    Write-Host '  [fail] node not found. XO setup and hooks require Node.js:'
    Write-Host '           Windows: winget install --id OpenJS.NodeJS.LTS -e'
    Write-Host '           any:     https://nodejs.org'
    exit 1
}

# Arguments travel as JSON in the environment: Windows PowerShell 5.1 splits a
# native-command argument such as --xocortex=C:\v a at the space.
$env:XO_SETUP_ARGV_JSON = ConvertTo-Json -InputObject @($args | ForEach-Object { [string]$_ }) -Compress
try { & $nodeExe (Join-Path $PSScriptRoot 'setup.js') }
finally { Remove-Item Env:XO_SETUP_ARGV_JSON -ErrorAction SilentlyContinue }
exit $LASTEXITCODE
