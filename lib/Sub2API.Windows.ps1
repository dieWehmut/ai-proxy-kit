# Shared by the installer and the installed launchers. Compatible with PowerShell 5.1.
Set-StrictMode -Version 2.0

function New-InstallSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Get-InstallProjectName {
    param([Parameter(Mandatory = $true)][string]$InstallDir)
    $path = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/').ToLowerInvariant()
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $digest = $hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($path)) }
    finally { $hash.Dispose() }
    return 'sub2api-' + ([BitConverter]::ToString($digest)).Replace('-', '').Substring(0, 12).ToLowerInvariant()
}

function Read-InstallEnvironment {
    param([Parameter(Mandatory = $true)][string]$Path)
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) { continue }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') { throw "Invalid environment file: $Path" }
        if ($values.ContainsKey($Matches[1])) { throw "Duplicate environment setting: $($Matches[1])" }
        $values[$Matches[1]] = $Matches[2]
    }
    if ($values.INSTALLER_FORMAT_VERSION -ne '1') {
        throw 'This directory belongs to another installation. Choose an empty -InstallDir.'
    }
    foreach ($key in @('COMPOSE_PROJECT_NAME', 'DOCKER_BACKEND', 'SERVER_PORT', 'DATABASE_PORT', 'DATABASE_USER',
        'DATABASE_DBNAME', 'DATABASE_PASSWORD', 'REDIS_PORT', 'REDIS_PASSWORD', 'ADMIN_EMAIL',
        'ADMIN_PASSWORD', 'JWT_SECRET', 'TOTP_ENCRYPTION_KEY')) {
        if (-not $values[$key]) { throw "Missing installation setting: $key" }
    }
    if ($values.COMPOSE_PROJECT_NAME -notmatch '^[a-z0-9][a-z0-9_-]+$') { throw 'Invalid Docker project name.' }
    if ($values.DOCKER_BACKEND -notin @('Native', 'WSL')) { throw 'Invalid saved Docker backend.' }
    if ($values.DOCKER_BACKEND -eq 'WSL' -and -not $values.DOCKER_DISTRIBUTION) { throw 'The saved WSL distribution is missing.' }
    if ($values.DOCKER_BACKEND -eq 'Native' -and $values.DOCKER_DISTRIBUTION) { throw 'A native Docker installation cannot select a WSL distribution.' }
    return $values
}

