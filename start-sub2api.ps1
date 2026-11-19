#Requires -Version 5.1
[CmdletBinding()]
param([string]$InstallDir = $PSScriptRoot, [ValidateRange(0, 65535)][int]$Port = 0, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Sub2API.Windows.ps1')
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$environment = Read-InstallEnvironment (Join-Path $InstallDir '.env')
if ($Port -eq 0) { $Port = [int]$environment.SERVER_PORT }
$exePath = [IO.Path]::GetFullPath((Join-Path $InstallDir 'sub2api.exe'))
if (-not (Test-Path -LiteralPath $exePath) -or -not (Test-Path -LiteralPath (Join-Path $InstallDir 'zoneinfo.zip'))) {
    throw 'The program or timezone data is missing. Rerun install-sub2api.ps1 from the installer source.'
}
$processes = @(Get-Sub2APIProcess $InstallDir)
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    if (-not $owner.Path -or [IO.Path]::GetFullPath($owner.Path) -ne $exePath) {
        throw "Port $Port belongs to another program. Choose a different -Port."
    }
}
if ($processes.Count -gt 0 -and $listeners.Count -eq 0) {
    throw 'This installation is running on another port or still starting. Wait, or use stop-sub2api.cmd first.'
}
$launcher = $null
if ($processes.Count -eq 0) {
    $context = Get-DockerContext $environment.DOCKER_DISTRIBUTION $environment.DOCKER_BACKEND
    $composeArguments = @(Get-InstallComposeArguments $context $InstallDir $environment)
    Write-Host 'Starting PostgreSQL and Redis; waiting for both health checks...'
    $null = Invoke-InstallDocker $context ($composeArguments + @('up', '--detach', '--wait', '--wait-timeout', '180'))
    $null = New-Item -ItemType Directory -Path (Join-Path $InstallDir 'logs') -Force
    $launcherFile = Join-Path $InstallDir 'run-sub2api.ps1'
    $launcherArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
        (ConvertTo-NativeArgument $launcherFile) + ' -InstallDir ' + (ConvertTo-NativeArgument $InstallDir) + " -Port $Port"
    # A dedicated launcher stays alive to own the redirected log streams after this script returns.
    $powershellPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launcher = Start-Process -FilePath $powershellPath -ArgumentList $launcherArguments -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru
}
$url = "http://127.0.0.1:$Port"
Wait-Sub2APIReady $url $launcher
Write-Host "Sub2API is ready: $url/login"
$adapters = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' })
$addresses = @($adapters | ForEach-Object { Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue } |
    Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress -Unique)
foreach ($address in $addresses) { Write-Host "LAN access: http://${address}:$Port/login" }
if (-not $NoBrowser) { Start-Process "$url/login" }
