#Requires -Version 5.1
[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Sub2API.Windows.ps1')
foreach ($process in @(Get-Sub2APIProcess $InstallDir)) {
    Stop-Process -Id $process.Id -ErrorAction Stop
    $process.WaitForExit()
}
Write-Host 'Sub2API stopped. PostgreSQL, Redis, and their persistent data were kept.'
