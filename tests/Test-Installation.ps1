#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Version = 'latest',
    [ValidateRange(1, 65535)][int]$Port = 18080,
    [ValidateRange(1, 65535)][int]$DatabasePort = 15432,
    [ValidateRange(1, 65535)][int]$RedisPort = 16379,
    [string]$DockerDistribution = '',
    [switch]$KeepInstallation
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'lib\Sub2API.Windows.ps1')
$testRoot = Join-Path $env:TEMP ('Sub2API smoke ' + [Guid]::NewGuid().ToString('N'))
$context = $null
$composeArguments = $null
$url = "http://127.0.0.1:$Port"
Write-Host "Isolated live installation: $testRoot"
try {
    & (Join-Path $root 'install-sub2api.ps1') -InstallDir $testRoot -Version $Version -Port $Port -DatabasePort $DatabasePort -RedisPort $RedisPort -DockerDistribution $DockerDistribution -NoBrowser -NoShortcut
    $environment = Read-InstallEnvironment (Join-Path $testRoot '.env')
    $context = Get-DockerContext $environment.DOCKER_DISTRIBUTION $environment.DOCKER_BACKEND
    $composeArguments = @(Get-InstallComposeArguments $context $testRoot $environment)
    $configHash = (Get-FileHash -LiteralPath (Join-Path $testRoot '.env') -Algorithm SHA256).Hash
    $body = @{ email = $environment.ADMIN_EMAIL; password = $environment.ADMIN_PASSWORD } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Uri "$url/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $body
    if ($login.code -ne 0 -or $login.data.user.role -ne 'admin') { throw 'First administrator login failed.' }
    $settings = Invoke-RestMethod -Uri "$url/api/v1/settings/public"
    if ($settings.data.turnstile_enabled -or $settings.data.turnstile_site_key) { throw 'Cloudflare was enabled on a fresh installation.' }
    Write-Host 'PASS: Fresh installation completed without a setup wizard or Cloudflare token.'

    $marker = [Guid]::NewGuid().ToString('N')
    $sqlArguments = @('exec', '-T', 'postgres', 'psql', '-U', $environment.DATABASE_USER, '-d', $environment.DATABASE_DBNAME, '-v', 'ON_ERROR_STOP=1', '-At', '-c')
    $sql = "CREATE TABLE installer_smoke (marker text NOT NULL); INSERT INTO installer_smoke (marker) VALUES ('$marker');"
    $null = Invoke-InstallDocker $context ($composeArguments + $sqlArguments + @($sql))
    & (Join-Path $root 'install-sub2api.ps1') -InstallDir $testRoot -Version $Version -NoBrowser -NoShortcut
    if ((Get-FileHash -LiteralPath (Join-Path $testRoot '.env') -Algorithm SHA256).Hash -ne $configHash) { throw 'Reinstallation changed saved credentials.' }
    if (@(Get-Sub2APIProcess $testRoot).Count -ne 1) { throw 'Reinstallation started a duplicate application.' }
    Write-Host 'PASS: Reinstallation kept credentials and reused the running application.'

    & (Join-Path $testRoot 'stop-sub2api.ps1')
    $null = Invoke-InstallDocker $context ($composeArguments + @('down'))
    & (Join-Path $testRoot 'start-sub2api.ps1') -NoBrowser
    $login = Invoke-RestMethod -Uri "$url/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $body
    $savedMarker = Invoke-InstallDocker $context ($composeArguments + $sqlArguments + @('SELECT marker FROM installer_smoke;'))
    if ($login.code -ne 0 -or $login.data.user.role -ne 'admin' -or $savedMarker -ne $marker) {
        throw 'Administrator or database records were lost when the containers were recreated.'
    }
    if ((Invoke-RestMethod -Uri "$url/setup/status").data.needs_setup -ne $false) { throw 'A restart brought back the setup wizard.' }
    Write-Host 'PASS: Administrator and database records survived application and container recreation.'
    Write-Host 'All live installation checks passed.'
} finally {
    if ($KeepInstallation) {
        Write-Host "Kept test installation: $testRoot"
    } elseif (Test-Path -LiteralPath (Join-Path $testRoot '.env')) {
        & (Join-Path $root 'stop-sub2api.ps1') -InstallDir $testRoot
        if (-not $context) {
            $environment = Read-InstallEnvironment (Join-Path $testRoot '.env')
            $context = Get-DockerContext $environment.DOCKER_DISTRIBUTION $environment.DOCKER_BACKEND
            $composeArguments = @(Get-InstallComposeArguments $context $testRoot $environment)
        }
        # Only this test's unique project is removed, never the user's installation.
        $null = Invoke-InstallDocker $context ($composeArguments + @('down', '--volumes'))
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
