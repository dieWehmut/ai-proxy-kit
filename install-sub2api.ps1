#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Sub2API'),
    [ValidateRange(1, 65535)][int]$Port = 8080,
    [ValidateRange(1, 65535)][int]$DatabasePort = 5432,
    [ValidateRange(1, 65535)][int]$RedisPort = 6379,
    [ValidatePattern('^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')]
    [string]$AdminEmail = 'admin@sub2api.local',
    [ValidatePattern('^(latest|v?[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]+)?)$')]
    [string]$Version = 'latest',
    [ValidateSet('auto', 'amd64', 'arm64')][string]$Architecture = 'auto',
    [string]$DockerDistribution = '',
    [switch]$NoBrowser,
    [switch]$NoShortcut
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:OS -ne 'Windows_NT') { throw 'Run this installer on Windows.' }
. (Join-Path $PSScriptRoot 'lib\Sub2API.Windows.ps1')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if (@($Port, $DatabasePort, $RedisPort | Select-Object -Unique).Count -ne 3) {
    throw 'The web, PostgreSQL, and Redis ports must be different.'
}

Write-Host "Installing Sub2API in $InstallDir"
$context = Get-DockerContext $DockerDistribution
$null = Invoke-InstallDocker $context @('compose', 'version') 30
$envPath = Join-Path $InstallDir '.env'
$newConfiguration = -not (Test-Path -LiteralPath $envPath)
if ($newConfiguration) {
    if ((Test-Path -LiteralPath $InstallDir) -and @(Get-ChildItem -LiteralPath $InstallDir -Force).Count -gt 0) {
        throw 'The installation directory is not empty. Choose another -InstallDir; existing installations are never overwritten.'
    }
    foreach ($servicePort in @($Port, $DatabasePort, $RedisPort)) {
        if (@(Get-NetTCPConnection -State Listen -LocalPort $servicePort -ErrorAction SilentlyContinue).Count -gt 0) {
            throw "Port $servicePort is in use. Choose -Port, -DatabasePort, and -RedisPort explicitly."
        }
    }
    $projectName = Get-InstallProjectName $InstallDir
    $oldVolumes = Invoke-InstallDocker $context @('volume', 'ls', '--filter', "label=com.docker.compose.project=$projectName", '--format', '{{.Name}}') 30
    if ($oldVolumes) { throw 'Persistent volumes already exist for this directory, but its .env is missing. Restore the original .env instead of generating new passwords.' }
    $null = New-Item -ItemType Directory -Path $InstallDir -Force
    $adminPassword = New-InstallSecret
    $environmentLines = @(
        'INSTALLER_FORMAT_VERSION=1',
        "COMPOSE_PROJECT_NAME=$projectName",
        "DOCKER_BACKEND=$($context.Kind)",
        "DOCKER_DISTRIBUTION=$($context.Distribution)",
        'AUTO_SETUP=true',
        'SERVER_HOST=0.0.0.0', "SERVER_PORT=$Port", 'SERVER_MODE=release',
        'DATABASE_HOST=127.0.0.1', "DATABASE_PORT=$DatabasePort", 'DATABASE_USER=sub2api',
        "DATABASE_PASSWORD=$(New-InstallSecret)", 'DATABASE_DBNAME=sub2api', 'DATABASE_SSLMODE=disable',
        'DATABASE_MAX_OPEN_CONNS=32', 'DATABASE_MAX_IDLE_CONNS=16',
        'REDIS_HOST=127.0.0.1', "REDIS_PORT=$RedisPort", "REDIS_PASSWORD=$(New-InstallSecret)",
        'REDIS_DB=0', 'REDIS_POOL_SIZE=32', 'REDIS_MIN_IDLE_CONNS=8', 'REDIS_ENABLE_TLS=false',
        "ADMIN_EMAIL=$AdminEmail", "ADMIN_PASSWORD=$adminPassword", "JWT_SECRET=$(New-InstallSecret)",
        'JWT_EXPIRE_HOUR=24', "TOTP_ENCRYPTION_KEY=$(New-InstallSecret)",
        'TURNSTILE_REQUIRED=false', 'SETUP_MIGRATION_TIMEOUT_SECONDS=600', 'TZ=Asia/Shanghai'
    )
    Write-PrivateText $envPath (($environmentLines -join "`r`n") + "`r`n")
    $loginText = "Sub2API administrator`r`nURL: http://127.0.0.1:$Port/login`r`nEmail: $AdminEmail`r`nPassword: $adminPassword`r`n`r`nKeep this file private. Change the administrator password after signing in.`r`n"
    Write-PrivateText (Join-Path $InstallDir 'LOGIN.txt') $loginText
} else {
    $existingEnvironment = Read-InstallEnvironment $envPath
    foreach ($pair in @(@('Port', 'SERVER_PORT'), @('DatabasePort', 'DATABASE_PORT'), @('RedisPort', 'REDIS_PORT'))) {
        if ($PSBoundParameters.ContainsKey($pair[0]) -and [int]$PSBoundParameters[$pair[0]] -ne [int]$existingEnvironment[$pair[1]]) {
            throw 'This directory is already configured with different ports. Reuse its saved ports or choose a new -InstallDir.'
        }
    }
    if ($PSBoundParameters.ContainsKey('AdminEmail') -and $AdminEmail -ne $existingEnvironment.ADMIN_EMAIL) {
        throw 'Reinstallation does not replace an existing administrator.'
    }
    Write-Host 'Reusing the saved credentials and persistent data.'
}
$environment = Read-InstallEnvironment $envPath
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'LOGIN.txt'))) {
    Write-PrivateText (Join-Path $InstallDir 'LOGIN.txt') "Sub2API administrator`r`nURL: http://127.0.0.1:$($environment.SERVER_PORT)/login`r`nEmail: $($environment.ADMIN_EMAIL)`r`nPassword: $($environment.ADMIN_PASSWORD)`r`n"
}
$needsBootstrap = -not (Test-Path -LiteralPath (Join-Path $InstallDir '.installed'))
foreach ($directory in @('lib', 'services', 'logs', 'downloads')) {
    $null = New-Item -ItemType Directory -Path (Join-Path $InstallDir $directory) -Force
}
foreach ($file in @('start-sub2api.ps1', 'start-sub2api.cmd', 'stop-sub2api.ps1', 'stop-sub2api.cmd',
    'run-sub2api.ps1', 'lib\Sub2API.Windows.ps1', 'services\docker-compose.yml')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $InstallDir $file) -Force
}