function Write-PrivateText {
    param([string]$Path, [string]$Content)
    [IO.File]::WriteAllText($Path, $Content, (New-Object Text.UTF8Encoding $false))
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $identities = @([Security.Principal.WindowsIdentity]::GetCurrent().User,
        (New-Object Security.Principal.SecurityIdentifier 'S-1-5-18'),
        (New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'))
    foreach ($identity in $identities) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    # Windows command-line parsing doubles backslashes before quotes and a closing quote.
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-ExternalCommand {
    param([string]$FilePath, [string[]]$CommandArguments, [int]$TimeoutSeconds = 600)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = (@($CommandArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $outputTask = $process.StandardOutput.ReadToEndAsync()
        $errorTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            throw "Command timed out: $([IO.Path]::GetFileName($FilePath))"
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = $outputTask.GetAwaiter().GetResult()
            ErrorOutput = $errorTask.GetAwaiter().GetResult()
        }
    } finally { $process.Dispose() }
}

function Get-DockerContext {
    param([string]$PreferredDistribution = '', [ValidateSet('Auto', 'Native', 'WSL')][string]$PreferredBackend = 'Auto')
    if ($PreferredDistribution) { $PreferredBackend = 'WSL' }
    $nativeCommand = Get-Command docker.exe -CommandType Application -ErrorAction SilentlyContinue
    $nativePath = if ($nativeCommand) { $nativeCommand.Source } else {
        Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'
    }
    if ($PreferredBackend -ne 'WSL' -and (Test-Path -LiteralPath $nativePath)) {
        $result = Invoke-ExternalCommand $nativePath @('info', '--format', '{{.OSType}}') 20
        if ($result.ExitCode -eq 0 -and $result.Output.Trim() -eq 'linux') {
            return [pscustomobject]@{ Kind = 'Native'; FilePath = $nativePath; Distribution = '' }
        }
    }
    $wslPath = Join-Path $env:WINDIR 'System32\wsl.exe'
    if ($PreferredBackend -ne 'Native' -and (Test-Path -LiteralPath $wslPath)) {
        $distributions = @($PreferredDistribution)
        if (-not $PreferredDistribution) {
            $listing = Invoke-ExternalCommand $wslPath @('--list', '--quiet') 20
            if ($listing.ExitCode -eq 0) {
                $distributions = @($listing.Output.Replace([string][char]0, '').Split("`n") |
                    ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notlike 'docker-desktop*' })
            }
        }
        foreach ($distribution in $distributions) {
            if (-not $distribution) { continue }
            $result = Invoke-ExternalCommand $wslPath @('-d', $distribution, '--exec', 'docker', 'info', '--format', '{{.OSType}}') 30
            if ($result.ExitCode -eq 0 -and $result.Output.Trim() -eq 'linux') {
                return [pscustomobject]@{ Kind = 'WSL'; FilePath = $wslPath; Distribution = $distribution }
            }
        }
    }
    $desktopPath = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if ($PreferredBackend -ne 'WSL' -and (Test-Path -LiteralPath $desktopPath) -and (Test-Path -LiteralPath $nativePath)) {
        Write-Host 'Starting Docker Desktop. Waiting for its Linux engine...'
        $null = Start-Process -FilePath $desktopPath -PassThru
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while ($timer.Elapsed.TotalSeconds -lt 180) {
            $result = Invoke-ExternalCommand $nativePath @('info', '--format', '{{.OSType}}') 10
            if ($result.ExitCode -eq 0 -and $result.Output.Trim() -eq 'linux') {
                return [pscustomobject]@{ Kind = 'Native'; FilePath = $nativePath; Distribution = '' }
            }
            Start-Sleep -Seconds 3
        }
    }
    throw 'Docker Linux engine not available. Start Docker Desktop, or install Docker + Compose v2 in WSL, then rerun. Use -DockerDistribution to select WSL explicitly.'
}

function Invoke-InstallDocker {
    param($Context, [string[]]$CommandArguments, [int]$TimeoutSeconds = 600)
    if ($Context.Kind -eq 'WSL') {
        $CommandArguments = @('-d', $Context.Distribution, '--exec', 'docker') + $CommandArguments
    }
    $result = Invoke-ExternalCommand $Context.FilePath $CommandArguments $TimeoutSeconds
    if ($result.ExitCode -ne 0) { throw "Docker command failed ($($result.ExitCode)): $($result.ErrorOutput.Trim())" }
    return $result.Output.Trim()
}

function ConvertTo-DockerPath {
    param($Context, [string]$Path)
    if ($Context.Kind -eq 'Native') { return [IO.Path]::GetFullPath($Path) }
    $result = Invoke-ExternalCommand $Context.FilePath @('-d', $Context.Distribution, '--exec', 'wslpath', '-u', $Path) 20
    if ($result.ExitCode -ne 0) { throw 'Could not convert the installation path for WSL.' }
    return $result.Output.Trim()
}

function Get-InstallComposeArguments {
    param($Context, [string]$InstallDir, [hashtable]$Environment)
    return @('compose', '--project-name', $Environment.COMPOSE_PROJECT_NAME,
        '--env-file', (ConvertTo-DockerPath $Context (Join-Path $InstallDir '.env')),
        '--file', (ConvertTo-DockerPath $Context (Join-Path $InstallDir 'services\docker-compose.yml')))
}

function Assert-FileChecksum {
    param([string]$Path, [string]$Sha256)
    if ($Sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'A valid SHA-256 checksum is required.' }
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Sha256) {
        throw "SHA-256 verification failed: $([IO.Path]::GetFileName($Path))"
    }
}

function Save-VerifiedDownload {
    param([string]$Url, [string]$Path, [string]$Sha256, [hashtable]$Headers = @{})
    if (([Uri]$Url).Scheme -ne 'https') { throw 'Downloads must use HTTPS.' }
    if (Test-Path -LiteralPath $Path) {
        try { Assert-FileChecksum $Path $Sha256; return } catch { Remove-Item -LiteralPath $Path }
    }
    $partialPath = $Path + '.' + [Guid]::NewGuid().ToString('N') + '.partial'
    try {
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $Url -Headers $Headers -OutFile $partialPath -TimeoutSec 300
                Assert-FileChecksum $partialPath $Sha256
                Move-Item -LiteralPath $partialPath -Destination $Path -Force
                return
            } catch {
                if ($attempt -eq 3) { throw }
                Start-Sleep -Seconds 2
            }
        }
    } finally { Remove-Item -LiteralPath $partialPath -Force -ErrorAction SilentlyContinue }
}

