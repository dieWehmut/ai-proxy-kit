#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Source = '',
    [string]$Distribution = '',
    [string]$AuthDir = '',
    [string]$BaseUrl = '',
    [string]$EnvFile = ''
)
$ErrorActionPreference = 'Stop'
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wsl)) { throw 'Install WSL first.' }

function ConvertTo-WslPath {
    param(
        [string]$Path,
        [string]$Description,
        [string[]]$WslArguments
    )
    $converted = & $wsl @WslArguments --exec wslpath -u $Path
    if ($LASTEXITCODE -ne 0) { throw "Cannot translate $Description into WSL." }
    return ([string]$converted).Trim()
}

if (-not $Source) {
    $downloads = Get-ItemPropertyValue -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name '{374DE290-123F-4565-9164-39C4925E467B}' -ErrorAction SilentlyContinue
    if (-not $downloads) { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
    $Source = Join-Path ([Environment]::ExpandEnvironmentVariables($downloads)) 'ai-proxy-kit-google-session.json'
}
if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
    throw 'Google-only session export not found. Export ai-proxy-kit-google-session.json to Downloads or pass its local JSON path.'
}
$Source = (Resolve-Path -LiteralPath $Source).ProviderPath
$arguments = @()
if ($PSScriptRoot -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
    if ($Distribution -and $Distribution -ne $Matches[1]) { throw 'The repository belongs to a different WSL distribution.' }
    $Distribution = $Matches[1]
    $linuxRoot = '/' + $Matches[2].Replace('\', '/')
} else {
    if ($Distribution) { $arguments = @('-d', $Distribution) }
    $linuxRoot = ConvertTo-WslPath -Path $PSScriptRoot -Description 'repository path' -WslArguments $arguments
}
if ($Distribution) { $arguments = @('-d', $Distribution) }
$linuxSource = ConvertTo-WslPath -Path $Source -Description 'session export path' -WslArguments $arguments
$pythonArguments = @("$linuxRoot/scripts/import-aistudio-session.py", $linuxSource)
if ($PSBoundParameters.ContainsKey('AuthDir')) {
    $linuxAuthDir = ConvertTo-WslPath -Path $AuthDir -Description 'authentication directory path' -WslArguments $arguments
    $pythonArguments += @('--auth-dir', $linuxAuthDir)
}
if ($PSBoundParameters.ContainsKey('BaseUrl')) {
    $pythonArguments += @('--base-url', $BaseUrl)
}
if ($PSBoundParameters.ContainsKey('EnvFile')) {
    $linuxEnvFile = ConvertTo-WslPath -Path $EnvFile -Description 'environment file path' -WslArguments $arguments
    $pythonArguments += @('--env-file', $linuxEnvFile)
}
& $wsl @arguments --exec python3 @pythonArguments
exit $LASTEXITCODE