$downloads = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'assets\downloads.json') -Raw | ConvertFrom-Json
$headers = @{ 'User-Agent' = 'sub2api-windows-installer'; Accept = 'application/vnd.github+json' }
if ($env:GITHUB_TOKEN) { $headers.Authorization = "Bearer $env:GITHUB_TOKEN" }
elseif ($env:GH_TOKEN) { $headers.Authorization = "Bearer $env:GH_TOKEN" }
$exePath = Join-Path $InstallDir 'sub2api.exe'
$releasePath = Join-Path $InstallDir '.release.json'
if (Test-Path -LiteralPath $exePath) {
    if (-not (Test-Path -LiteralPath $releasePath)) { throw 'An unmanaged sub2api.exe is already present.' }
    $installedRelease = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
    if ($Version -ne 'latest' -and ('v' + $Version.TrimStart('v')) -ne $installedRelease.tag) {
        throw 'Changing an installed version requires a separate, backed-up upgrade. This installer only creates or resumes installations.'
    }
    Assert-FileChecksum $exePath $installedRelease.binarySha256
    Write-Host "Reusing Sub2API $($installedRelease.tag)."
} else {
    $release = Get-UpstreamRelease $downloads.upstreamRepository $Version $headers
    $asset = Get-WindowsReleaseAsset $release $Architecture
    $sha256 = if ($asset.PSObject.Properties['digest'] -and $asset.digest -match '^sha256:([a-fA-F0-9]{64})$') { $Matches[1] } else {
        $checksumAssets = @($release.assets | Where-Object { $_.name -eq 'checksums.txt' })
        if ($checksumAssets.Count -ne 1) { throw 'Upstream did not supply a checksum for its Windows archive.' }
        $checksumText = ConvertFrom-WebContent (Invoke-WebRequest -UseBasicParsing -Uri $checksumAssets[0].browser_download_url -TimeoutSec 30).Content
        $checksumLines = @($checksumText.Split("`n") | Where-Object { $_.TrimEnd() -match ('^[a-fA-F0-9]{64}\s+\*?' + [Regex]::Escape($asset.name) + '$') })
        if ($checksumLines.Count -ne 1) { throw 'The Windows archive checksum is missing or ambiguous.' }
        $checksumLines[0].Substring(0, 64)
    }
    $archivePath = Join-Path $InstallDir ('downloads\' + $asset.name)
    Write-Host "Downloading and verifying Sub2API $($release.tag_name)..."
    # Do not send a GitHub API token to release/CDN downloads.
    Save-VerifiedDownload $asset.browser_download_url $archivePath $sha256
    $extractPath = Join-Path $InstallDir ('downloads\extract-' + [Guid]::NewGuid().ToString('N'))
    try {
        Expand-Archive -LiteralPath $archivePath -DestinationPath $extractPath
        if (-not (Test-Path -LiteralPath (Join-Path $extractPath 'sub2api.exe'))) { throw 'The verified ZIP does not contain sub2api.exe.' }
        Copy-Item -LiteralPath (Join-Path $extractPath 'sub2api.exe') -Destination $exePath
        Copy-Item -LiteralPath (Join-Path $extractPath 'LICENSE') -Destination (Join-Path $InstallDir 'SUB2API-LICENSE')
        $metadata = @{ tag = $release.tag_name; asset = $asset.name; archiveSha256 = $sha256;
            binarySha256 = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash.ToLowerInvariant() }
        [IO.File]::WriteAllText($releasePath, ($metadata | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
    } finally { Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue }
}
Save-VerifiedDownload $downloads.zoneInfoUrl (Join-Path $InstallDir 'zoneinfo.zip') $downloads.zoneInfoSha256

& (Join-Path $InstallDir 'start-sub2api.ps1') -NoBrowser
$url = "http://127.0.0.1:$($environment.SERVER_PORT)"
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir '.installed')) -or -not (Test-Path -LiteralPath (Join-Path $InstallDir 'config.yaml'))) {
    throw 'Automatic setup did not write its configuration and installation marker.'
}
if ($needsBootstrap) {
    $body = @{ email = $environment.ADMIN_EMAIL; password = $environment.ADMIN_PASSWORD } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Uri "$url/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 15
    if ($login.code -ne 0 -or $login.data.user.role -ne 'admin' -or -not $login.data.access_token) {
        throw 'The automatically created administrator could not sign in.'
    }
    Write-Host 'Administrator login verified. No setup wizard or captcha was needed.'
}
if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Sub2API.lnk'))
    $shortcut.TargetPath = Join-Path $InstallDir 'start-sub2api.cmd'
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.Description = 'Start Sub2API and open its login page'
    $shortcut.Save()
}
Write-Host "Installation complete: $url/login"
Write-Host "Administrator credentials: $(Join-Path $InstallDir 'LOGIN.txt')"
Write-Host 'Sub2API runs in the background. Use stop-sub2api.cmd to stop it.'
if (-not $NoBrowser) { Start-Process "$url/login" }
