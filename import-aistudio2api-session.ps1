#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Source = '',
    [string]$Distribution = '',
    [string]$DataDir = '',
    [string]$Container = 'aistudio2api-local'
)
$ErrorActionPreference = 'Stop'
$wsl = Join-Path $env:WINDIR 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wsl)) { throw 'Install WSL first.' }
if (-not $Container) { throw 'Container cannot be empty.' }
if ($Container -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') {
    throw 'Container name contains unsupported characters.'
}

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
    throw 'Google storage-state export not found. Export ai-proxy-kit-google-session.json to Downloads or pass its local JSON path.'
}
$Source = (Resolve-Path -LiteralPath $Source).ProviderPath

$arguments = @()
if ($PSScriptRoot -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
    if ($Distribution -and $Distribution -ne $Matches[1]) { throw 'The repository belongs to a different WSL distribution.' }
    $Distribution = $Matches[1]
} else {
    if ($Distribution) { $arguments = @('-d', $Distribution) }
}
if ($Distribution) { $arguments = @('-d', $Distribution) }

if (-not $DataDir) { $DataDir = Join-Path $PSScriptRoot 'runtime\aistudio2api' }
$null = New-Item -ItemType Directory -Path $DataDir -Force
$DataDir = (Resolve-Path -LiteralPath $DataDir).ProviderPath
$sessionDestination = Join-Path $DataDir 'session.json'
if (-not [string]::Equals($Source, $sessionDestination, [StringComparison]::OrdinalIgnoreCase)) {
    Copy-Item -LiteralPath $Source -Destination $sessionDestination -Force
}

$linuxDataDir = ConvertTo-WslPath -Path $DataDir -Description 'session data directory' -WslArguments $arguments
$linuxSessionPath = $linuxDataDir.TrimEnd('/') + '/session.json'
& $wsl @arguments --exec chmod 600 $linuxSessionPath
if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict session file permissions in WSL.' }

# The documented docker run command mounts this directory at /data.
$containerSessionPath = '/data/session.json'
& $wsl @arguments -- docker exec $Container aistudio2api setup --storage-state $containerSessionPath
exit $LASTEXITCODE