function Get-WindowsReleaseAsset {
    param($Release, [ValidateSet('auto', 'amd64', 'arm64')][string]$Architecture = 'auto')
    $selectedArchitecture = $Architecture
    if ($selectedArchitecture -eq 'auto') {
        $selectedArchitecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
        if ($selectedArchitecture -eq 'arm64' -and -not @($Release.assets | Where-Object { $_.name -match '_windows_arm64\.zip$' }).Count) {
            Write-Host 'Upstream supplies amd64 only; Windows on ARM will use x64 emulation.'
            $selectedArchitecture = 'amd64'
        }
    }
    $assets = @($Release.assets | Where-Object { $_.name -match ('^sub2api_[A-Za-z0-9.+_-]+_windows_' + $selectedArchitecture + '\.zip$') })
    if ($assets.Count -ne 1) { throw "No unique Windows $selectedArchitecture archive in this release." }
    return $assets[0]
}

function ConvertFrom-WebContent {
    param($Content)
    if ($Content -is [byte[]]) { return [Text.Encoding]::UTF8.GetString($Content) }
    return [string]$Content
}

function Get-UpstreamRelease {
    param([string]$Repository, [string]$Version, [hashtable]$Headers)
    $releaseRef = if ($Version -eq 'latest') { 'latest' } else { 'tags/v' + $Version.TrimStart('v') }
    try {
        return Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository/releases/$releaseRef" -Headers $Headers -TimeoutSec 30
    } catch {
        Write-Host 'GitHub API unavailable. Using the official release page and checksums instead.'
    }
    $tag = 'v' + $Version.TrimStart('v')
    if ($Version -eq 'latest') {
        $request = [Net.HttpWebRequest]::Create("https://github.com/$Repository/releases/latest")
        $request.Method = 'HEAD'
        $request.AllowAutoRedirect = $false
        $request.Timeout = 30000
        $request.UserAgent = 'sub2api-windows-installer'
        $response = $request.GetResponse()
        try {
            $location = $response.Headers['Location']
            $pattern = '^https://github\.com/' + [Regex]::Escape($Repository) + '/releases/tag/(v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)$'
            if ($location -notmatch $pattern) { throw 'The official latest-release redirect was not recognized.' }
            $tag = $Matches[1]
        } finally { $response.Close() }
    }
    $baseUrl = "https://github.com/$Repository/releases/download/$tag"
    $checksumText = ConvertFrom-WebContent (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/checksums.txt" -TimeoutSec 60).Content
    $assets = @()
    foreach ($line in $checksumText.Split("`n")) {
        if ($line.Trim() -match '^([a-fA-F0-9]{64})\s+\*?(sub2api_[A-Za-z0-9.+_-]+_windows_(?:amd64|arm64)\.zip)$') {
            $assets += [pscustomobject]@{ name = $Matches[2]; digest = 'sha256:' + $Matches[1]; browser_download_url = "$baseUrl/$($Matches[2])" }
        }
    }
    if ($assets.Count -eq 0) { throw 'Official checksums did not contain a supported Windows archive.' }
    return [pscustomobject]@{ tag_name = $tag; assets = $assets }
}

function Get-Sub2APIProcess {
    param([string]$InstallDir)
    $expectedPath = [IO.Path]::GetFullPath((Join-Path $InstallDir 'sub2api.exe'))
    return @(Get-Process -Name sub2api -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [IO.Path]::GetFullPath($_.Path) -eq $expectedPath
    })
}

function Wait-Sub2APIReady {
    param([string]$Url, $Launcher, [int]$TimeoutSeconds = 300)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if ($Launcher -and $Launcher.HasExited) { throw 'Sub2API exited during startup. Check logs\launcher-error.log and logs\sub2api-error.log.' }
        try {
            $health = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 3
            $setup = Invoke-RestMethod -Uri "$Url/setup/status" -TimeoutSec 3
            $page = Invoke-WebRequest -UseBasicParsing -Uri "$Url/login" -TimeoutSec 3
            if ($health.status -eq 'ok' -and $setup.code -eq 0 -and $setup.data.needs_setup -eq $false -and
                $page.StatusCode -eq 200 -and $page.Content -match '<html[\s>]') { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    throw "Sub2API did not complete automatic setup at $Url. Check the logs folder."
}
