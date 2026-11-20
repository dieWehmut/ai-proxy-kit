#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('install', 'start', 'stop', 'status', 'check')][string]$Action = 'start',
    [ValidateSet('all', 'antigravity', 'workbuddy', 'sub2api', 'aistudio')][string]$Service = 'all',
    [string]$Distribution = '',
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Options
)
$ErrorActionPreference = 'Stop'
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wsl)) { throw 'Install WSL and enable a Linux Docker Engine first.' }
$arguments = @()
if ($PSScriptRoot -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
    if ($Distribution -and $Distribution -ne $Matches[1]) { throw 'The repository belongs to a different WSL distribution.' }
    $Distribution = $Matches[1]
    $linuxRoot = '/' + $Matches[2].Replace('\', '/')
} else {
    if ($Distribution) { $arguments = @('-d', $Distribution) }
    $linuxRoot = (& $wsl @arguments --exec wslpath -u $PSScriptRoot)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot translate repository path into WSL.' }
    $linuxRoot = $linuxRoot.Trim()
}
if ($Distribution) { $arguments = @('-d', $Distribution) }
Write-Host "AI Proxy Kit: $Action $Service (Linux/WSL; gateways bind 0.0.0.0)"
& $wsl @arguments --exec bash "$linuxRoot/$Action.sh" $Service @Options
exit $LASTEXITCODE
