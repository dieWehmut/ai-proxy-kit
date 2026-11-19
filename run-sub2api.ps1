#Requires -Version 5.1
param([Parameter(Mandatory = $true)][string]$InstallDir, [Parameter(Mandatory = $true)][int]$Port)

$ErrorActionPreference = 'Stop'
$exitCode = 1
try {
    . (Join-Path $PSScriptRoot 'lib\Sub2API.Windows.ps1')
    $environment = Read-InstallEnvironment (Join-Path $InstallDir '.env')
    foreach ($key in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($key, $environment[$key], 'Process')
    }
    # Download authentication is not needed by the application process.
    Remove-Item Env:GH_TOKEN, Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    $env:DATA_DIR = $InstallDir
    $env:ZONEINFO = Join-Path $InstallDir 'zoneinfo.zip'
    $env:SERVER_HOST = '0.0.0.0'
    $env:SERVER_PORT = [string]$Port
    $env:GIN_MODE = 'release'
    $process = Start-Process -FilePath (Join-Path $InstallDir 'sub2api.exe') -WorkingDirectory $InstallDir -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $InstallDir 'logs\sub2api.log') -RedirectStandardError (Join-Path $InstallDir 'logs\sub2api-error.log')
    $process.WaitForExit()
    $exitCode = $process.ExitCode
} catch {
    [IO.File]::WriteAllText((Join-Path $InstallDir 'logs\launcher-error.log'), $_.Exception.Message)
} finally {
    if (Get-Variable process -ErrorAction SilentlyContinue) { $process.Dispose() }
}
exit $exitCode
