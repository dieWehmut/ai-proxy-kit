#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
. (Join-Path $root 'lib\Sub2API.Windows.ps1')

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}
function Assert-Throws {
    param([scriptblock]$Action, [string]$Message)
    $thrown = $false
    try { & $Action } catch { $thrown = $true }
    Assert-True $thrown $Message
}

$tempRoot = Join-Path $env:TEMP ('Sub2API function tests ' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $tempRoot
try {
    foreach ($file in Get-ChildItem -LiteralPath $root -Filter *.ps1 -Recurse) {
        $tokens = $null; $parseErrors = $null
        $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
        Assert-True ($parseErrors.Count -eq 0) "PowerShell parser rejected $($file.Name)."
        Assert-True (-not @([IO.File]::ReadAllBytes($file.FullName) | Where-Object { $_ -gt 127 }).Count) "Non-ASCII script requires a BOM for Windows PowerShell: $($file.Name)."
    }
    Write-Host 'PASS: PowerShell syntax and Windows encoding compatibility.'

    $secrets = @(1..20 | ForEach-Object { New-InstallSecret })
    foreach ($secret in $secrets) { Assert-True ($secret -match '^[a-f0-9]{64}$') 'Generated credentials must be environment-safe and 256 bits long.' }
    Assert-True (@($secrets | Select-Object -Unique).Count -eq 20) 'Credential generation repeated a value.'
    Assert-True ((Get-InstallProjectName 'C:\Apps\Sub2API') -eq (Get-InstallProjectName 'c:\apps\sub2api\')) 'Windows path aliases must share a project.'
    Assert-True ((Get-InstallProjectName 'C:\Apps\Sub2API') -ne (Get-InstallProjectName 'C:\Apps\Other')) 'Independent installations must not share volumes.'
    Write-Host 'PASS: Independent credentials and stable, isolated Docker projects.'

    $fixtureLines = @('INSTALLER_FORMAT_VERSION=1', 'COMPOSE_PROJECT_NAME=sub2api-test', 'DOCKER_BACKEND=WSL', 'DOCKER_DISTRIBUTION=Ubuntu-22.04', 'SERVER_PORT=18080',
        'DATABASE_PORT=15432', 'DATABASE_USER=sub2api', 'DATABASE_DBNAME=sub2api', 'DATABASE_PASSWORD=test-only',
        'REDIS_PORT=16379', 'REDIS_PASSWORD=test-only', 'ADMIN_EMAIL=test@example.com', 'ADMIN_PASSWORD=test-only',
        'JWT_SECRET=test-only', 'TOTP_ENCRYPTION_KEY=test-only')
    $fixturePath = Join-Path $tempRoot 'fixture.env'
    [IO.File]::WriteAllLines($fixturePath, $fixtureLines)
    $read = Read-InstallEnvironment $fixturePath
    Assert-True ($read.ADMIN_EMAIL -eq 'test@example.com') 'The environment reader lost an administrator value.'
    [IO.File]::WriteAllLines($fixturePath, ($fixtureLines | Where-Object { $_ -notlike 'DOCKER_DISTRIBUTION=*' }))
    Assert-Throws { Read-InstallEnvironment $fixturePath | Out-Null } 'A saved WSL installation must not switch to another engine.'
    [IO.File]::WriteAllLines($fixturePath, $fixtureLines)
    [IO.File]::AppendAllText($fixturePath, "ADMIN_EMAIL=duplicate@example.com`r`n")
    Assert-Throws { Read-InstallEnvironment $fixturePath | Out-Null } 'Ambiguous configuration must be rejected.'
    [IO.File]::WriteAllText($fixturePath, 'AUTO_SETUP=true')
    Assert-Throws { Read-InstallEnvironment $fixturePath | Out-Null } 'Unmanaged installations must be rejected.'
    Write-Host 'PASS: Malformed, unmanaged, and ambiguous Docker configuration is not adopted.'

    $privatePath = Join-Path $tempRoot 'private.txt'
    Write-PrivateText $privatePath 'test-only private data'
    $acl = Get-Acl -LiteralPath $privatePath
    Assert-True $acl.AreAccessRulesProtected 'Private file permissions still inherit public access.'
    Write-Host 'PASS: Generated credential files have protected Windows ACLs.'

    $dataPath = Join-Path $tempRoot 'checksum.bin'
    [IO.File]::WriteAllText($dataPath, 'verified fixture')
    $checksum = (Get-FileHash -LiteralPath $dataPath -Algorithm SHA256).Hash
    Assert-FileChecksum $dataPath $checksum
    [IO.File]::AppendAllText($dataPath, 'corrupted')
    Assert-Throws { Assert-FileChecksum $dataPath $checksum } 'Corrupt downloads must not be accepted.'
    Write-Host 'PASS: Corrupt downloads fail SHA-256 verification.'

    $release = [pscustomobject]@{ assets = @(
        [pscustomobject]@{ name = 'sub2api_0.2.23_windows_amd64.zip' },
        [pscustomobject]@{ name = 'sub2api_0.2.23_linux_arm64.tar.gz' }) }
    Assert-True ((Get-WindowsReleaseAsset $release 'amd64').name -eq 'sub2api_0.2.23_windows_amd64.zip') 'A non-Windows artifact was selected.'
    Assert-Throws { Get-WindowsReleaseAsset $release 'arm64' | Out-Null } 'An unavailable explicitly selected architecture must fail.'
    Write-Host 'PASS: Release selection uses the requested Windows architecture.'

    $probePath = Join-Path $tempRoot 'argument probe.ps1'
    [IO.File]::WriteAllText($probePath, 'param([string]$Text,[AllowEmptyString()][string]$Second); [pscustomobject]@{Text=$Text;Second=$Second} | ConvertTo-Json -Compress')
    $powershellPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($value in @('C:\folder with spaces\', 'embedded "quote" and \ slash', 'literal $() & | text')) {
        $result = Invoke-ExternalCommand $powershellPath @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $probePath, '-Text', $value, '-Second', '') 30
        Assert-True ($result.ExitCode -eq 0) 'The native command argument probe failed.'
        $echoed = $result.Output | ConvertFrom-Json
        Assert-True ($echoed.Text -eq $value -and $echoed.Second -eq '') 'A native argument was changed or interpreted.'
    }
    Write-Host 'PASS: Native arguments preserve spaces, quotes, backslashes, and literal shell text.'
} finally { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
Write-Host 'All function checks passed.'
